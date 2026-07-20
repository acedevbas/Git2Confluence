"""
Endpoint Change Detector for Origin MR Detection.

This module provides the EndpointChangeDetector class that determines
whether a specific MR introduced changes to a given API endpoint.

It encapsulates:
- Spec downloading and caching
- Endpoint extraction via SchemaExtractor
- Change comparison via DeepDiff
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

from deepdiff import DeepDiff

from src.openapi.file_detection import touches_openapi_source
from src.openapi.schema_extractor import SchemaExtractor
from .types import CandidateMR, EndpointChange, OriginDetectionConfig, ChangeType, EXCLUDE_COSMETIC_REGEX_PATHS

if TYPE_CHECKING:
    from src.gitlab.client import AsyncGitLabClient

logger = logging.getLogger(__name__)


@dataclass
class SpecProvider:
    """
    Provides spec downloading and caching functionality.
    
    This is a composition point - the detector doesn't care how specs
    are retrieved, just that they can be got by SHA.
    """
    download_func: Callable[[str], Any]  # async (sha) -> Optional[Dict]


class EndpointChangeDetector:
    """
    Detects whether a candidate MR changed a specific endpoint.
    
    This class encapsulates the logic for:
    1. Downloading specs at base and head SHAs
    2. Extracting the specific endpoint from each spec
    3. Comparing with DeepDiff to detect meaningful changes
    
    Usage:
        detector = EndpointChangeDetector(spec_provider, config)
        change = await detector.detect(client, project, candidate, "POST /orders", "post", "/orders")
    """
    
    def __init__(
        self,
        spec_download_func: Callable[[AsyncGitLabClient, str, str], Any],
        config: OriginDetectionConfig,
    ):
        """
        Initialize the detector.
        
        Args:
            spec_download_func: Async function (client, project_path, sha) -> Optional[Dict]
            config: Detection configuration
        """
        self._download_spec = spec_download_func
        self._config = config
    
    async def detect(
        self,
        client: AsyncGitLabClient,
        project_path: str,
        candidate: CandidateMR,
        swagger_path: str,
        endpoint_key: str,
        method: str,
        path: str,
    ) -> Optional[EndpointChange]:
        """
        Detect if a candidate MR changed the specified endpoint.
        
        Args:
            client: GitLab API client
            project_path: GitLab project path
            candidate: Candidate MR to inspect
            swagger_path: Path to swagger folder (for file change filtering)
            endpoint_key: Full endpoint key (e.g., "POST /orders")
            method: HTTP method (lowercase)
            path: API path
            
        Returns:
            EndpointChange if the MR changed the endpoint, None otherwise
        """
        try:
            # Step 1: Quick filter - check if MR touched swagger files
            changed_files = await client.get_mr_changed_files(
                project_path, 
                candidate.iid
            )
            
            if not self._touches_swagger(changed_files, swagger_path):
                logger.debug(f"[Detector] !{candidate.iid} didn't touch swagger files")
                return None
            
            # Step 2: Get diff SHAs
            base_sha, head_sha = await client.get_mr_diff_shas(
                project_path, 
                candidate.iid
            )
            
            if not base_sha or not head_sha:
                logger.debug(f"[Detector] !{candidate.iid} has no diff SHAs")
                return None
            
            # Step 3: Download specs
            spec_before = await self._download_spec(client, project_path, base_sha)
            spec_after = await self._download_spec(client, project_path, head_sha)
            
            # Step 4: Extract endpoint from each spec
            endpoint_before = self._extract_endpoint(spec_before, method, path)
            endpoint_after = self._extract_endpoint(spec_after, method, path)
            
            # Step 5: Determine change type
            change_type = self._compare_endpoints(endpoint_before, endpoint_after)
            
            if change_type == ChangeType.NONE:
                logger.debug(f"[Detector] !{candidate.iid} has no changes to {endpoint_key}")
                return None
            
            logger.info(
                f"[Detector] Found origin !{candidate.iid} "
                f"({change_type.value} {endpoint_key})"
            )
            
            return EndpointChange(
                endpoint_key=endpoint_key,
                change_type=change_type.value,
                mr=candidate,
            )
            
        except Exception as e:
            logger.warning(f"[Detector] Error inspecting !{candidate.iid}: {e}")
            return None
    
    def _touches_swagger(
        self, 
        changed_files: list, 
        swagger_path: str
    ) -> bool:
        """Check if any changed file is a swagger/openapi file."""
        return touches_openapi_source(
            changed_files,
            swagger_path,
            self._config.swagger_patterns,
        )
    
    def _extract_endpoint(
        self, 
        spec: Optional[Dict[str, Any]], 
        method: str, 
        path: str
    ) -> Optional[Dict[str, Any]]:
        """Extract endpoint schema from spec."""
        if not spec:
            return None
        try:
            return SchemaExtractor.extract(spec, method, path)
        except Exception:
            return None
    
    def _compare_endpoints(
        self,
        before: Optional[Dict[str, Any]],
        after: Optional[Dict[str, Any]],
    ) -> ChangeType:
        """Compare two endpoint schemas and determine change type."""
        # Created: didn't exist before, exists now
        if before is None and after is not None:
            return ChangeType.CREATED
        
        # Deleted: existed before, doesn't exist now
        if before is not None and after is None:
            return ChangeType.DELETED
        
        # Both None: endpoint never existed in this MR's scope
        if before is None and after is None:
            return ChangeType.NONE
        
        # Both exist: check for meaningful differences
        diff = DeepDiff(
            before,
            after,
            ignore_order=True,
            exclude_regex_paths=EXCLUDE_COSMETIC_REGEX_PATHS,
        )
        
        if diff:
            return ChangeType.MODIFIED
        
        return ChangeType.NONE
