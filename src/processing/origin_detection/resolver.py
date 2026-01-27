"""
Origin MR Resolver - Main Facade for Origin Detection.

This module provides the OriginMRResolver class that orchestrates
candidate collection and endpoint change detection to find the
original MR that introduced a change.

This is the main entry point for the origin detection system.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from .types import CandidateMR, OriginDetectionConfig
from .candidate_collector import CandidateCollector, get_default_collectors
from .endpoint_detector import EndpointChangeDetector

if TYPE_CHECKING:
    from src.gitlab.client import AsyncGitLabClient

logger = logging.getLogger(__name__)


class OriginMRResolver:
    """
    Facade for finding the original MR that introduced an API change.
    
    This class orchestrates:
    1. Candidate collection via multiple strategies
    2. Deep inspection via EndpointChangeDetector
    3. Returns the first MR that actually changed the endpoint
    
    Design principles:
    - Dependency injection: collectors and detector are provided
    - Strategy pattern: multiple collectors can be used
    - Single responsibility: only orchestration logic here
    
    Usage:
        resolver = OriginMRResolver.create_default(spec_download_func)
        origin = await resolver.resolve(
            client, project, parent_mr, swagger_path,
            endpoint_key="POST /orders",
            method="post",
            path="/orders"
        )
    """
    
    def __init__(
        self,
        collectors: List[CandidateCollector],
        detector: EndpointChangeDetector,
        config: OriginDetectionConfig,
    ):
        """
        Initialize the resolver with dependencies.
        
        Args:
            collectors: List of candidate collectors (in priority order)
            detector: Endpoint change detector
            config: Detection configuration
        """
        self._collectors = collectors
        self._detector = detector
        self._config = config
    
    @classmethod
    def create_default(
        cls,
        spec_download_func: Callable[[Any, str, str], Any],
        config: Optional[OriginDetectionConfig] = None,
    ) -> OriginMRResolver:
        """
        Factory method to create resolver with default configuration.
        
        Args:
            spec_download_func: Async function (client, project_path, sha) -> Optional[Dict]
            config: Optional custom configuration
            
        Returns:
            Configured OriginMRResolver instance
        """
        config = config or OriginDetectionConfig()
        
        return cls(
            collectors=get_default_collectors(),
            detector=EndpointChangeDetector(spec_download_func, config),
            config=config,
        )
    
    async def resolve(
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
        Find the original MR that introduced a change to an endpoint.
        
        Args:
            client: GitLab API client
            project_path: GitLab project path
            parent_mr: Parent/release MR data from GitLab API
            swagger_path: Path to swagger folder
            endpoint_key: Full endpoint key (e.g., "POST /orders")
            method: HTTP method (lowercase)
            path: API path
            
        Returns:
            Original MR dict if found, None otherwise
        """
        source_branch = parent_mr.get('source_branch', '')
        
        # Quick exit for direct master merges
        if not source_branch or source_branch == 'master':
            return None
        
        # Step 1: Collect all candidates
        candidates = await self._collect_candidates(
            client, project_path, parent_mr
        )
        
        if not candidates:
            logger.debug(
                f"[Resolver] No candidates found for !{parent_mr.get('iid')}"
            )
            return None
        
        logger.debug(
            f"[Resolver] Collected {len(candidates)} candidates "
            f"for !{parent_mr.get('iid')}"
        )
        
        # Step 2: Deep inspection if endpoint info provided
        if endpoint_key and method and path:
            origin = await self._find_by_endpoint_change(
                client, project_path, candidates, swagger_path,
                endpoint_key, method, path
            )
            if origin:
                return origin.to_dict()
        
        # Step 3: Fallback - return first candidate touching swagger
        fallback = await self._find_first_swagger_toucher(
            client, project_path, candidates, swagger_path
        )
        if fallback:
            return fallback.to_dict()
        
        return None
    
    async def _collect_candidates(
        self,
        client: AsyncGitLabClient,
        project_path: str,
        parent_mr: Dict[str, Any],
    ) -> List[CandidateMR]:
        """Collect candidates from all collectors."""
        all_candidates: List[CandidateMR] = []
        seen_iids: set = set()
        
        for collector in self._collectors:
            try:
                candidates = await collector.collect(
                    client, project_path, parent_mr, self._config
                )
                
                # Deduplicate
                for candidate in candidates:
                    if candidate.iid not in seen_iids:
                        seen_iids.add(candidate.iid)
                        all_candidates.append(candidate)
                        
            except Exception as e:
                logger.warning(
                    f"[Resolver] Collector {type(collector).__name__} failed: {e}"
                )
        
        return all_candidates
    
    async def _find_by_endpoint_change(
        self,
        client: AsyncGitLabClient,
        project_path: str,
        candidates: List[CandidateMR],
        swagger_path: str,
        endpoint_key: str,
        method: str,
        path: str,
    ) -> Optional[CandidateMR]:
        """Find the candidate that actually changed the endpoint."""
        for candidate in candidates:
            change = await self._detector.detect(
                client, project_path, candidate, swagger_path,
                endpoint_key, method, path
            )
            if change:
                return candidate
        return None
    
    async def _find_first_swagger_toucher(
        self,
        client: AsyncGitLabClient,
        project_path: str,
        candidates: List[CandidateMR],
        swagger_path: str,
    ) -> Optional[CandidateMR]:
        """Fallback: find the first candidate that touched swagger files."""
        for candidate in candidates:
            try:
                changed = await client.get_mr_changed_files(
                    project_path, candidate.iid
                )
                for filepath in changed:
                    if swagger_path in filepath:
                        logger.info(
                            f"[Resolver] Fallback: using !{candidate.iid} "
                            f"(touched swagger)"
                        )
                        return candidate
                    if self._config.matches_swagger_file(filepath):
                        logger.info(
                            f"[Resolver] Fallback: using !{candidate.iid} "
                            f"(touched swagger)"
                        )
                        return candidate
            except Exception:
                continue
        return None
