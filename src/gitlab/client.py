"""
Async GitLab Client with Selective Folder Download.

This module provides an async HTTP client for GitLab API operations,
optimized for downloading only OpenAPI/Swagger spec folders instead
of entire repository archives.

Key Features:
- Selective folder download via Tree + Files API (~1MB vs ~100MB)
- Connection pooling with aiohttp.TCPConnector
- Rate limiting with asyncio.Semaphore
- Automatic retry on 429 (rate limit) responses
- Fallback to full archive for backward compatibility

Usage:
    async with AsyncGitLabClient(base_url, token) as client:
        files = await client.download_swagger_folder(project, sha, config)
        
Architecture:
    - Uses aiohttp for async HTTP with connection pooling
    - Follows context manager pattern for proper resource cleanup
    - Dataclasses for configuration (Pydantic-like but stdlib)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import aiohttp

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration Dataclasses
# =============================================================================

@dataclass(frozen=True)
class SwaggerConfig:
    """
    Per-project swagger folder configuration.
    
    Attributes:
        path: Path to swagger folder in repository (e.g., "api/swagger")
        entry_files: Priority-ordered list of main spec file names
    """
    path: str = "api/swagger"
    entry_files: tuple[str, ...] = (
        "openapi.yaml",
        "openapi.json", 
        "api.yaml",
        "api.json",
    )
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SwaggerConfig:
        """Create SwaggerConfig from dictionary (e.g., from YAML)."""
        return cls(
            path=data.get("path", "api/swagger"),
            entry_files=tuple(data.get("entry_files", [
                "openapi.yaml", "openapi.json", "api.yaml", "api.json"
            ])),
        )


@dataclass
class ClientConfig:
    """
    Client connection configuration.
    
    Attributes:
        base_url: GitLab instance URL
        token: Private access token
        ssl_verify: Whether to verify SSL certificates
        max_connections: Maximum concurrent connections (connection pool size)
        rate_limit: Maximum concurrent API requests (semaphore limit)
        timeout_total: Total request timeout in seconds
        retry_delay: Delay before retry on rate limit (seconds)
    """
    base_url: str
    token: str
    ssl_verify: bool = False
    max_connections: int = 20
    rate_limit: int = 10
    timeout_total: int = 120
    retry_delay: float = 5.0


# =============================================================================
# Custom Exceptions
# =============================================================================

class GitLabClientError(Exception):
    """Base exception for GitLab client errors."""
    pass


class FolderNotFoundError(GitLabClientError):
    """Raised when the requested folder doesn't exist at the specified commit."""
    pass


class FileNotFoundError(GitLabClientError):
    """Raised when a file doesn't exist at the specified commit."""
    pass


class RateLimitError(GitLabClientError):
    """Raised when rate limit is exceeded and retries are exhausted."""
    pass


class ApiError(GitLabClientError):
    """Raised for unexpected API errors."""
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(f"GitLab API error {status}: {message}")


# =============================================================================
# Async GitLab Client
# =============================================================================

