"""
Type definitions for Origin MR Detection module.

This module defines the data structures used throughout the origin detection
system, following Python 3.10+ best practices with dataclasses and Protocol.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ChangeType(str, Enum):
    """Types of endpoint changes."""
    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class CandidateMR:
    """
    Represents a candidate Merge Request that might be the origin of a change.
    
    Immutable dataclass for safe passing between components.
    
    Attributes:
        iid: GitLab MR internal ID
        title: MR title (for task ID extraction)
        source_branch: Source branch name
        target_branch: Target branch name
        web_url: Optional URL for logging/debugging
    """
    iid: int
    title: str
    source_branch: str
    target_branch: str
    web_url: str = ""
    
    @classmethod
    def from_gitlab_response(cls, mr_data: Dict[str, Any]) -> CandidateMR:
        """Factory method to create from GitLab API response."""
        return cls(
            iid=mr_data.get('iid', 0),
            title=mr_data.get('title', ''),
            source_branch=mr_data.get('source_branch', ''),
            target_branch=mr_data.get('target_branch', ''),
            web_url=mr_data.get('web_url', ''),
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert back to dict for compatibility with existing code."""
        return {
            'iid': self.iid,
            'title': self.title,
            'source_branch': self.source_branch,
            'target_branch': self.target_branch,
            'web_url': self.web_url,
        }


@dataclass(frozen=True, slots=True)
class EndpointChange:
    """
    Represents a detected change to a specific endpoint.
    
    Attributes:
        endpoint_key: Full endpoint identifier (e.g., "POST /orders")
        change_type: Type of change (created, modified, deleted)
        mr: The MR that introduced this change
    """
    endpoint_key: str
    change_type: str  # "created" | "modified" | "deleted"
    mr: CandidateMR


@dataclass
class OriginDetectionConfig:
    """
    Configuration for the origin detection system.
    
    Centralizes all configurable patterns and limits to avoid hardcoding.
    
    Attributes:
        swagger_patterns: Patterns to identify swagger/openapi files
        commit_merge_pattern: Regex to extract branch from merge commits
        max_candidates: Maximum candidates to collect per strategy
        max_commits_scan: Maximum commits to scan for merge patterns
    """
    swagger_patterns: List[str] = field(
        default_factory=lambda: ['swagger', 'openapi']
    )
    commit_merge_pattern: str = r"Merge branch '([^']+)'"
    max_candidates: int = 50
    max_commits_scan: int = 50
    
    def matches_swagger_file(self, filepath: str) -> bool:
        """Check if a file path matches swagger/openapi patterns."""
        filepath_lower = filepath.lower()
        return any(pattern in filepath_lower for pattern in self.swagger_patterns)
    
    def extract_branch_from_commit(self, commit_title: str) -> Optional[str]:
        """Extract branch name from a merge commit message."""
        match = re.search(self.commit_merge_pattern, commit_title)
        if match:
            branch = match.group(1).replace('refs/heads/', '')
            return branch
        return None


# Cosmetic fields to exclude from DeepDiff comparisons
# These don't represent meaningful API changes
EXCLUDE_COSMETIC_REGEX_PATHS: List[str] = [
    r".*\['x-.*'\]",           # OpenAPI extensions
    r".*\['description'\]",    # Description changes
    r".*\['summary'\]",        # Summary changes  
    r".*\['example'\]",        # Example changes
    r".*\['examples'\]",       # Examples changes
    r".*\['deprecated'\]",     # Deprecation flag
]
