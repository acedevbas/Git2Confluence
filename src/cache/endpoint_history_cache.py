"""
Endpoint History Cache - Pre-computed API change history.

This module provides pre-computed storage for endpoint change history,
computed during cache warming rather than at documentation request time.

Key Features:
- Pre-computed history reduces doc generation from 30-60s to <1s
- Incremental updates - only processes new MRs
- Stores complete history events with schemas
- Uses diskcache for persistent storage

Architecture:
    Cache warming:
        1. For each MR, extract ALL endpoints
        2. Compare with previous versions
        3. Store history events per endpoint
    
    Documentation generation:
        1. Read pre-computed history from cache → O(1)
        2. Generate Confluence page (no schema comparison needed)

Cache Key Structure:
    history/{project_hash}/{endpoint_hash} → List[HistoryEvent]
    history_meta/{project_hash}/{endpoint_hash} → {last_mr_date, event_count}
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from deepdiff import DeepDiff

from .disk_cache import DiskCacheManager

logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class HistoryEvent:
    """
    A single change event in endpoint history.
    
    Stored pre-computed during cache warming.
    """
    event_type: str  # "CREATED", "MODIFIED", "DELETED"
    task_id: str
    mr_iid: int
    author: str
    merged_at: str  # ISO format date
    commit_sha: str
    title: Optional[str] = None
    base_sha: Optional[str] = None
    head_sha: Optional[str] = None
    diff: Optional[Dict[str, Any]] = None
    
    # Schema data (for documentation generation)
    schema: Optional[Dict[str, Any]] = None
    previous_schema: Optional[Dict[str, Any]] = None
    
    # Change details
    field_changes: Dict[str, str] = field(default_factory=dict)
    diff_summary: Optional[str] = None
    
    # Links
    mr_link: Optional[str] = None
    jira_link: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for caching."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> HistoryEvent:
        """Create from cached dictionary."""
        return cls(**data)


@dataclass
class EndpointHistory:
    """
    Complete history for a single endpoint.
    """
    endpoint_key: str  # "POST /orders"
    method: str
    path: str
    events: List[HistoryEvent] = field(default_factory=list)
    
    # Metadata
    last_updated: Optional[str] = None
    last_mr_date: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for caching."""
        return {
            "endpoint_key": self.endpoint_key,
            "method": self.method,
            "path": self.path,
            "events": [e.to_dict() for e in self.events],
            "last_updated": self.last_updated,
            "last_mr_date": self.last_mr_date,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EndpointHistory:
        """Create from cached dictionary."""
        return cls(
            endpoint_key=data["endpoint_key"],
            method=data["method"],
            path=data["path"],
            events=[HistoryEvent.from_dict(e) for e in data.get("events", [])],
            last_updated=data.get("last_updated"),
            last_mr_date=data.get("last_mr_date"),
        )


# =============================================================================
# Cosmetic Fields (excluded from comparison)
# =============================================================================

EXCLUDE_COSMETIC_FIELDS = [
    re.compile(r"\['description'\]"),
    re.compile(r"\['summary'\]"),
    re.compile(r"\['title'\]"),
    re.compile(r"\['externalDocs'\]"),
    re.compile(r"\['deprecated'\]"),
    re.compile(r"\['tags'\]"),
    re.compile(r"\['operationId'\]"),
    re.compile(r"\['minItems'\]"),
    re.compile(r"\['maxItems'\]"),
    re.compile(r"\['minLength'\]"),
    re.compile(r"\['maxLength'\]"),
    re.compile(r"\['minimum'\]"),
    re.compile(r"\['maximum'\]"),
    re.compile(r"\['pattern'\]"),
    re.compile(r"\['default'\]"),
    re.compile(r"\['x-.*'\]"),
    re.compile(r"\['example'\]"),
]


# =============================================================================
# Endpoint History Cache
# =============================================================================

class EndpointHistoryCache:
    """
    Manages pre-computed endpoint history in disk cache.
    
    Usage:
        cache = EndpointHistoryCache()
        
        # During cache warming:
        cache.add_event(project_path, endpoint_key, event)
        
        # During documentation generation:
        history = cache.get_history(project_path, endpoint_key)
    """
    
    def __init__(self, disk_cache: Optional[DiskCacheManager] = None):
        """
        Initialize history cache.
        
        Args:
            disk_cache: Existing disk cache manager (creates new if None)
        """
        self._cache = disk_cache or DiskCacheManager()
        self._owns_cache = disk_cache is None
    
    def close(self):
        """Close cache if we own it."""
        if self._owns_cache:
            self._cache.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        self.close()
    
    # =========================================================================
    # Key Generation
    # =========================================================================
    
    def _project_hash(self, project_path: str) -> str:
        """Generate short hash for project path."""
        return hashlib.md5(project_path.encode()).hexdigest()[:8]
    
    def _endpoint_hash(self, endpoint_key: str) -> str:
        """Generate short hash for endpoint key."""
        return hashlib.md5(endpoint_key.encode()).hexdigest()[:12]
    
    def _make_history_key(self, project_path: str, endpoint_key: str) -> str:
        """Create cache key for endpoint history."""
        proj_hash = self._project_hash(project_path)
        ep_hash = self._endpoint_hash(endpoint_key)
        return f"history/{proj_hash}/{ep_hash}"
    
    def _make_meta_key(self, project_path: str, endpoint_key: str) -> str:
        """Create cache key for history metadata."""
        proj_hash = self._project_hash(project_path)
        ep_hash = self._endpoint_hash(endpoint_key)
        return f"history_meta/{proj_hash}/{ep_hash}"
    
    # =========================================================================
    # Read Operations
    # =========================================================================
    
    def get_history(
        self, 
        project_path: str, 
        endpoint_key: str
    ) -> Optional[EndpointHistory]:
        """
        Get pre-computed history for an endpoint.
        
        Args:
            project_path: GitLab project path
            endpoint_key: Endpoint key (e.g., "POST /orders")
            
        Returns:
            EndpointHistory if found, None otherwise
        """
        key = self._make_history_key(project_path, endpoint_key)
        data = self._cache._cache.get(key)
        
        if data is None:
            return None
        
        return EndpointHistory.from_dict(data)
    
    def has_history(self, project_path: str, endpoint_key: str) -> bool:
        """Check if history exists for endpoint."""
        key = self._make_history_key(project_path, endpoint_key)
        return key in self._cache._cache
    
    def get_meta(
        self, 
        project_path: str, 
        endpoint_key: str
    ) -> Optional[Dict[str, Any]]:
        """Get metadata for endpoint history."""
        key = self._make_meta_key(project_path, endpoint_key)
        return self._cache._cache.get(key)
    
    def list_endpoints(self, project_path: str) -> List[str]:
        """
        List all endpoints with pre-computed history for a project.
        
        Returns:
            List of endpoint keys
        """
        proj_hash = self._project_hash(project_path)
        prefix = f"history/{proj_hash}/"
        
        endpoints = []
        for key in self._cache._cache.iterkeys():
            if key.startswith(prefix):
                # Get endpoint key from metadata
                meta_key = key.replace("history/", "history_meta/")
                meta = self._cache._cache.get(meta_key)
                if meta and "endpoint_key" in meta:
                    endpoints.append(meta["endpoint_key"])
        
        return endpoints
    
    # =========================================================================
    # Write Operations
    # =========================================================================
    
    def set_history(
        self, 
        project_path: str, 
        endpoint_key: str,
        history: EndpointHistory
    ) -> None:
        """
        Store complete history for an endpoint.
        
        Args:
            project_path: GitLab project path
            endpoint_key: Endpoint key
            history: Complete endpoint history
        """
        key = self._make_history_key(project_path, endpoint_key)
        self._cache._cache.set(key, history.to_dict())
        
        # Update metadata
        meta_key = self._make_meta_key(project_path, endpoint_key)
        self._cache._cache.set(meta_key, {
            "endpoint_key": endpoint_key,
            "method": history.method,
            "path": history.path,
            "event_count": len(history.events),
            "last_updated": datetime.now().isoformat(),
            "last_mr_date": history.last_mr_date,
        })
    
    def add_event(
        self,
        project_path: str,
        endpoint_key: str,
        event: HistoryEvent
    ) -> None:
        """
        Add a new event to endpoint history.
        
        Creates history if it doesn't exist.
        """
        history = self.get_history(project_path, endpoint_key)
        
        if history is None:
            parts = endpoint_key.split(" ", 1)
            method = parts[0] if len(parts) > 1 else ""
            path = parts[1] if len(parts) > 1 else endpoint_key
            history = EndpointHistory(
                endpoint_key=endpoint_key,
                method=method,
                path=path,
            )
        
        # Add event (avoid duplicates by commit_sha)
        existing_shas = {e.commit_sha for e in history.events}
        if event.commit_sha not in existing_shas:
            history.events.append(event)
            history.last_mr_date = event.merged_at[:10]
            history.last_updated = datetime.now().isoformat()
        
        self.set_history(project_path, endpoint_key, history)
    
    def clear_endpoint(self, project_path: str, endpoint_key: str) -> None:
        """Clear history for a specific endpoint."""
        key = self._make_history_key(project_path, endpoint_key)
        meta_key = self._make_meta_key(project_path, endpoint_key)
        
        if key in self._cache._cache:
            del self._cache._cache[key]
        if meta_key in self._cache._cache:
            del self._cache._cache[meta_key]
    
    def clear_project(self, project_path: str) -> int:
        """Clear all history for a project."""
        proj_hash = self._project_hash(project_path)
        prefix = f"history/{proj_hash}/"
        meta_prefix = f"history_meta/{proj_hash}/"
        
        deleted = 0
        keys_to_delete = []
        
        for key in self._cache._cache.iterkeys():
            if key.startswith(prefix) or key.startswith(meta_prefix):
                keys_to_delete.append(key)
        
        for key in keys_to_delete:
            del self._cache._cache[key]
            deleted += 1
        
        return deleted


# =============================================================================
# History Builder (used during cache warming)
# =============================================================================

class HistoryBuilder:
    """
    Builds endpoint history from MRs and schemas.
    
    Used during cache warming to pre-compute history for all endpoints.
    """
    
    def __init__(
        self,
        project_path: str,
        history_cache: EndpointHistoryCache,
        gitlab_url: str = "",
        jira_base_url: str = "",
    ):
        self.project_path = project_path
        self.history_cache = history_cache
        self.gitlab_url = gitlab_url
        self.jira_base_url = jira_base_url
        
        # Track schemas per endpoint for comparison
        self._previous_schemas: Dict[str, Dict[str, Any]] = {}
    
    def process_mr_schemas(
        self,
        mr_info: Dict[str, Any],
        endpoints: Dict[str, Dict[str, Any]]
    ) -> List[HistoryEvent]:
        """
        Process all endpoints from a single MR.
        
        Args:
            mr_info: MR metadata (iid, commit_sha, merged_at, author, task_id, etc.)
            endpoints: Dict of endpoint_key → schema
            
        Returns:
            List of history events generated
        """
        events = []
        
        for endpoint_key, current_schema in endpoints.items():
            previous_schema = self._previous_schemas.get(endpoint_key)
            
            event = self._compare_and_create_event(
                endpoint_key=endpoint_key,
                current_schema=current_schema,
                previous_schema=previous_schema,
                mr_info=mr_info
            )
            
            if event:
                self.history_cache.add_event(
                    self.project_path, 
                    endpoint_key, 
                    event
                )
                events.append(event)
            
            # Update previous schema
            self._previous_schemas[endpoint_key] = current_schema
        
        # Check for deleted endpoints
        for endpoint_key in list(self._previous_schemas.keys()):
            if endpoint_key not in endpoints:
                event = self._create_deleted_event(endpoint_key, mr_info)
                self.history_cache.add_event(
                    self.project_path,
                    endpoint_key,
                    event
                )
                events.append(event)
                del self._previous_schemas[endpoint_key]
        
        return events
    
    def _compare_and_create_event(
        self,
        endpoint_key: str,
        current_schema: Dict[str, Any],
        previous_schema: Optional[Dict[str, Any]],
        mr_info: Dict[str, Any]
    ) -> Optional[HistoryEvent]:
        """Compare schemas and create event if changed."""
        
        event_type = None
        field_changes = {}
        diff_summary = None
        
        if previous_schema is None:
            event_type = "CREATED"
        else:
            # Normalize and compare
            normalized_prev = self._normalize_schema(previous_schema)
            normalized_curr = self._normalize_schema(current_schema)
            
            diff = DeepDiff(
                normalized_prev,
                normalized_curr,
                ignore_order=True,
                exclude_regex_paths=EXCLUDE_COSMETIC_FIELDS
            )
            
            if diff:
                event_type = "MODIFIED"
                field_changes = self._extract_field_changes(diff)
                diff_summary = self._format_diff_summary(diff)
        
        if not event_type:
            return None
        
        return HistoryEvent(
            event_type=event_type,
            task_id=mr_info.get("task_id", "NO-TASK"),
            mr_iid=mr_info.get("iid", 0),
            author=mr_info.get("author", "Unknown"),
            merged_at=mr_info.get("merged_at", ""),
            commit_sha=mr_info.get("commit_sha", ""),
            schema=current_schema,
            previous_schema=previous_schema,
            field_changes=field_changes,
            diff_summary=diff_summary,
            mr_link=f"{self.gitlab_url}/{self.project_path}/-/merge_requests/{mr_info.get('iid', '')}",
            jira_link=f"{self.jira_base_url}/{mr_info.get('task_id', '')}" 
                if mr_info.get("task_id") not in (None, "NO-TASK", "REVERT") else None,
        )
    
    def _create_deleted_event(
        self,
        endpoint_key: str,
        mr_info: Dict[str, Any]
    ) -> HistoryEvent:
        """Create a DELETED event for endpoint."""
        previous_schema = self._previous_schemas.get(endpoint_key)
        
        return HistoryEvent(
            event_type="DELETED",
            task_id=mr_info.get("task_id", "NO-TASK"),
            mr_iid=mr_info.get("iid", 0),
            author=mr_info.get("author", "Unknown"),
            merged_at=mr_info.get("merged_at", ""),
            commit_sha=mr_info.get("commit_sha", ""),
            schema=None,
            previous_schema=previous_schema,
            field_changes={},
            diff_summary="Endpoint deleted",
            mr_link=f"{self.gitlab_url}/{self.project_path}/-/merge_requests/{mr_info.get('iid', '')}",
            jira_link=None,
        )
    
    def _normalize_schema(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize schema for comparison (sort allOf/anyOf/oneOf)."""
        if not isinstance(schema, dict):
            return schema
        
        result = {}
        for key, value in schema.items():
            if key in ('allOf', 'anyOf', 'oneOf') and isinstance(value, list):
                def sort_key(item):
                    if isinstance(item, dict) and 'properties' in item:
                        props = item['properties']
                        if props:
                            return sorted(props.keys())[0]
                    return ''
                sorted_items = sorted(value, key=sort_key)
                result[key] = [self._normalize_schema(item) for item in sorted_items]
            elif isinstance(value, dict):
                result[key] = self._normalize_schema(value)
            elif isinstance(value, list):
                result[key] = [
                    self._normalize_schema(item) if isinstance(item, dict) else item 
                    for item in value
                ]
            else:
                result[key] = value
        
        return result
    
    def _extract_field_changes(self, diff: Dict[str, Any]) -> Dict[str, str]:
        """Extract field changes from DeepDiff result."""
        changes = {}
        
        if 'dictionary_item_added' in diff:
            for path in diff['dictionary_item_added']:
                normalized = self._normalize_path(path)
                changes[normalized] = 'added'
        
        if 'dictionary_item_removed' in diff:
            for path in diff['dictionary_item_removed']:
                normalized = self._normalize_path(path)
                changes[normalized] = 'removed'
        
        if 'values_changed' in diff:
            for path in diff['values_changed']:
                normalized = self._normalize_path(path)
                changes[normalized] = 'modified'
        
        if 'type_changes' in diff:
            for path in diff['type_changes']:
                normalized = self._normalize_path(path)
                changes[normalized] = 'type_changed'
        
        return changes
    
    def _normalize_path(self, path: str) -> str:
        """Normalize DeepDiff path to readable format."""
        # root['key1']['key2'] → key1.key2
        path = path.replace("root", "")
        path = re.sub(r"\['([^']+)'\]", r".\1", path)
        path = re.sub(r"\[(\d+)\]", r"[\1]", path)
        return path.lstrip(".")
    
    def _format_diff_summary(self, diff: Dict[str, Any]) -> str:
        """Format diff as human-readable summary."""
        parts = []
        
        if 'dictionary_item_added' in diff:
            parts.append(f"+{len(diff['dictionary_item_added'])} fields")
        if 'dictionary_item_removed' in diff:
            parts.append(f"-{len(diff['dictionary_item_removed'])} fields")
        if 'values_changed' in diff:
            parts.append(f"~{len(diff['values_changed'])} modified")
        
        return ", ".join(parts) if parts else "Changed"
