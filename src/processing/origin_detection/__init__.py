"""
Origin Detection Module.

This module provides functionality to find the original MR that
introduced a change to an API endpoint, handling rollup/release merges.

Main Components:
    - OriginMRResolver: Facade for origin detection
    - CandidateCollector: Protocol for candidate collection strategies
    - EndpointChangeDetector: Detects endpoint changes in MRs
    - OriginDetectionConfig: Configuration dataclass

Usage:
    from src.processing.origin_detection import OriginMRResolver
    
    resolver = OriginMRResolver.create_default(spec_download_func)
    origin = await resolver.resolve(
        client, project, parent_mr, swagger_path,
        endpoint_key="POST /orders",
        method="post",
        path="/orders"
    )
"""

from .types import (
    CandidateMR,
    EndpointChange,
    OriginDetectionConfig,
    EXCLUDE_COSMETIC_REGEX_PATHS,
)
from .candidate_collector import (
    CandidateCollector,
    TargetBranchCollector,
    CommitMessageCollector,
    get_default_collectors,
)
from .endpoint_detector import (
    EndpointChangeDetector,
    ChangeType,
)
from .resolver import OriginMRResolver


__all__ = [
    # Main facade
    "OriginMRResolver",
    
    # Types
    "CandidateMR",
    "EndpointChange",
    "OriginDetectionConfig",
    "ChangeType",
    "EXCLUDE_COSMETIC_REGEX_PATHS",
    
    # Collectors
    "CandidateCollector",
    "TargetBranchCollector",
    "CommitMessageCollector",
    "get_default_collectors",
    
    # Detector
    "EndpointChangeDetector",
]
