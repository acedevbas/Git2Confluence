
import os
import json
import zipfile
import shutil
import tempfile
import logging
from typing import Optional, Dict, Any, List, Sequence
from prance import ResolvingParser
import yaml  # PyYAML

logger = logging.getLogger(__name__)


class SpecLoader:
    """
    Loads and resolves OpenAPI/Swagger specs from repository snapshots.
    
    Uses Prance's RefResolver directly (without validation) to handle
    specs that may be incomplete or non-compliant (e.g., missing 'responses').
    
    Supports two loading modes:
    1. load_spec_from_snapshot() - from extracted ZIP archive (legacy)
    2. load_spec_from_files() - from file dictionary (optimized async mode)
    """
    
    # Default entry file names in priority order
    DEFAULT_ENTRY_FILES = (
        "openapi.yaml",
        "openapi.json",
        "api.yaml",
        "api.json",
    )
    
    def __init__(self):
        pass
    
    # =========================================================================
    # New: Load from File Dictionary (for async client)
    # =========================================================================
    
    def load_spec_from_files(
        self,
        files: Dict[str, bytes],
        entry_files: Optional[Sequence[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Load and resolve OpenAPI spec from downloaded files dictionary.
        
        This method is used with the optimized async GitLab client that
        downloads only the swagger folder instead of the entire repository.
        
        Process:
        1. Create temporary directory
        2. Write all files preserving directory structure
        3. Find entry file (openapi.yaml, api.yaml, etc.)
        4. Resolve using existing Prance logic
        5. Clean up temporary directory
        
        Args:
            files: Dictionary mapping file paths to content bytes
                   Example: {"api/swagger/openapi.yaml": b"...", 
                            "api/swagger/components/Order.yaml": b"..."}
            entry_files: Priority-ordered list of main spec file names
                        Defaults to: openapi.yaml, openapi.json, api.yaml, api.json
        
        Returns:
            Resolved OpenAPI spec dictionary or None on failure
        """
        if not files:
            logger.warning("No files provided to load_spec_from_files")
            return None
        
        entry_files = entry_files or self.DEFAULT_ENTRY_FILES
        
        # Create temporary directory for file reconstruction
        with tempfile.TemporaryDirectory(prefix="openapi_spec_") as tmpdir:
            # Step 1: Write all files to temp directory
            for file_path, content in files.items():
                # Normalize path separators
                normalized_path = file_path.replace('\\', '/')
                full_path = os.path.join(tmpdir, normalized_path)
                
                # Create parent directories
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                
                # Write file content
                with open(full_path, 'wb') as f:
                    f.write(content)
            
            # Step 2: Find entry file in priority order
            entry_path = self._find_entry_file(tmpdir, files.keys(), entry_files)
            
            if not entry_path:
                logger.warning(
                    f"No entry file found. Tried: {entry_files}. "
                    f"Available files: {list(files.keys())[:5]}..."
                )
                return None
            
            logger.debug(f"Using entry file: {entry_path}")
            
            # Step 3: Resolve using existing logic
            return self._resolve_spec(entry_path)
    
    def _find_entry_file(
        self,
        tmpdir: str,
        file_paths: Sequence[str],
        entry_files: Sequence[str],
    ) -> Optional[str]:
        """
        Find the main OpenAPI entry file from downloaded files.
        
        Searches in priority order of entry_files list.
        Handles both shallow (api/swagger/openapi.yaml) and deep paths.
        """
        # Build set of basenames for quick lookup
        path_list = list(file_paths)
        
        # Try each entry file in priority order
        for entry_name in entry_files:
            for file_path in path_list:
                # Check if this path ends with the entry file name
                normalized = file_path.replace('\\', '/')
                if normalized.endswith(f"/{entry_name}") or normalized == entry_name:
                    # Found it - return full path in tmpdir
                    return os.path.join(tmpdir, normalized)
        
        # Fallback: find any yaml/json at shallowest level
        # Sort by path depth (fewest slashes = shallowest)
        sorted_paths = sorted(path_list, key=lambda p: p.count('/'))
        for file_path in sorted_paths:
            if file_path.endswith(('.yaml', '.yml', '.json')):
                normalized = file_path.replace('\\', '/')
                return os.path.join(tmpdir, normalized)
        
        return None


    def load_spec_from_snapshot(self, snapshot_dir: str) -> Optional[Dict[str, Any]]:
        """
        Extracts execution path:
        1. Find OpenAPI file in dir.
        2. Attempt Loading (Standard -> Fallback).
        3. Return resolved dict or None.
        """
        # 1. Unzip artifacts.zip if present
        zip_path = os.path.join(snapshot_dir, "artifacts.zip")
        extract_dir = os.path.join(snapshot_dir, "extracted")
        
        if os.path.exists(zip_path):
            try:
                if os.path.exists(extract_dir): shutil.rmtree(extract_dir)
                os.makedirs(extract_dir)
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    zip_ref.extractall(extract_dir)
                
                # Update snapshot_root to the extracted folder (usually contains subfolder like 'rms-api-master-...')
                # We need to walk to find api/swagger
                search_root = extract_dir
            except zipfile.BadZipFile:
                print("  ❌ Bad Zip File in snapshot.")
                return None
        else:
            search_root = snapshot_dir

        # 2. Find OpenAPI File
        openapi_path = self._find_openapi_file(search_root)
        if not openapi_path:
            return None

        # 3. Load & Resolve
        return self._resolve_spec(openapi_path)

    def _find_openapi_file(self, root_dir: str) -> Optional[str]:
        """
        Finds OpenAPI/Swagger spec file in the snapshot directory.
        
        Priority order (higher = more preferred):
        1. api/swagger/openapi.yaml (standard location)
        2. openapi.yaml, openapi.json (explicit OpenAPI files)
        3. swagger.yaml, swagger.json (Swagger 2.0 files)
        
        Excludes hidden directories (.catalog, .gitlab, etc.) from fallback search.
        """
        # Collect all potential matches first, then pick best one
        found_files = {}  # priority -> file_path
        
        # Priority candidates (lower number = higher priority)
        # Note: api.yaml is used in older versions of the repo
        priority_patterns = [
            (1, "api/swagger/openapi.yaml"),
            (1, "api/swagger/openapi.json"),
            (1, "api/swagger/api.yaml"),      # Legacy naming
            (1, "api/swagger/api.json"),      # Legacy naming
            (2, "openapi.yaml"),
            (2, "openapi.json"),
            (3, "swagger.yaml"),
            (3, "swagger.json"),
            (4, "api.yaml"),                  # Generic fallback
            (4, "api.json"),
        ]
        
        for root, dirs, files in os.walk(root_dir):
            # Skip hidden directories and vendor
            dirs[:] = [d for d in dirs if not d.startswith('.') and d != 'vendor']
            
            for priority, pattern in priority_patterns:
                pattern_dir, pattern_file = os.path.split(pattern)
                
                if pattern_file not in files:
                    continue
                
                full_path = os.path.join(root, pattern_file)
                
                # If pattern has directory requirement, check it
                if pattern_dir:
                    norm_root = os.path.normpath(root)
                    norm_pattern_dir = os.path.normpath(pattern_dir)
                    if not norm_root.endswith(norm_pattern_dir):
                        continue
                
                # Store if better priority or not found yet
                if priority not in found_files:
                    found_files[priority] = full_path
        
        # Return highest priority match
        if found_files:
            best_priority = min(found_files.keys())
            return found_files[best_priority]
        
        return None

    def _resolve_spec(self, file_path: str) -> Optional[Dict[str, Any]]:
        """
        Resolves $ref references in OpenAPI spec using ResolvingParser.
        
        ResolvingParser with strict=False allows parsing specs that may have
        validation issues while still fully resolving all $ref references.
        
        ALWAYS cleans duplicate keys from all YAML files first, then runs Prance.
        This ensures consistent $ref resolution across all commits.
        """
        # ALWAYS clean duplicate keys from ALL yaml files first
        # This is critical for consistent $ref resolution - some commits have
        # duplicate keys in component files which cause Prance to fail and
        # fall back to unresolved YAML
        self._remove_duplicate_keys_from_file(file_path)
        
        # Now try ResolvingParser on cleaned files
        spec = None
        method_used = None
        
        try:
            parser = ResolvingParser(file_path, strict=False)
            spec = parser.specification
            method_used = "ResolvingParser"
        except Exception as e:
            error_msg = str(e)
            # Try with different backend
            try:
                parser = ResolvingParser(file_path, strict=False, backend='swagger-spec-validator')
                spec = parser.specification
                method_used = "ResolvingParser+swagger-spec-validator"
            except Exception:
                pass
        
        # Last resort: load without resolution
        if spec is None:
            specs = self._load_permissive_yaml(file_path)
            if specs:
                # Try to resolve refs manually with prance.util.resolver
                try:
                    from prance.util.resolver import RefResolver
                    resolver = RefResolver(specs, url=file_path)
                    spec = resolver.specs
                    method_used = "RefResolver (partial)"
                except Exception:
                    spec = specs
                    method_used = "raw YAML (NO resolution)"
        
        if spec is None:
            print(f"  ❌ Failed to parse spec")
            return None
        
        # Validate: check for unresolved $refs
        unresolved = self._count_unresolved_refs(spec)
        if unresolved > 0:
            print(f"  ⚠️ {method_used}: {unresolved} unresolved $refs remain!")
        
        return spec
    
    def _count_unresolved_refs(self, obj: Any, path: str = "") -> int:
        """Count unresolved $ref in spec"""
        count = 0
        if isinstance(obj, dict):
            if "$ref" in obj and len(obj) == 1:  # Pure $ref, not resolved
                count += 1
            for k, v in obj.items():
                if k != "$ref":
                    count += self._count_unresolved_refs(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                count += self._count_unresolved_refs(v, f"{path}[{i}]")
        return count

    def _remove_duplicate_keys_from_file(self, file_path: str) -> Optional[str]:
        """
        Removes duplicate keys from YAML file AND all referenced component files.
        Overwrites original files IN PLACE so Prance $ref resolution works correctly.
        Returns the original file_path (now cleaned) or None on failure.
        """
        try:
            from ruamel.yaml import YAML
            from io import StringIO
            
            y = YAML()
            y.allow_duplicate_keys = True
            y.preserve_quotes = True
            
            base_dir = os.path.dirname(file_path)
            
            # Find all yaml files in directory and subdirectories
            yaml_files = []
            for root, dirs, files in os.walk(base_dir):
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                for f in files:
                    if f.endswith(('.yaml', '.yml')):
                        yaml_files.append(os.path.join(root, f))
            
            # Clean each yaml file IN PLACE
            cleaned_count = 0
            for yaml_file in yaml_files:
                try:
                    with open(yaml_file, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    data = y.load(StringIO(content))
                    
                    output = StringIO()
                    y.dump(data, output)
                    cleaned_content = output.getvalue()
                    
                    # Overwrite original file with cleaned content
                    with open(yaml_file, 'w', encoding='utf-8') as f:
                        f.write(cleaned_content)
                    
                    cleaned_count += 1
                except Exception:
                    pass  # Skip files that can't be cleaned
            
            if cleaned_count > 0:
                return file_path  # Return original path, now cleaned
            
            return None
        except Exception as e:
            return None

    def _load_permissive_yaml(self, path: str) -> Any:
        errors = []
        
        # Method A: PyYAML safe_load
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except Exception as e:
            errors.append(str(e))
            
        # Method B: Ruamel (Explicit allow_duplicate_keys)
        try:
            from ruamel.yaml import YAML
            y = YAML()
            y.allow_duplicate_keys = True
            with open(path, 'r', encoding='utf-8') as f:
                return y.load(f)
        except Exception as e:
            errors.append(str(e))
            
        # Method C: PyYAML BaseLoader (Unsafe/String only - desperate measure)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return yaml.load(f, Loader=yaml.BaseLoader)
        except Exception as e:
            errors.append(str(e))
            
        print(f"  Details: {'; '.join(errors)}")
        return None
