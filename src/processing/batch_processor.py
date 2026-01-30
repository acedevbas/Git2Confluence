"""
Batch Processor for Multi-Project Cache Warming and Documentation.

This module orchestrates batch operations across multiple GitLab projects,
designed for nightly cron execution.

Key Features:
- Multi-project parallel processing
- Incremental cache warming (only new MRs)
- Smart folder download (skip full archive)
- Fallback to archive for old commits
- Progress tracking and error handling

Usage:
    processor = BatchProcessor.from_config("projects.yaml")
    results = await processor.warm_cache_all()
    
Architecture:
    - Uses AsyncGitLabClient for optimized GitLab operations
    - Uses SpecLoader for OpenAPI spec resolution
    - Uses DiskCacheManager for persistent caching
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import yaml

from src.gitlab.client import (
    AsyncGitLabClient,
    ClientConfig,
    FolderNotFoundError,
    SwaggerConfig,
)
from src.openapi.spec_loader import SpecLoader
from src.openapi.spec_loader import SpecLoader
from src.cache.disk_cache import DiskCacheManager
from deepdiff import DeepDiff
from src.cache.endpoint_history_cache import HistoryEvent
from src.processing.origin_detection import OriginMRResolver, OriginDetectionConfig

EXCLUDE_COSMETIC_FIELDS = [
    r"root\['info'\]",
    r"root\['servers'\]",
    r"root\['externalDocs'\]",
    r"root\['components'\]\['securitySchemes'\]",
    r"root\['[^']+'\]\['description'\]",
    r"root\['[^']+'\]\['summary'\]",
    r"root\['[^']+'\]\['tags'\]",
    r"root\['[^']+'\]\['operationId'\]",
    r"root\['components'\]\['schemas'\]\['[^']+'\]\['description'\]",
    r"root\['components'\]\['schemas'\]\['[^']+'\]\['example'\]",
    r"root\['components'\]\['schemas'\]\['[^']+'\]\['title'\]",
]

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration Dataclasses
# =============================================================================

@dataclass
class ConfluenceConfig:
    """Confluence publishing configuration."""
    space: str
    parent_page_id: str
    token: Optional[str] = None


@dataclass
class ProjectConfig:
    """
    Configuration for a single project.
    
    Loaded from projects.yaml with environment variable substitution.
    """
    name: str
    path: str
    gitlab_token: str
    gitlab_url: str = "https://gitlab.example.com"
    gitlab_ssl_verify: bool = False
    target_branch: str = "master"
    swagger: SwaggerConfig = field(default_factory=SwaggerConfig)
    confluence: Optional[ConfluenceConfig] = None
    
    @classmethod
    def from_dict(
        cls,
        data: Dict[str, Any],
        defaults: Dict[str, Any],
    ) -> ProjectConfig:
        """
        Create ProjectConfig from dictionary with defaults.
        
        Supports environment variable substitution: ${VAR_NAME}
        """
        def substitute_env(value: Any) -> Any:
            """Recursively substitute environment variables."""
            if isinstance(value, str):
                # Match ${VAR_NAME} pattern
                pattern = r'\$\{([^}]+)\}'
                matches = re.findall(pattern, value)
                for var_name in matches:
                    env_value = os.environ.get(var_name, '')
                    value = value.replace(f'${{{var_name}}}', env_value)
                return value
            elif isinstance(value, dict):
                return {k: substitute_env(v) for k, v in value.items()}
            elif isinstance(value, list):
                return [substitute_env(v) for v in value]
            return value
        
        # Merge project data with defaults
        merged = {**defaults, **data}
        merged = substitute_env(merged)
        
        # Parse swagger config
        swagger_data = merged.get('swagger', defaults.get('swagger', {}))
        swagger = SwaggerConfig.from_dict(swagger_data)
        
        # Parse confluence config
        confluence = None
        if 'confluence' in merged:
            conf_data = merged['confluence']
            confluence = ConfluenceConfig(
                space=conf_data.get('space', ''),
                parent_page_id=str(conf_data.get('parent_page_id', '')),
                token=conf_data.get('token'),
            )
        
        return cls(
            name=merged.get('name', merged.get('path', 'Unknown')),
            path=merged['path'],
            gitlab_token=merged.get('gitlab_token', ''),
            gitlab_url=merged.get('gitlab_url', 'https://gitlab.example.com'),
            gitlab_ssl_verify=merged.get('gitlab_ssl_verify', False),
            target_branch=merged.get('target_branch', 'master'),
            swagger=swagger,
            confluence=confluence,
        )


# =============================================================================
# Result Dataclasses
# =============================================================================

@dataclass
class ProcessingResult:
    """Result of processing a single project."""
    project_name: str
    project_path: str
    mrs_found: int = 0
    specs_cached: int = 0
    specs_skipped: int = 0
    specs_failed: int = 0
    method_folder: int = 0
    method_archive: int = 0
    history_events: int = 0
    endpoints_processed: int = 0
    duration: float = 0.0

    errors: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate as percentage."""
        total = self.specs_cached + self.specs_skipped + self.specs_failed
        if total == 0:
            return 100.0
        return (self.specs_cached + self.specs_skipped) / total * 100