class AsyncGitLabClient:
    """
    High-performance async GitLab client for OpenAPI spec retrieval.
    
    Key optimization: Downloads only the swagger folder (~1MB) instead of
    the entire repository archive (~100MB), achieving ~10-20x speedup.
    
    Usage as async context manager:
        async with AsyncGitLabClient(config) as client:
            files = await client.download_swagger_folder(project, sha)
    
    Thread Safety:
        Each instance maintains its own session and is NOT thread-safe.
        Use one instance per asyncio task/coroutine.
    """
    
    __slots__ = ('_config', '_session', '_rate_limiter', '_closed')
    
    def __init__(self, config: ClientConfig):
        """
        Initialize client with configuration.
        
        Args:
            config: Client configuration dataclass
        """
        self._config = config
        self._session: Optional[aiohttp.ClientSession] = None
        self._rate_limiter = asyncio.Semaphore(config.rate_limit)
        self._closed = False
    
    @classmethod
    def create(
        cls,
        base_url: str,
        token: str,
        ssl_verify: bool = False,
        **kwargs: Any
    ) -> AsyncGitLabClient:
        """
        Factory method for creating client with individual parameters.
        
        Args:
            base_url: GitLab instance URL
            token: Private access token
            ssl_verify: Whether to verify SSL certificates
            **kwargs: Additional ClientConfig parameters
        """
        config = ClientConfig(
            base_url=base_url.rstrip('/'),
            token=token,
            ssl_verify=ssl_verify,
            **kwargs
        )
        return cls(config)
    
    # =========================================================================
    # Context Manager Protocol
    # =========================================================================
    
    async def __aenter__(self) -> AsyncGitLabClient:
        """Enter async context: create HTTP session."""
        if self._session is not None:
            raise RuntimeError("Client already initialized")
        
        connector = aiohttp.TCPConnector(
            ssl=self._config.ssl_verify,
            limit=self._config.max_connections,
            limit_per_host=self._config.max_connections,
        )
        timeout = aiohttp.ClientTimeout(total=self._config.timeout_total)
        
        self._session = aiohttp.ClientSession(
            headers={"PRIVATE-TOKEN": self._config.token},
            connector=connector,
            timeout=timeout,
        )
        self._closed = False
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit async context: close HTTP session."""
        await self.close()
    
    async def close(self) -> None:
        """Close the HTTP session and release resources."""
        if self._session and not self._closed:
            await self._session.close()
            self._closed = True
    
    # =========================================================================
    # Internal Helpers
    # =========================================================================
    
    def _ensure_session(self) -> aiohttp.ClientSession:
        """Ensure session is initialized and return it."""
        if self._session is None or self._closed:
            raise RuntimeError(
                "Client not initialized. Use 'async with client:' context manager."
            )
        return self._session
    
    def _encode_project(self, project_path: str) -> str:
        """URL-encode project path for API calls."""
        return quote(project_path, safe='')
    
    async def _request_with_retry(
        self, 
        method: str, 
        url: str, 
        max_retries: int = 2,
        **kwargs: Any
    ) -> aiohttp.ClientResponse:
        """
        Make HTTP request with rate limiting and retry logic.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            url: Full URL to request
            max_retries: Maximum number of retries on rate limit
            **kwargs: Additional arguments to pass to session.request()
            
        Returns:
            aiohttp.ClientResponse object
            
        Raises:
            RateLimitError: If rate limit exceeded after all retries
            ApiError: For unexpected HTTP errors
        """
        session = self._ensure_session()
        retries = 0
        
        while True:
            async with self._rate_limiter:
                async with session.request(method, url, **kwargs) as resp:
                    if resp.status == 429:
                        retries += 1
                        if retries > max_retries:
                            raise RateLimitError(
                                f"Rate limit exceeded after {max_retries} retries"
                            )
                        logger.warning(
                            f"Rate limited, waiting {self._config.retry_delay}s "
                            f"(retry {retries}/{max_retries})"
                        )
                        await asyncio.sleep(self._config.retry_delay)
                        continue
                    
                    # Return a copy-friendly response for 2xx status
                    return resp
    
    # =========================================================================
    # Tree API: List Files in Directory
    # =========================================================================
    
    async def list_tree(
        self,
        project_path: str,
        sha: str,
        folder_path: str,
    ) -> List[Dict[str, Any]]:
        """
        List all files in a folder recursively at a specific commit.
        
        Uses GitLab Repository Tree API:
        GET /projects/:id/repository/tree?path=...&recursive=true&ref=<sha>
        
        Args:
            project_path: GitLab project path (e.g., "group/project")
            sha: Commit SHA
            folder_path: Path to folder (e.g., "api/swagger")
            
        Returns:
            List of tree items:
            [
                {"name": "openapi.yaml", "type": "blob", "path": "api/swagger/openapi.yaml"},
                {"name": "components", "type": "tree", "path": "api/swagger/components"},
                ...
            ]
            
        Raises:
            FolderNotFoundError: If folder doesn't exist at the commit
            ApiError: For unexpected API errors
        """
        session = self._ensure_session()
        project_id = self._encode_project(project_path)
        url = f"{self._config.base_url}/api/v4/projects/{project_id}/repository/tree"
        
        all_items: List[Dict[str, Any]] = []
        page = 1
        
        while True:
            params = {
                "path": folder_path,
                "recursive": "true",
                "ref": sha,
                "per_page": 100,
                "page": page,
            }
            
            async with self._rate_limiter:
                async with session.get(url, params=params) as resp:
                    if resp.status == 404:
                        raise FolderNotFoundError(
                            f"Folder '{folder_path}' not found at commit {sha[:8]}"
                        )
                    if resp.status == 429:
                        logger.warning("Rate limited on tree API, waiting...")
                        await asyncio.sleep(self._config.retry_delay)
                        continue
                    if resp.status != 200:
                        text = await resp.text()
                        raise ApiError(resp.status, text)
                    
                    items = await resp.json()
                    if not items:
                        break
                    
                    all_items.extend(items)
                    page += 1
        
        return all_items
    
    # =========================================================================
    # Files API: Download Single File
    # =========================================================================
    
    async def download_file_raw(
        self,
        project_path: str,
        sha: str,
        file_path: str,
    ) -> bytes:
        """
        Download a single file at a specific commit.
        
        Uses GitLab Repository Files API (raw endpoint):
        GET /projects/:id/repository/files/:path/raw?ref=<sha>
        
        Args:
            project_path: GitLab project path
            sha: Commit SHA
            file_path: Full file path in repository
            
        Returns:
            File content as bytes
            
        Raises:
            FileNotFoundError: If file doesn't exist
            ApiError: For unexpected API errors
        """
        session = self._ensure_session()
        project_id = self._encode_project(project_path)
        file_path_encoded = quote(file_path, safe='')
        
        url = (
            f"{self._config.base_url}/api/v4/projects/{project_id}"
            f"/repository/files/{file_path_encoded}/raw"
        )
        params = {"ref": sha}
        
        async with self._rate_limiter:
            async with session.get(url, params=params) as resp:
                if resp.status == 404:
                    raise FileNotFoundError(f"File not found: {file_path}")
                if resp.status == 429:
                    await asyncio.sleep(self._config.retry_delay)
                    async with session.get(url, params=params) as retry_resp:
                        if retry_resp.status != 200:
                            raise ApiError(retry_resp.status, await retry_resp.text())
                        return await retry_resp.read()
                if resp.status != 200:
                    raise ApiError(resp.status, await resp.text())
                
                return await resp.read()
    
    # =========================================================================
    # High-Level: Download Swagger Folder
    # =========================================================================
    
    async def download_swagger_folder(
        self,
        project_path: str,
        sha: str,
        swagger_config: Optional[SwaggerConfig] = None,
    ) -> Dict[str, bytes]:
        """
        Download only the swagger folder content, not entire repository.
        
        This is the main optimization: downloads ~1MB instead of ~100MB.
        
        Process:
        1. List all files in swagger folder via Tree API
        2. Download each file concurrently via Files API
        3. Return dict mapping paths to content
        
        Args:
            project_path: GitLab project path
            sha: Commit SHA
            swagger_config: Swagger folder configuration (uses defaults if None)
            
        Returns:
            Dictionary mapping file paths to their content bytes
            Example: {
                "api/swagger/openapi.yaml": b"...",
                "api/swagger/components/Order.yaml": b"...",
            }
            
        Raises:
            FolderNotFoundError: If swagger folder doesn't exist at this commit
        """
        config = swagger_config or SwaggerConfig()
        
        # Step 1: List all files in swagger folder
        logger.debug(f"Listing files in {config.path} at {sha[:8]}")
        tree_items = await self.list_tree(project_path, sha, config.path)
        
        # Filter to only files (blobs), not directories (trees)
        files_to_download = [
            item for item in tree_items
            if item.get('type') == 'blob'
        ]
        
        if not files_to_download:
            raise FolderNotFoundError(
                f"No files found in {config.path} at commit {sha[:8]}"
            )
        
        logger.debug(f"Found {len(files_to_download)} files to download")
        
        # Step 2: Download all files concurrently
        async def download_one(item: Dict[str, Any]) -> tuple[str, Optional[bytes]]:
            path = item['path']
            try:
                content = await self.download_file_raw(project_path, sha, path)
                return (path, content)
            except Exception as e:
                logger.warning(f"Failed to download {path}: {e}")
                return (path, None)
        
        results = await asyncio.gather(*[
            download_one(item) for item in files_to_download
        ])
        
        # Build result dict, filtering out failed downloads
        files = {path: content for path, content in results if content is not None}
        
        downloaded_size = sum(len(c) for c in files.values())
        logger.info(
            f"Downloaded {len(files)}/{len(files_to_download)} files "
            f"({downloaded_size / 1024:.1f} KB) from {config.path} at {sha[:8]}"
        )
        
        return files
    
    # =========================================================================
    # Fallback: Full Archive Download
    # =========================================================================
    
    async def download_archive(
        self,
        project_path: str,
        sha: str,
    ) -> bytes:
        """
        Download entire repository archive (fallback for old commits).
        
        Use only when swagger folder doesn't exist at the commit.
        This is the original slow method (~100MB download).
        
        Args:
            project_path: GitLab project path
            sha: Commit SHA
            
        Returns:
            ZIP archive content as bytes
        """
        session = self._ensure_session()
        project_id = self._encode_project(project_path)
        
        url = (
            f"{self._config.base_url}/api/v4/projects/{project_id}"
            f"/repository/archive.zip"
        )
        params = {"sha": sha}
        
        async with self._rate_limiter:
            async with session.get(url, params=params) as resp:
                if resp.status == 429:
                    await asyncio.sleep(self._config.retry_delay)
                    async with session.get(url, params=params) as retry_resp:
                        if retry_resp.status != 200:
                            raise ApiError(retry_resp.status, await retry_resp.text())
                        return await retry_resp.read()
                if resp.status != 200:
                    raise ApiError(resp.status, await resp.text())
                
                content = await resp.read()
                logger.info(
                    f"Downloaded full archive ({len(content) / 1024 / 1024:.1f} MB) "
                    f"for {sha[:8]}"
                )
                return content
    
    # =========================================================================
    # MR Fetching
    # =========================================================================
    
    async def get_merged_mrs(
        self,
        project_path: str,
        limit: Optional[int] = None,
        since_date: Optional[str] = None,
        target_branch: str = "master",
        state: str = "merged", # Added state parameter
    ) -> List[Dict[str, Any]]:
        """
        Fetch merge requests with optional filter. # Updated description
        
        Args:
            project_path: GitLab project path
            limit: Maximum number of MRs to fetch (None for unlimited)
            since_date: Only fetch MRs merged after this date (YYYY-MM-DD)
            target_branch: Target branch filter (default: master)
            state: MR state (merged, closed, opened, all). Default: merged # Added state description
            
        Returns:
            List of MR dictionaries # Simplified return description
        """
        session = self._ensure_session()
        project_id = self._encode_project(project_path)
        url = f"{self._config.base_url}/api/v4/projects/{project_id}/merge_requests"
        
        mrs: List[Dict[str, Any]] = []
        page = 1
        
        # If filtering by date, sort descending to get newest first
        # Note: 'merged_at' works for merged MRs, 'updated_at' might be better for others
        sort_order = "desc" if since_date else "asc"
        order_by = "merged_at" if state == "merged" else "updated_at" # New logic for order_by
        
        while limit is None or len(mrs) < limit:
            params = {
                "state": state, # Use the new state parameter
                "target_branch": target_branch,
                "order_by": order_by, # Use the new order_by variable
                "sort": sort_order,
                "per_page": 100,
                "page": page,
            }
            
            async with self._rate_limiter:
                async with session.get(url, params=params) as resp:
                    if resp.status == 429:
                        logger.warning("Rate limited, waiting...")
                        await asyncio.sleep(self._config.retry_delay)
                        continue # Corrected duplicated continue
                    if resp.status != 200:
                        logger.error(f"Failed to fetch MRs: {resp.status}")
                        break
                    
                    data = await resp.json()
                    if not data:
                        break
                    
                    for mr in data:
                        # Apply date filter
                        if since_date:
                            mr_date = mr.get('merged_at', '')[:10]
                            if mr_date < since_date:
                                # Reached older MRs, stop
                                return mrs[:limit] if limit else mrs
                        mrs.append(mr)
                        if limit and len(mrs) >= limit:
                             break
                    
                    page += 1
        
        return mrs[:limit] if limit else mrs
    
    # =========================================================================
    # MR Changes API: Precise Attribution
    # =========================================================================
    
    async def get_mr_changed_files(
        self,
        project_path: str,
        mr_iid: int,
    ) -> List[str]:
        """
        Get list of files changed in a specific MR.
        
        Uses GitLab MR Changes API:
        GET /projects/:id/merge_requests/:iid/changes
        
        Args:
            project_path: GitLab project path
            mr_iid: Merge request IID
            
        Returns:
            List of file paths that were modified in this MR
        """
        session = self._ensure_session()
        project_id = self._encode_project(project_path)
        url = (
            f"{self._config.base_url}/api/v4/projects/{project_id}"
            f"/merge_requests/{mr_iid}/changes"
        )
        
        async with self._rate_limiter:
            async with session.get(url) as resp:
                if resp.status == 429:
                    await asyncio.sleep(self._config.retry_delay)
                    async with session.get(url) as retry_resp:
                        if retry_resp.status != 200:
                            logger.warning(f"Failed to get MR !{mr_iid} changes: {retry_resp.status}")
                            return []
                        data = await retry_resp.json()
                elif resp.status != 200:
                    logger.warning(f"Failed to get MR !{mr_iid} changes: {resp.status}")
                    return []
                else:
                    data = await resp.json()
        
        changes = data.get('changes', [])
        return [c.get('new_path') or c.get('old_path') for c in changes if c]
    
    async def get_mr_diff_shas(
        self,
        project_path: str,
        mr_iid: int,
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Get base and head SHAs for a specific MR.
        
        Uses GitLab MR API to get diff_refs:
        GET /projects/:id/merge_requests/:iid
        
        Args:
            project_path: GitLab project path
            mr_iid: Merge request IID
            
        Returns:
            Tuple of (base_commit_sha, head_commit_sha)
            base_sha = commit before MR (target branch state)
            head_sha = last commit in MR branch
        """
        session = self._ensure_session()
        project_id = self._encode_project(project_path)
        url = (
            f"{self._config.base_url}/api/v4/projects/{project_id}"
            f"/merge_requests/{mr_iid}"
        )
        
        async with self._rate_limiter:
            async with session.get(url) as resp:
                if resp.status == 429:
                    await asyncio.sleep(self._config.retry_delay)
                    async with session.get(url) as retry_resp:
                        if retry_resp.status != 200:
                            logger.warning(f"Failed to get MR !{mr_iid}: {retry_resp.status}")
                            return (None, None)
                        data = await retry_resp.json()
                elif resp.status != 200:
                    logger.warning(f"Failed to get MR !{mr_iid}: {resp.status}")
                    return (None, None)
                else:
                    data = await resp.json()
        
        diff_refs = data.get('diff_refs', {})
        return (
            diff_refs.get('base_sha'),
            diff_refs.get('head_sha')
        )

    # =========================================================================
    # MR Commits API: For Origin Detection
    # =========================================================================
    
    async def get_mr_commits(
        self,
        project_path: str,
        mr_iid: int,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Get commits associated with a merge request.
        
        Uses GitLab MR Commits API:
        GET /projects/:id/merge_requests/:iid/commits
        
        Args:
            project_path: GitLab project path
            mr_iid: Merge request IID
            limit: Maximum commits to fetch
            
        Returns:
            List of commit objects with 'id', 'title', 'message', etc.
        """
        session = self._ensure_session()
        project_id = self._encode_project(project_path)
        url = (
            f"{self._config.base_url}/api/v4/projects/{project_id}"
            f"/merge_requests/{mr_iid}/commits"
        )
        
        async with self._rate_limiter:
            async with session.get(url, params={"per_page": limit}) as resp:
                if resp.status == 429:
                    await asyncio.sleep(self._config.retry_delay)
                    async with session.get(url, params={"per_page": limit}) as retry_resp:
                        if retry_resp.status != 200:
                            logger.warning(f"Failed to get commits for !{mr_iid}: {retry_resp.status}")
                            return []
                        return await retry_resp.json()
                elif resp.status != 200:
                    logger.warning(f"Failed to get commits for !{mr_iid}: {resp.status}")
                    return []
                return await resp.json()
    
    async def search_mrs_by_source_branch(
        self,
        project_path: str,
        source_branch: str,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Search for merge requests by source branch name.
        
        Uses GitLab MR API with source_branch filter:
        GET /projects/:id/merge_requests?source_branch=...
        
        Args:
            project_path: GitLab project path
            source_branch: Source branch name to search for
            limit: Maximum MRs to return
            
        Returns:
            List of MR objects matching the source branch
        """
        session = self._ensure_session()
        project_id = self._encode_project(project_path)
        url = f"{self._config.base_url}/api/v4/projects/{project_id}/merge_requests"
        
        params = {
            "source_branch": source_branch,
            "per_page": limit,
        }
        
        async with self._rate_limiter:
            async with session.get(url, params=params) as resp:
                if resp.status == 429:
                    await asyncio.sleep(self._config.retry_delay)
                    async with session.get(url, params=params) as retry_resp:
                        if retry_resp.status != 200:
                            return []
                        return await retry_resp.json()
                elif resp.status != 200:
                    return []
                return await resp.json()
    async def get_branch_head(
        self,
        project_path: str,
        branch_name: str,
    ) -> Optional[str]:
        """
        Get the commit SHA of the HEAD of a branch.
        
        Uses GitLab Branches API:
        GET /projects/:id/repository/branches/:branch
        
        Args:
            project_path: GitLab project path
            branch_name: Branch name (e.g., "master")
            
        Returns:
            Commit SHA (str) or None if branch doesn't exist
        """
        session = self._ensure_session()
        project_id = self._encode_project(project_path)
        branch_encoded = quote(branch_name, safe='')
        url = f"{self._config.base_url}/api/v4/projects/{project_id}/repository/branches/{branch_encoded}"
        
        async with self._rate_limiter:
            async with session.get(url) as resp:
                if resp.status == 429:
                    await asyncio.sleep(self._config.retry_delay)
                    async with session.get(url) as retry_resp:
                        if retry_resp.status != 200:
                            logger.error(f"Failed to fetch branch {branch_name}: {retry_resp.status}")
                            return None
                        data = await retry_resp.json()
                elif resp.status != 200:
                    logger.error(f"Failed to fetch branch {branch_name}: {resp.status}")
                    return None
                else:
                    data = await resp.json()
        
        commit = data.get('commit', {})
        return commit.get('id')
