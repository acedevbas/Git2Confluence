"""
DiskCache - File-based cache for OpenAPI specs with multi-project support.

Replaces Redis for single-container deployments.
Uses diskcache library for fast file-based caching.

Key structure:
- specs/{project_hash}/{sha16} - Full OpenAPI spec
- schemas/{project_hash}/{sha8}/{endpoint_hash} - Extracted endpoint schema
"""
import os
import hashlib
import gzip
from typing import Optional, Dict, Any, List
from pathlib import Path

import orjson
from diskcache import Cache

from config import settings

# Special marker for "no spec found"
NULL_MARKER = "__NULL__"


class DiskCacheManager:
    """
    File-based cache for OpenAPI specs with multi-project support.
    
    Uses DiskCache (SQLite-backed) for fast persistent storage.
    """
    
    def __init__(self, cache_dir: str = None):
        """
        Initialize cache.
        
        Args:
            cache_dir: Directory for cache files (default: ./cache_data)
        """
        self.cache_dir = cache_dir or getattr(settings, 'cache_dir', './cache_data')
        self._cache: Optional[Cache] = None
        self._connected = False
        self.connect()
    
    def connect(self) -> bool:
        """Connect to cache (create directory if needed)."""
        try:
            Path(self.cache_dir).mkdir(parents=True, exist_ok=True)
            self._cache = Cache(
                self.cache_dir,
                size_limit=2 * 1024 * 1024 * 1024,  # 2GB limit
                eviction_policy='least-recently-used'
            )
            self._connected = True
            print(f"✅ Connected to DiskCache at {self.cache_dir}")
            return True
        except Exception as e:
            print(f"❌ Failed to connect to DiskCache: {e}")
            self._connected = False
            return False
    
    def _project_hash(self, project_path: str) -> str:
        """Generate short hash for project path."""
        return hashlib.md5(project_path.encode()).hexdigest()[:8]
    
    def _make_spec_key(self, project_path: str, commit_sha: str) -> str:
        """Create key for spec storage."""
        proj_hash = self._project_hash(project_path)
        return f"specs/{proj_hash}/{commit_sha[:16]}"
    
    def _make_schema_key(self, project_path: str, commit_sha: str, endpoint: str) -> str:
        """Create key for schema storage."""
        proj_hash = self._project_hash(project_path)
        endpoint_hash = hashlib.md5(endpoint.encode()).hexdigest()[:8]
        return f"schemas/{proj_hash}/{commit_sha[:8]}/{endpoint_hash}"
    
    # =========================================================================
    # SPEC OPERATIONS
    # =========================================================================
    
    def get_spec(self, project_path: str, commit_sha: str) -> Optional[Dict[str, Any]]:
        """
        Get OpenAPI spec from cache.
        
        Returns:
            - Dict: The spec
            - False: Spec was checked but doesn't exist for this commit
            - None: Not in cache (needs download)
        """
        if not self._connected:
            raise RuntimeError("Not connected to cache")
        
        key = self._make_spec_key(project_path, commit_sha)
        value = self._cache.get(key)
        
        if value is None:
            return None  # Not in cache
        
        if value == NULL_MARKER:
            return False  # No spec for this commit
        
        try:
            # Decompress if needed
            if isinstance(value, bytes) and value[:2] == b'\x1f\x8b':
                value = gzip.decompress(value)
            return orjson.loads(value)
        except Exception as e:
            print(f"⚠️ Failed to decode spec: {e}")
            return None
    
    def set_spec(self, project_path: str, commit_sha: str, spec: Optional[Dict[str, Any]]) -> bool:
        """Save spec to cache."""
        if not self._connected:
            raise RuntimeError("Not connected to cache")
        
        key = self._make_spec_key(project_path, commit_sha)
        
        if spec is None or spec is False:
            self._cache.set(key, NULL_MARKER)
        else:
            # Compress for storage efficiency
            # OPT_NON_STR_KEYS needed for HTTP status codes (200, 404, etc.)
            json_bytes = orjson.dumps(spec, option=orjson.OPT_SORT_KEYS | orjson.OPT_NON_STR_KEYS)
            compressed = gzip.compress(json_bytes, compresslevel=6)
            self._cache.set(key, compressed)
        
        # Store project metadata for stats
        from datetime import datetime
        proj_hash = self._project_hash(project_path)
        self._cache.set(f"project_names/{proj_hash}", project_path)
        self._cache.set(f"project_updated/{proj_hash}", datetime.now().isoformat())
        
        return True
    
    def has_spec(self, project_path: str, commit_sha: str) -> bool:
        """Check if spec exists in cache."""
        if not self._connected:
            return False
        key = self._make_spec_key(project_path, commit_sha)
        return key in self._cache
    
    # =========================================================================
    # SCHEMA OPERATIONS
    # =========================================================================
    
    def get_schema(self, project_path: str, commit_sha: str, endpoint: str) -> Optional[Dict[str, Any]]:
        """Get extracted schema from cache."""
        if not self._connected:
            raise RuntimeError("Not connected to cache")
        
        key = self._make_schema_key(project_path, commit_sha, endpoint)
        value = self._cache.get(key)
        
        if value is None:
            return None
        
        if value == NULL_MARKER:
            return False  # No endpoint in this version
        
        try:
            return orjson.loads(value)
        except Exception:
            return None
    
    def set_schema(self, project_path: str, commit_sha: str, endpoint: str, 
                   schema: Optional[Dict[str, Any]]) -> bool:
        """Save schema to cache."""
        if not self._connected:
            raise RuntimeError("Not connected to cache")
        
        key = self._make_schema_key(project_path, commit_sha, endpoint)
        
        if schema is None:
            self._cache.set(key, NULL_MARKER)
        else:
            value = orjson.dumps(schema, option=orjson.OPT_SORT_KEYS | orjson.OPT_NON_STR_KEYS)
            self._cache.set(key, value)
        
        return True
    
    # =========================================================================
    # STATS & MANAGEMENT
    # =========================================================================
    
    def get_stats(self) -> Dict[str, Any]:
        """Get overall cache statistics."""
        if not self._connected:
            return {"connected": False}
        
        return {
            "connected": True,
            "cache_dir": self.cache_dir,
            "size_bytes": self._cache.volume(),
            "total_keys": len(self._cache)
        }
    
    def get_project_stats(self, project_path: str = None) -> Dict[str, Any]:
        """
        Get per-project cache statistics.
        
        If project_path is None, returns stats for all projects.
        """
        if not self._connected:
            return {"connected": False}
        
        # Scan keys to count per project
        projects = {}
        
        for key in self._cache.iterkeys():
            parts = key.split('/')
            if len(parts) >= 2:
                key_type = parts[0]  # specs or schemas
                proj_hash = parts[1]
                
                # Skip non-data keys
                if key_type in ('project_names', 'history', 'history_meta'):
                    continue
                
                if proj_hash not in projects:
                    projects[proj_hash] = {"spec_count": 0, "schema_count": 0}
                
                if key_type == "specs":
                    projects[proj_hash]["spec_count"] += 1
                elif key_type == "schemas":
                    projects[proj_hash]["schema_count"] += 1
        
        if project_path:
            proj_hash = self._project_hash(project_path)
            return {
                "project_path": project_path,
                **projects.get(proj_hash, {"spec_count": 0, "schema_count": 0})
            }
        
        # Resolve project names from stored mapping
        project_list = []
        for h, stats in projects.items():
            project_name = self._cache.get(f"project_names/{h}") or f"unknown ({h})"
            last_updated = self._cache.get(f"project_updated/{h}")
            project_list.append({
                "project_path": project_name,
                "last_updated": last_updated,
                **stats
            })
        
        return {
            "connected": True,
            "projects": project_list
        }
    
    def get_all_projects(self) -> List[Dict[str, Any]]:
        """List all cached projects with their stats."""
        stats = self.get_project_stats()
        return stats.get("projects", [])
    
    def flush_project(self, project_path: str) -> int:
        """Delete all cache for a specific project including metadata."""
        if not self._connected:
            return 0
        
        proj_hash = self._project_hash(project_path)
        prefixes = [
            f"specs/{proj_hash}/",
            f"schemas/{proj_hash}/",
        ]
        
        deleted = 0
        keys_to_delete = []
        
        for key in self._cache.iterkeys():
            for prefix in prefixes:
                if key.startswith(prefix):
                    keys_to_delete.append(key)
                    break
        
        for key in keys_to_delete:
            del self._cache[key]
            deleted += 1
        
        # Also delete project metadata
        meta_keys = [
            f"project_names/{proj_hash}",
            f"project_updated/{proj_hash}",
        ]
        for key in meta_keys:
            if key in self._cache:
                del self._cache[key]
                deleted += 1
        
        return deleted
    
    def flush_all(self) -> bool:
        """Clear entire cache."""
        if not self._connected:
            return False
        self._cache.clear()
        return True
    
    def close(self):
        """Close cache connection."""
        if self._cache:
            self._cache.close()
            self._connected = False
            print("🔌 DiskCache connection closed")
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


# Convenience function
def create_cache() -> DiskCacheManager:
    """Create cache with default settings."""
    return DiskCacheManager()
