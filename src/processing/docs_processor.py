"""
Batch Documentation Processor.

Handles batch generation and publication of API documentation to Confluence.
"""
import logging
import asyncio
from typing import Optional, List, Dict, Any
from datetime import datetime

from src.processing.batch_processor import BatchProcessor, ProjectConfig
from src.gitlab.client import AsyncGitLabClient, ClientConfig
from src.openapi.spec_loader import SpecLoader
from src.openapi.schema_extractor import SchemaExtractor
from src.cache.disk_cache import DiskCacheManager
from src.cache.endpoint_history_cache import EndpointHistoryCache
from src.confluence.publisher import ConfluencePublisher

logger = logging.getLogger(__name__)

class DocumentationBatchProcessor:
    """
    Orchestrates batch documentation publication.
    
    Flow:
    1. Load project config
    2. Fetch LATEST spec from target branch (HEAD)
    3. Extract all endpoints
    4. For each endpoint:
       - Check if history exists in DiskCache
       - If yes, publish to Confluence using ConfluencePublisher
    """
    
    def __init__(self, projects: List[ProjectConfig]):
        self.projects = {p.name: p for p in projects}
        self.cache = DiskCacheManager()
        self.history_cache = EndpointHistoryCache(self.cache)
        self.spec_loader = SpecLoader()
    
    async def publish_all(
        self, 
        project_filter: Optional[str] = None,
        endpoint_filter: Optional[str] = None,
        dry_run: bool = False
    ):
        """
        Run batch publication.
        
        Args:
            project_filter: Specific project path to process
            endpoint_filter: Specific endpoint key (e.g. "POST /api/v1/users")
            dry_run: If True, skip actual Confluence API calls
        """
        target_projects = []
        if project_filter:
            # Look up by name; fallback to path match
            config = self.projects.get(project_filter)
            if not config:
                config = next((p for p in self.projects.values() if p.path == project_filter), None)
            if not config:
                logger.error(f"Project '{project_filter}' not found in configuration")
                return
            target_projects = [config]
        else:
            target_projects = list(self.projects.values())
            
        logger.info(f"🚀 Starting batch publication for {len(target_projects)} projects (Dry Run: {dry_run})")
        
        for config in target_projects:
            await self._process_project(config, endpoint_filter, dry_run)
            
    async def _process_project(
        self, 
        config: ProjectConfig, 
        endpoint_filter: Optional[str],
        dry_run: bool
    ):
        logger.info(f"[{config.name}] processing...")
        
        if not config.confluence:
            logger.warning(f"[{config.name}] No Confluence configuration found. Skipping.")
            return

        try:
            # 1. Fetch latest spec from GitLab
            client_config = ClientConfig(
                base_url=config.gitlab_url,
                token=config.gitlab_token,
                ssl_verify=config.gitlab_ssl_verify,
            )
            
            spec = None
            async with AsyncGitLabClient(client_config) as client:
                # Resolve HEAD sha
                head_sha = await client.get_branch_head(config.path, config.target_branch)
                if not head_sha:
                    logger.error(f"[{config.name}] Could not resolve HEAD for {config.target_branch}")
                    return
                
                logger.info(f"[{config.name}] HEAD is {head_sha[:8]}")
                
                # Download spec
                # Reuse logic from BatchProcessor essentially, but simplified
                try:
                    files = await client.download_swagger_folder(config.path, head_sha, config.swagger)
                    spec = self.spec_loader.load_spec_from_files(files, config.swagger.entry_files)
                except Exception as e:
                    logger.warning(f"[{config.name}] Folder download failed: {e}. Trying archive...")
                    # Fallback to archive
                    archive = await client.download_archive(config.path, head_sha)
                    # We need to process archive in thread to avoid blocking
                    spec = await asyncio.to_thread(self._process_archive_sync, archive)
            
            if not spec:
                logger.error(f"[{config.name}] Failed to load OpenAPI spec")
                return
                
            # 2. Extract endpoints
            endpoints = SchemaExtractor.extract_all(spec)
            logger.info(f"[{config.name}] Found {len(endpoints)} endpoints in current spec")
            
            # 3. Filter and Publish
            count_published = 0
            count_skipped = 0
            
            publisher = None
            if not dry_run:
                publisher = ConfluencePublisher(
                    base_url="https://kb.vseinstrumenti.ru", # TODO: Get from env or config? 
                    # The ProjectConfig doesn't seem to have base_url for Confluence, usually strictly env or global config
                    # existing manage.py uses .env via settings? 
                    # Let's check projects.yaml confluence config structure again.
                    # It has space, parent_page_id, token. Base URL is likely global.
                    token=config.confluence.token,
                    space_key=config.confluence.space,
                    parent_page_id=config.confluence.parent_page_id
                )

            for key, schema in endpoints.items():
                if endpoint_filter and key != endpoint_filter:
                    continue
                
                # Check history cache
                history = self.history_cache.get_history(config.name, key)
                if not history or not history.events:
                    logger.warning(f"[{config.name}] No history found for '{key}'. Skipping (strict mode).")
                    count_skipped += 1
                    continue
                
                logger.info(f"[{config.name}] Publishing '{key}' ({len(history.events)} events)")
                
                if dry_run:
                    count_published += 1
                    continue
                
                # Prepare events for publisher
                # Provide minimal fields required by publisher
                events_for_publisher = []
                for event in history.events:
                    events_for_publisher.append({
                        'date': event.merged_at[:10] if event.merged_at else '',
                        'task_id': event.task_id,
                        'mr_id': event.mr_iid,
                        'author': event.author,
                        'type': f"📝 {event.event_type}" if event.event_type == 'MODIFIED' else f"🆕 {event.event_type}" if event.event_type == 'CREATED' else f"❌ {event.event_type}",
                        'changes': '', # Generated by publisher
                        'diff': event.diff,
                        'schema': event.schema,
                        'previous_schema': event.previous_schema,
                        'link': event.mr_link,
                        'jira_link': event.jira_link,
                        'field_changes': event.field_changes
                    })
                
                # Publish
                try:
                    parts = key.split(" ", 1)
                    method = parts[0]
                    path = parts[1]
                    
                    publisher.publish_history(
                        method=method,
                        path=path,
                        events=events_for_publisher,
                        project_path=config.path,
                        project_name=config.name
                    )
                    count_published += 1
                except Exception as ex:
                    logger.error(f"[{config.name}] Failed to publish '{key}': {ex}")
            
            logger.info(f"[{config.name}] Result: {count_published} published, {count_skipped} skipped")

        except Exception as e:
            logger.exception(f"[{config.name}] Processing failed: {e}")

    def _process_archive_sync(self, archive_bytes: bytes) -> Optional[Dict[str, Any]]:
        """Process archive and extract spec (sync, for thread pool)."""
        import tempfile
        import zipfile
        import os
        import shutil
        
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = os.path.join(tmpdir, "archive.zip")
            with open(archive_path, 'wb') as f:
                f.write(archive_bytes)
            
            try:
                with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                    zip_ref.extractall(tmpdir)
            except zipfile.BadZipFile:
                return None
            
            return self.spec_loader.load_spec_from_snapshot(tmpdir)