@dataclass
class BatchResult:
    """Result of batch processing across all projects."""
    project_results: List[ProcessingResult] = field(default_factory=list)
    total_duration_seconds: float = 0.0
    
    @property
    def total_mrs(self) -> int:
        return sum(r.mrs_found for r in self.project_results)
    
    @property
    def total_cached(self) -> int:
        return sum(r.specs_cached for r in self.project_results)
    
    @property
    def total_errors(self) -> int:
        return sum(len(r.errors) for r in self.project_results)


# =============================================================================
# Batch Processor
# =============================================================================

class BatchProcessor:
    """
    Orchestrates batch processing across multiple GitLab projects.
    
    Designed for nightly cron execution with:
    - Incremental cache warming (only new MRs)
    - Multi-project parallel processing
    - Smart folder download optimization
    - Progress logging and error handling
    
    Usage:
        processor = BatchProcessor.from_config("projects.yaml")
        
        # Warm cache for all projects
        result = await processor.warm_cache_all()
        
        # Or for specific project
        result = await processor.warm_cache_project("group/project")
    """
    
    def __init__(
        self,
        projects: List[ProjectConfig],
        cache: Optional[DiskCacheManager] = None,
        max_concurrent_projects: int = 3,
    ):
        """
        Initialize batch processor.
        
        Args:
            projects: List of project configurations
            cache: Cache manager (creates default if None)
            max_concurrent_projects: Max projects to process in parallel
        """
        self.projects = {p.path: p for p in projects}
        self.cache = cache or DiskCacheManager()
        self.spec_loader = SpecLoader()
        self.max_concurrent_projects = max_concurrent_projects
        
        # Initialize Origin Resolver with default config
        # Initialize Origin Resolver with default config
        self.origin_resolver = OriginMRResolver.create_default(
            self._download_spec_adapter
        )
    
    @classmethod
    def from_config(cls, config_path: str = "projects.yaml") -> BatchProcessor:
        """
        Create BatchProcessor from YAML configuration file.
        
        Args:
            config_path: Path to projects.yaml
        """
        config_path = Path(config_path)
        if not config_path.exists():
            logger.warning(f"Config file not found: {config_path}")
            return cls(projects=[])
        
        with open(config_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        
        defaults = data.get('defaults', {})
        projects_data = data.get('projects', [])
        
        projects = [
            ProjectConfig.from_dict(p, defaults)
            for p in projects_data
        ]
        
        logger.info(f"Loaded {len(projects)} projects from {config_path}")
        return cls(projects=projects)
    
    # =========================================================================
    # Cache Warming
    # =========================================================================
    
    async def warm_cache_all(
        self,
        incremental: bool = True,
        mr_limit: Optional[int] = None,
        with_history: bool = True,
    ) -> BatchResult:
        """
        Warm cache for all configured projects.
        
        Args:
            incremental: Only fetch MRs since last run
            mr_limit: Maximum MRs to process per project (None for unlimited)
            with_history: Whether to pre-compute endpoint history (default True)
            
        Returns:
            BatchResult with per-project results
        """
        start_time = datetime.now()
        
        # Process projects with limited concurrency
        semaphore = asyncio.Semaphore(self.max_concurrent_projects)
        
        async def process_project(config: ProjectConfig) -> ProcessingResult:
            async with semaphore:
                if with_history:
                    return await self.warm_cache_with_history(
                        config.path,
                        incremental=incremental,
                        mr_limit=mr_limit,
                    )
                else:
                    return await self.warm_cache_project(
                        config.path,
                        incremental=incremental,
                        mr_limit=mr_limit,
                    )
        
        tasks = [process_project(p) for p in self.projects.values()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Convert exceptions to error results
        project_results = []
        for result, config in zip(results, self.projects.values()):
            if isinstance(result, Exception):
                project_results.append(ProcessingResult(
                    project_name=config.name,
                    project_path=config.path,
                    errors=[str(result)],
                ))
            else:
                project_results.append(result)
        
        total_duration = (datetime.now() - start_time).total_seconds()
        
        return BatchResult(
            project_results=project_results,
            total_duration_seconds=total_duration,
        )
    
    async def warm_cache_project(
        self,
        project_path: str,
        incremental: bool = True,
        mr_limit: Optional[int] = None,
    ) -> ProcessingResult:
        """
        Warm cache for a single project.
        
        Args:
            project_path: GitLab project path
            incremental: Only fetch MRs since last run
            mr_limit: Maximum MRs to process (None for unlimited)
        """
        config = self.projects.get(project_path)
        if not config:
            return ProcessingResult(
                project_name="Unknown",
                project_path=project_path,
                errors=[f"Project not found in config: {project_path}"],
            )
        
        start_time = datetime.now()
        result = ProcessingResult(
            project_name=config.name,
            project_path=project_path,
        )
        
        # Get last processed date for incremental warming
        since_date = None
        if incremental:
            since_date = self._get_last_mr_date(project_path)
            if since_date:
                logger.info(f"[{config.name}] Incremental mode: since {since_date}")
        
        try:
            client_config = ClientConfig(
                base_url=config.gitlab_url,
                token=config.gitlab_token,
                ssl_verify=config.gitlab_ssl_verify,
            )
            
            async with AsyncGitLabClient(client_config) as client:
                # Fetch MRs
                mrs = await client.get_merged_mrs(
                    project_path,
                    limit=mr_limit,
                    since_date=since_date,
                    target_branch=config.target_branch,
                )
                result.mrs_found = len(mrs)
                
                if not mrs:
                    logger.info(f"[{config.name}] No new MRs to process")
                    return result
                
                logger.info(f"[{config.name}] Processing {len(mrs)} MRs")
                
                # Process each MR
                for i, mr in enumerate(mrs, 1):
                    # Use original MR SHA (not merge_commit_sha) for accurate attribution
                    # when multiple MRs are merged through release branches
                    sha = mr.get('sha')  # last commit in MR branch before merge
                    mr_iid = mr.get('iid', '?')
                    
                    if not sha:
                        continue
                    
                    # Check cache
                    if self.cache.has_spec(project_path, sha):
                        result.specs_skipped += 1
                        continue
                    
                    # Process MR
                    try:
                        spec, method = await self._process_single_mr(
                            client, config, sha
                        )
                        
                        if spec:
                            self.cache.set_spec(project_path, sha, spec)
                            result.specs_cached += 1
                            if method == "folder":
                                result.method_folder += 1
                            else:
                                result.method_archive += 1
                        else:
                            result.specs_failed += 1
                            
                    except Exception as e:
                        result.specs_failed += 1
                        result.errors.append(f"MR !{mr_iid}: {e}")
                    
                    # Progress logging every 10 MRs
                    if i % 10 == 0:
                        logger.info(
                            f"[{config.name}] Progress: {i}/{len(mrs)} "
                            f"(cached: {result.specs_cached}, skipped: {result.specs_skipped})"
                        )
                
                # Update last MR date
                if mrs:
                    latest_date = max(
                        mr.get('merged_at', '')[:10]
                        for mr in mrs
                        if mr.get('merged_at')
                    )
                    self._set_last_mr_date(project_path, latest_date)
        
        except Exception as e:
            result.errors.append(f"Project error: {e}")
            logger.exception(f"[{config.name}] Cache warming failed")
        
        result.duration_seconds = (datetime.now() - start_time).total_seconds()
        
        logger.info(
            f"[{config.name}] Complete: "
            f"{result.specs_cached} cached, {result.specs_skipped} skipped, "
            f"{result.specs_failed} failed in {result.duration_seconds:.1f}s "
            f"(folder: {result.method_folder}, archive: {result.method_archive})"
        )
        
        return result
    
    async def _process_single_mr(
        self,
        client: AsyncGitLabClient,
        config: ProjectConfig,
        sha: str,
    ) -> tuple[Optional[Dict[str, Any]], str]:
        """
        Process single MR with optimized folder download.
        
        Returns:
            Tuple of (spec_dict, method_used) where method is "folder" or "archive"
        """
        # Try 1: Optimized folder download (~1MB)
        try:
            files = await client.download_swagger_folder(
                config.path,
                sha,
                config.swagger,
            )
            spec = self.spec_loader.load_spec_from_files(
                files,
                config.swagger.entry_files,
            )
            if spec:
                return (spec, "folder")
        except FolderNotFoundError:
            logger.debug(f"Folder not found at {sha[:8]}, trying archive")
        except Exception as e:
            logger.warning(f"Folder download failed for {sha[:8]}: {e}")
        
        # Try 2: Fallback to archive (~100MB)
        try:
            archive = await client.download_archive(config.path, sha)
            spec = await asyncio.to_thread(
                self._process_archive, archive
            )
            if spec:
                return (spec, "archive")
        except Exception as e:
            logger.warning(f"Archive fallback failed for {sha[:8]}: {e}")
        
        return (None, "none")
    
    def _process_archive(self, archive_bytes: bytes) -> Optional[Dict[str, Any]]:
        """Process archive and extract spec (sync, for thread pool)."""
        import tempfile
        import zipfile
        
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = os.path.join(tmpdir, "archive.zip")
            with open(archive_path, 'wb') as f:
                f.write(archive_bytes)
            
            # Extract
            try:
                with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                    zip_ref.extractall(tmpdir)
            except zipfile.BadZipFile:
                return None
            
            # Load using existing method
            return self.spec_loader.load_spec_from_snapshot(tmpdir)
    
    # =========================================================================
    # State Tracking
    # =========================================================================
    
    def _get_last_mr_date(self, project_path: str) -> Optional[str]:
        """Get last processed MR date for incremental warming."""
        key = f"__last_mr_date__{self.cache._project_hash(project_path)}"
        return self.cache._cache.get(key)
    
    def _set_last_mr_date(self, project_path: str, date: str) -> None:
        """Store last processed MR date."""
        key = f"__last_mr_date__{self.cache._project_hash(project_path)}"
        self.cache._cache.set(key, date)
    
    # =========================================================================
    # Cache Warming with History Pre-computation
    # =========================================================================
    
    async def warm_cache_with_history(
        self,
        project_path: str,
        incremental: bool = False,
        mr_limit: Optional[int] = None,
    ) -> ProcessingResult:
        """
        Warm cache AND pre-compute endpoint history.
        
        This is the optimized version that:
        1. Downloads specs for each MR
        2. Extracts ALL endpoints from each spec
        3. Compares with previous versions
        4. Stores history events in cache
        
        Documentation generation becomes O(1) cache read!
        
        Args:
            project_path: GitLab project path
            incremental: Only fetch MRs since last run (default False for history rebuild)
            mr_limit: Maximum MRs to process (None for unlimited)
        """
        from src.openapi.schema_extractor import SchemaExtractor
        from src.cache.endpoint_history_cache import EndpointHistoryCache, HistoryBuilder
        from config import settings
        
        config = self.projects.get(project_path)
        if not config:
            return ProcessingResult(
                project_name="Unknown",
                project_path=project_path,
                errors=[f"Project not found in config: {project_path}"],
            )
        
        start_time = datetime.now()
        result = ProcessingResult(
            project_name=config.name,
            project_path=project_path,
        )
        
        # Get last processed date for incremental
        since_date = None
        if incremental:
            since_date = self._get_last_mr_date(project_path)
            if since_date:
                logger.info(f"[{config.name}] Incremental mode: since {since_date}")
        else:
            logger.info(f"[{config.name}] Full history rebuild mode")
        
        try:
            client_config = ClientConfig(
                base_url=config.gitlab_url,
                token=config.gitlab_token,
                ssl_verify=config.gitlab_ssl_verify,
            )
            
            # Initialize history cache
            history_cache = EndpointHistoryCache(self.cache)
            
            # Clear existing history for full rebuild
            if not incremental:
                cleared = history_cache.clear_project(project_path)
                logger.info(f"[{config.name}] Cleared {cleared} history entries")
            

            
            async with AsyncGitLabClient(client_config) as client:
                # Fetch MRs
                mrs = await client.get_merged_mrs(
                    project_path,
                    limit=mr_limit,
                    since_date=since_date,
                    target_branch=config.target_branch,
                )
                result.mrs_found = len(mrs)
                
                if not mrs:
                    logger.info(f"[{config.name}] No MRs to process")
                    return result
                
                # Sort MRs chronologically for correct history
                mrs_sorted = sorted(mrs, key=lambda x: x.get('merged_at', ''))
                
                logger.info(f"[{config.name}] Processing {len(mrs_sorted)} MRs with history")
                
                # OPTIMIZATION: Deduplicate MRs by merge_commit_sha
                # If multiple MRs result in the same commit (e.g. feature + release wrapper),
                # pick the "Release" one to ensure correct origin detection context.
                unique_mrs = []
                mrs_by_sha = {}
                
                for mr in mrs_sorted:
                    sha = mr.get('merge_commit_sha')
                    # Fallback if no merge commit
                    if not sha:
                         # Attempt to get it (slow, but needed for grouping)
                         # We'll skip complex lookups here to avoid N+1, 
                         # usually merged_mrs response has sha if we use new enough API
                         # For now, treat unknown SHAs as unique
                         unique_mrs.append(mr)
                         continue
                    
                    if sha not in mrs_by_sha:
                        mrs_by_sha[sha] = []
                    mrs_by_sha[sha].append(mr)
                
                # Now build the filtered list in chronological order of SHAs
                # We need to preserve the order of SHAs from mrs_sorted
                seen_shas = set()
                final_mrs = []
                
                for mr in mrs_sorted:
                    sha = mr.get('merge_commit_sha')
                    if not sha:
                        final_mrs.append(mr)
                        continue
                        
                    if sha in seen_shas:
                        continue
                    seen_shas.add(sha)
                    
                    # Pick best MR for this SHA
                    group = mrs_by_sha[sha]
                    selected_mr = group[0]
                    
                    # Priority: Title contains "Release" > "Merge" > last one
                    if len(group) > 1:
                        # Check for Release
                        release_mr = next((m for m in group if "refresh" in m.get('title', '').lower() or "release" in m.get('title', '').lower()), None)
                        if release_mr:
                            selected_mr = release_mr
                            logger.info(f"[{config.name}] SHA {sha[:8]}: Chose Release MR !{selected_mr['iid']} over {[m['iid'] for m in group if m != selected_mr]}")
                        else:
                            # Use the last one (closest to list end likely implies 'container' or latest intent?)
                            # Or usually the one with highest ID is the wrapper?
                            # Let's verify: !238 (Release) > !214 (Feature). Higher IID is better.
                            selected_mr = max(group, key=lambda x: x.get('iid', 0))
                            
                    final_mrs.append(selected_mr)
                
                mrs_sorted = final_mrs
                logger.info(f"[{config.name}] Deduplicated to {len(mrs_sorted)} unique commits")

                # Track stats
                endpoints_found = set()
                history_events_created = 0
                seen_changes = set() # (task_id, diff_hash) deduplication
                
                # Track last event type per endpoint for REVERT detection
                endpoint_last_event = {}  # key -> last_event_type
                
                # MERGE COMMIT TIMELINE: Track previous merge commit for correct sequential comparison
                # This compares actual master states, not individual branch diffs
                previous_merge_sha: Optional[str] = None
                previous_endpoints: Dict[str, Dict] = {}
                
                # Process each MR using precise diff logic
                for i, mr in enumerate(mrs_sorted, 1):
                    mr_iid = mr.get('iid')
                    merge_commit_sha = mr.get('merge_commit_sha')
                    
                    if i % 10 == 0:
                        logger.info(f"[{config.name}] Scanning MR {i}/{len(mrs_sorted)} (!{mr_iid})...")
                    
                    try:
                        # 1. Check if MR touched swagger files
                        try:
                            changed = await client.get_mr_changed_files(project_path, mr_iid)
                            if not any('swagger' in f.lower() or 'openapi' in f.lower() for f in changed):
                                continue
                        except Exception as e:
                            logger.warning(f"Failed to check changes for MR !{mr_iid}: {e}")
                            continue

                        # 2. Get current master state at merge point (merge_commit_sha)
                        if not merge_commit_sha:
                            # Fallback to head_sha if merge_commit_sha not available
                            _, merge_commit_sha = await client.get_mr_diff_shas(project_path, mr_iid)
                            if not merge_commit_sha:
                                continue
                        
                        # 3. Download spec at merge commit (actual master state after MR merged)
                        spec_current = await self._get_or_download_spec(client, config, merge_commit_sha)
                        
                        if not spec_current:
                            continue
                            
                        result.specs_cached += 1
                        
                        # 4. Extract endpoints from current state
                        endpoints_current = SchemaExtractor.extract_all(spec_current) if spec_current else {}
                        
                        if endpoints_current:
                            endpoints_found.update(endpoints_current.keys())
                        
                        # 5. Get "before" state - either from previous merge commit or MR's own base
                        if previous_endpoints:
                            # Use previous MR's endpoints as baseline (correct timeline)
                            endpoints_before = previous_endpoints
                        else:
                            # First MR - get its own base for comparison
                            base_sha, _ = await client.get_mr_diff_shas(project_path, mr_iid)
                            if base_sha:
                                spec_before = await self._get_or_download_spec(client, config, base_sha)
                                endpoints_before = SchemaExtractor.extract_all(spec_before) if spec_before else {}
                            else:
                                endpoints_before = {}
                        
                        # 6. Compare endpoints
                        all_keys = set(endpoints_before.keys()) | set(endpoints_current.keys())
                        
                        for key in all_keys:
                            event_type = None
                            diff = None
                            
                            before = endpoints_before.get(key)
                            after = endpoints_current.get(key)
                            
                            if before is None and after:
                                event_type = "CREATED"
                            elif before and not after:
                                event_type = "DELETED"
                            elif before and after:
                                # Compare sequential master states
                                dd = DeepDiff(
                                    before, 
                                    after, 
                                    ignore_order=True
                                )
                                if dd:
                                    event_type = "MODIFIED"
                                    diff = dd.to_dict()
                            
                            if event_type:
                                # FOUND CHANGE!
                                # 6. Recursive Origin Lookup if needed
                                # Parse endpoint for deep inspection
                                ep_parts = key.split(" ", 1)
                                ep_method = ep_parts[0].lower()
                                ep_path = ep_parts[1] if len(ep_parts) > 1 else ""

                                origin_mr = await self._find_origin_mr(
                                    client, 
                                    project_path, 
                                    mr, 
                                    "swagger",
                                    endpoint_key=key,
                                    method=ep_method,
                                    path=ep_path
                                )
                                
                                final_mr = origin_mr if origin_mr else mr
                                final_task_id = self._extract_task_id(
                                    final_mr.get('title', ''), 
                                    final_mr.get('source_branch', '')
                                )
                                
                                # Fallback to Parent MR if Origin didn't have a task ID
                                if final_task_id == 'NO-TASK' and origin_mr:
                                    final_task_id = self._extract_task_id(
                                        mr.get('title', ''), 
                                        mr.get('source_branch', '')
                                    )
                                
                                # REVERT detection: pure state-based logic
                                # If endpoint was DELETED and now CREATED again = REVERT
                                if (event_type == "CREATED" and 
                                    endpoint_last_event.get(key) == "DELETED"):
                                    event_type = "REVERT"
                                    logger.info(f"[REVERT] State transition DELETED→CREATED for {key} in MR !{mr_iid}")
                                
                                
                                # Deduplication logic
                                diff_hash = ""
                                if diff:
                                    # Use custom default handler for DeepDiff types (SetOrdered etc)
                                    # Ensure sets are sorted for stable hashing
                                    def _json_default(o):
                                        if isinstance(o, (set, tuple)) or 'SetOrdered' in o.__class__.__name__:
                                            try:
                                                return sorted(list(o), key=str)
                                            except:
                                                return list(o)
                                        return str(o)

                                    diff_hash = hashlib.md5(
                                        json.dumps(
                                            diff, 
                                            sort_keys=True,
                                            default=_json_default
                                        ).encode()
                                    ).hexdigest()
                                
                                # Include endpoint key and MR IID in signature to prevent collisions
                                # We allow same changes if they are in different MRs (e.g. revert and re-apply)
                                event_signature = f"{key}:{final_task_id}:{diff_hash}:{mr_iid}"
                                if event_signature in seen_changes:
                                    logger.info(f"[DEDUP] Skipping duplicate event {event_signature} in MR !{mr_iid}")
                                    continue
                                
                                logger.info(f"[DEDUP] Adding new event {event_signature} from MR !{mr_iid} (Origin !{final_mr['iid']})")
                                seen_changes.add(event_signature)
                                
                                # Create Event
                                event = HistoryEvent(
                                    event_type=event_type,
                                    task_id=final_task_id,
                                    mr_iid=mr_iid,  # Use current MR where change was detected
                                    author=mr.get('author', {}).get('name', 'Unknown') 
                                        if isinstance(mr.get('author'), dict) else 'Unknown',
                                    merged_at=mr.get('merged_at', ''),
                                    commit_sha=merge_commit_sha,
                                    title=mr.get('title', ''),
                                    base_sha=previous_merge_sha or '',
                                    head_sha=merge_commit_sha,
                                    schema=after,
                                    previous_schema=before,
                                    diff=diff
                                )
                                
                                history_cache.add_event(project_path, key, event)
                                history_events_created += 1
                                
                                # Update state tracker for next iteration
                                endpoint_last_event[key] = event_type
                        
                        # Update timeline: current state becomes "before" for next MR
                        previous_merge_sha = merge_commit_sha
                        previous_endpoints = endpoints_current

                    except Exception as e:
                        result.specs_failed += 1
                        result.errors.append(f"MR !{mr_iid}: {e}")
                        logger.warning(f"[{config.name}] Failed processing MR !{mr_iid}: {e}")
                
                # Update last MR date
                if mrs_sorted:
                    latest_date = max(
                        mr.get('merged_at', '')[:10]
                        for mr in mrs_sorted
                        if mr.get('merged_at')
                    )
                    self._set_last_mr_date(project_path, latest_date)
                
                logger.info(
                    f"[{config.name}] History complete: "
                    f"{len(endpoints_found)} endpoints, {history_events_created} events"
                )
        
        except Exception as e:
            result.errors.append(f"Project error: {e}")
            logger.exception(f"[{config.name}] Cache warming with history failed")
        
        result.duration_seconds = (datetime.now() - start_time).total_seconds()
        
        logger.info(
            f"[{config.name}] Complete: "
            f"{result.specs_cached} cached, {result.specs_skipped} skipped, "
            f"{result.specs_failed} failed in {result.duration_seconds:.1f}s"
        )
        
        return result
    
    def _extract_task_id(self, title: str, branch: str) -> str:
        """Extract JIRA task ID from MR title or branch."""
        import re
        
        # Combine sources (title first)
        sources = [title, branch]
        
        for text in sources:
            if not text:
                continue
            
            # Match patterns: LOGRETAIL-1168, LOG-4996
            patterns = [
                r'([A-Za-z]+-\d+)',
                r'([A-Za-z]+)\s+(\d+)',
            ]
            
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    if len(match.groups()) == 2:
                        return f"{match.group(1).upper()}-{match.group(2)}"
                    return match.group(1).upper()
                    
        # Fallback: Check for REVERT
        if title.lower().startswith('revert') or branch.lower().startswith('revert'):
            return 'REVERT'
        
        return 'NO-TASK'
    
    # =========================================================================
    # Precise Endpoint History Building
    # =========================================================================
    
    async def build_endpoint_history_precise(
        self,
        project_path: str,
        endpoint_key: str,
        mr_limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Build precise history for a specific endpoint.
        
        This method:
        1. Fetches all MRs
        2. Filters to MRs that changed swagger files (fast API call)
        3. For each filtered MR, compares endpoint before/after
        4. Returns only MRs that actually changed this endpoint
        
        Args:
            project_path: GitLab project path
            endpoint_key: Endpoint to track (e.g., "POST /orders/products/list")
            mr_limit: Maximum MRs to process
            
        Returns:
            List of history events with precise MR attribution
        """
        from deepdiff import DeepDiff
        from src.openapi.schema_extractor import SchemaExtractor
        from src.cache.endpoint_history_cache import EXCLUDE_COSMETIC_FIELDS
        
        config = self.projects.get(project_path)
        if not config:
            logger.error(f"Project not found: {project_path}")
            return []
        
        # Parse endpoint
        parts = endpoint_key.split(" ", 1)
        method = parts[0].lower()
        path = parts[1] if len(parts) > 1 else ""
        
        events = []
        
        try:
            client_config = ClientConfig(
                base_url=config.gitlab_url,
                token=config.gitlab_token,
                ssl_verify=config.gitlab_ssl_verify,
            )
            
            async with AsyncGitLabClient(client_config) as client:
                # Step 1: Fetch all MRs
                logger.info(f"[PRECISE] Fetching MRs for {project_path}")
                mrs = await client.get_merged_mrs(
                    project_path,
                    limit=mr_limit,
                    target_branch=config.target_branch,
                )
                
                # Sort chronologically
                mrs_sorted = sorted(mrs, key=lambda x: x.get('merged_at', ''))
                logger.info(f"[PRECISE] {len(mrs_sorted)} MRs to process")
                
                # Step 2: Filter MRs that touched swagger files
                swagger_path = config.swagger.path
                relevant_mrs = []
                
                for i, mr in enumerate(mrs_sorted):
                    mr_iid = mr.get('iid')
                    if i % 50 == 0:
                        logger.info(f"[PRECISE] Filtering {i}/{len(mrs_sorted)}...")
                    
                    changed_files = await client.get_mr_changed_files(project_path, mr_iid)
                    
                    # Check if any swagger files were changed
                    if any(swagger_path in f or 'swagger' in f.lower() or 'openapi' in f.lower() 
                           for f in changed_files):
                        relevant_mrs.append(mr)
                
                logger.info(f"[PRECISE] {len(relevant_mrs)} MRs touched swagger files")
                
                # Step 3: For each relevant MR, compare endpoint before/after
                for i, mr in enumerate(relevant_mrs):
                    mr_iid = mr.get('iid')
                    title = mr.get('title', '')
                    
                    if mr_iid in [168, 176, 190]:
                         logger.info(f"[DEBUG] Processing target MR !{mr_iid}")
                    
                    # Get base/head SHAs from individual MR details
                    base_sha, head_sha = await client.get_mr_diff_shas(project_path, mr_iid)
                    
                    if mr_iid in [168, 176, 190]:
                        logger.info(f"[DEBUG] MR !{mr_iid} SHAs: base={base_sha}, head={head_sha}")
                    
                    if not base_sha or not head_sha:
                        logger.warning(f"[PRECISE] MR !{mr_iid}: No diff SHAs available")
                        continue
                    
                    # Download specs at both commits
                    try:
                        spec_before = await self._get_or_download_spec(
                            client, config, base_sha
                        )
                        spec_after = await self._get_or_download_spec(
                            client, config, head_sha
                        )
                    except Exception as e:
                        logger.warning(f"[PRECISE] MR !{mr_iid}: Failed to download specs: {e}")
                        continue
                    
                    if mr_iid in [168, 176, 190]:
                        logger.info(f"[DEBUG] MR !{mr_iid} specs: before={bool(spec_before)}, after={bool(spec_after)}")

                    
                    # Extract endpoint from both specs
                    endpoint_before = None
                    endpoint_after = None
                    
                    if spec_before:
                        endpoint_before = SchemaExtractor.extract(spec_before, method, path)
                    if spec_after:
                        endpoint_after = SchemaExtractor.extract(spec_after, method, path)
                    
                    # Determine event type
                    event_type = None
                    diff = None
                    
                    if endpoint_before is None and endpoint_after is not None:
                        event_type = "CREATED"
                    elif endpoint_before is not None and endpoint_after is None:
                        event_type = "DELETED"
                    elif endpoint_before is not None and endpoint_after is not None:
                        diff = DeepDiff(
                            endpoint_before,
                            endpoint_after,
                            ignore_order=True,
                            exclude_regex_paths=EXCLUDE_COSMETIC_FIELDS
                        )
                        if diff:
                            event_type = "MODIFIED"
                    
                    if event_type:
                        # Try to find original MR if this is a merge/release MR
                        origin_mr = await self._find_origin_mr(
                            client, project_path, mr, swagger_path,
                            endpoint_key=endpoint_key,
                            method=method,
                            path=path
                        )
                        
                        final_mr = origin_mr if origin_mr else mr
                        final_task_id = self._extract_task_id(
                            final_mr.get('title', ''), 
                            final_mr.get('source_branch', '')
                        )

                        # Fallback to Parent MR if Origin didn't have a task ID
                        if final_task_id == 'NO-TASK' and origin_mr:
                            final_task_id = self._extract_task_id(
                                mr.get('title', ''), 
                                mr.get('source_branch', '')
                            )
                        
                        # Check for Revert and set event_type accordingly
                        if final_mr.get('title', '').lower().startswith('revert') or final_mr.get('source_branch', '').lower().startswith('revert'):
                            event_type = "REVERT" 
                        
                        logger.info(f"[PRECISE] MR !{mr_iid} -> Origin !{final_mr['iid']} ({final_task_id}): {event_type}")
                        
                        events.append({
                            'event_type': event_type,
                            'mr_iid': final_mr['iid'], # Attribution to origin
                            'task_id': final_task_id, # Task from origin
                            'author': final_mr.get('author', {}).get('name', 'Unknown') 
                                if isinstance(final_mr.get('author'), dict) else 'Unknown',
                            'merged_at': mr.get('merged_at', ''), # Keep date of deploy to master
                            'title': final_mr.get('title', ''), # Title from origin
                            'base_sha': base_sha,
                            'head_sha': head_sha,
                            'schema': endpoint_after,
                            'previous_schema': endpoint_before,
                            'diff': diff,
                            'parent_mr_iid': mr_iid if origin_mr else None, # Track parent for debugging
                        })
                
                # Deduplicate events: keep unique (task_id, diff_hash)
                # If duplicates exist, prefer the one that is NOT a "Release" or "Merge" if possible,
                # or simply the one with lower ID (original feature MR)
                unique_events = {}
                for event in events:
                    # Create a signature for the event content
                    diff = event.get('diff')
                    diff_sig = str(diff) if diff else "created_or_deleted"
                    task_id = event.get('task_id')
                    
                    # If we can't identify task, fall back to MR ID
                    key = (task_id, diff_sig) if task_id != 'NO-TASK' else (f"MR-{event['mr_iid']}", diff_sig)
                    
                    if key not in unique_events:
                        unique_events[key] = event
                    else:
                        # Logic to choose 'better' event
                        existing = unique_events[key]
                        # If current is "original" (lower MR ID usually implies created earlier), prefer it
                        if event['mr_iid'] < existing['mr_iid']:
                            unique_events[key] = event
                
                final_events = sorted(unique_events.values(), key=lambda x: x.get('merged_at', ''))
                
                logger.info(f"[PRECISE] Complete: {len(final_events)} events for {endpoint_key}")
                return final_events
                
        except Exception as e:
            logger.exception(f"[PRECISE] Failed: {e}")
            return []
    
    async def _find_origin_mr(
        self,
        client: AsyncGitLabClient,
        project_path: str,
        parent_mr: Dict[str, Any],
        swagger_path: str,
        endpoint_key: str = "",
        method: str = "",
        path: str = "",
    ) -> Optional[Dict[str, Any]]:
        """
        Find the original MR that introduced the change.
        
        Delegates to the OriginMRResolver facade.
        """
        return await self.origin_resolver.resolve(
            client,
            project_path,
            parent_mr,
            swagger_path,
            endpoint_key,
            method,
            path
        )
    
    async def _download_spec_adapter(
        self,
        client: AsyncGitLabClient,
        project_path: str,
        sha: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Adapter to allow OriginMRResolver to download specs using project path string.
        Resolves project path to config.
        """
        config = self.projects.get(project_path)
        if not config:
            logger.warning(f"Project config not found for {project_path}")
            return None
        return await self._get_or_download_spec(client, config, sha)

    async def _get_or_download_spec(
        self,
        client: AsyncGitLabClient,
        config: ProjectConfig,
        sha: str,
    ) -> Optional[Dict[str, Any]]:
        """Get spec from cache or download it."""
        # Check cache first
        cached = self.cache.get_spec(config.path, sha)
        if cached is not None:
            return cached if cached else None
        
        # Download
        try:
            files = await client.download_swagger_folder(
                config.path, sha, config.swagger
            )
            spec = self.spec_loader.load_spec_from_files(
                files, config.swagger.entry_files
            )
            if spec:
                self.cache.set_spec(config.path, sha, spec)
            return spec
        except FolderNotFoundError:
            return None
        except Exception as e:
            logger.debug(f"Failed to download spec at {sha[:8]}: {e}")
            return None

