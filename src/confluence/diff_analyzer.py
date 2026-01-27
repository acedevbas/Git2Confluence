"""
Diff Analyzer for OpenAPI Schema Changes.

Analyzes DeepDiff results and extracts field-level changes for documentation.

Usage:
    from src.confluence.diff_analyzer import DiffAnalyzer
    
    analyzer = DiffAnalyzer()
    changes = analyzer.extract_field_changes(diff, current_schema, previous_schema)
"""
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .schema_utils import flatten_schema_to_fields
from .constants import STRUCTURAL_KEYS, COSMETIC_FIELDS, VALIDATION_KEYWORDS, IGNORED_KEYS

logger = logging.getLogger(__name__)


# =============================================================================
# Path Parsing Utilities
# =============================================================================

# Schema internal elements to skip during path normalization
SKIP_SCHEMA_INTERNALS = STRUCTURAL_KEYS

# Schema attributes that should not be part of field path
SKIP_ATTRIBUTES = COSMETIC_FIELDS | VALIDATION_KEYWORDS | {'format', 'type', 'default', 'nullable'}

# OpenAPI keywords to skip when extracting field names
SKIP_OPENAPI_KEYS = IGNORED_KEYS | {'required'}


def parse_diff_path(path: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse DeepDiff path and determine section and field name.
    
    Examples:
        - root['requestBody']['properties']['unitId'] → ('requestBody', 'unitId')
        - root['parameters']['header']['Authorization'] → ('header', 'Authorization')
        - root['responses']['403'] → ('responses.error', '403')
        - root['responses']['200']['schema']['properties']['success'] → ('responses.200', 'success')
    
    Returns:
        (section, field_name) or (None, None) if unparseable
    """
    if not path:
        return (None, None)
    
    # Normalize path
    normalized = path.replace("root", "").replace("['", ".").replace("']", "").strip(".")
    parts = normalized.split(".")
    
    if not parts:
        return (None, None)
    
    section = None
    field_name = None
    
    if 'parameters' in parts or 'header' in parts:
        section = 'header'
        try:
            idx = parts.index('header') if 'header' in parts else -1
            if idx >= 0 and idx + 1 < len(parts):
                field_name = parts[idx + 1]
        except (ValueError, IndexError):
            pass
    
    elif 'requestBody' in parts:
        section = 'requestBody'
        field_name = _extract_field_name(parts)
    
    elif 'responses' in parts:
        try:
            idx = parts.index('responses')
            if idx + 1 < len(parts):
                status_code = parts[idx + 1]
                if status_code == '200':
                    section = 'responses.200'
                    field_name = _extract_field_name(parts)
                elif status_code.isdigit():
                    section = 'responses.error'
                    if idx + 2 >= len(parts) or parts[idx + 2] in ['schema', 'content', 'description']:
                        field_name = status_code
                    else:
                        field_name = _extract_field_name(parts)
        except (ValueError, IndexError):
            pass
    
    return (section, field_name)


def _extract_field_name(parts: List[str]) -> Optional[str]:
    """Extract last meaningful field name from path parts."""
    for part in reversed(parts):
        if part.isdigit():
            continue
        if part in SKIP_OPENAPI_KEYS:
            continue
        if part:
            return part
    return None


def extract_full_field_path(deepdiff_path: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract full field path from DeepDiff path for field_changes key.
    
    Examples:
        - root['requestBody']['properties']['products']['items']['properties']['guid']['format']
          → ('requestBody', 'products.guid')
        - root['responses']['200']['schema']['properties']['data']['properties']['id']
          → ('responses.200', 'data.id')
    
    Returns:
        (section, field_path) or (None, None) if unparseable
    """
    if not deepdiff_path:
        return (None, None)
    
    # Normalize path
    normalized = deepdiff_path.replace("root", "")
    normalized = normalized.replace("['", ".").replace("']", "")
    normalized = re.sub(r'\[\d+\]', '', normalized)  # Remove array indices
    normalized = normalized.strip(".")
    parts = normalized.split(".")
    
    if not parts:
        return (None, None)
    
    skip_keys = IGNORED_KEYS | VALIDATION_KEYWORDS | {'type', 'format', 'default', 'enum'}
    
    section = None
    field_parts = []
    
    if 'parameters' in parts or 'header' in parts:
        section = 'header'
        try:
            idx = parts.index('header') if 'header' in parts else parts.index('parameters') + 1
            for part in parts[idx+1:]:
                if part.isdigit() or part in skip_keys:
                    continue
                field_parts.append(part)
        except (ValueError, IndexError):
            pass
    
    elif 'requestBody' in parts:
        section = 'requestBody'
        try:
            idx = parts.index('requestBody')
            for part in parts[idx+1:]:
                if part.isdigit() or part in skip_keys:
                    continue
                field_parts.append(part)
        except ValueError:
            pass
    
    elif 'responses' in parts:
        try:
            idx = parts.index('responses')
            if idx + 1 < len(parts):
                status_code = parts[idx + 1]
                if status_code == '200':
                    section = 'responses.200'
                elif status_code.isdigit():
                    section = 'responses.error'
                
                for part in parts[idx+2:]:
                    if part.isdigit() or part in skip_keys:
                        continue
                    field_parts.append(part)
        except (ValueError, IndexError):
            pass
    
    field_path = '.'.join(field_parts) if field_parts else None
    return (section, field_path)


def normalize_path(deepdiff_path: str) -> str:
    """
    Normalize DeepDiff path to field path for table matching.
    
    Uses DeepDiff's parse_path for reliable path parsing.
    
    Example:
        "root['requestBody']['properties']['order']['properties']['guid']"
        → "requestBody.order.guid"
    """
    from deepdiff import parse_path
    
    try:
        keys = parse_path(deepdiff_path)
    except Exception:
        return ""
    
    if not keys:
        return ""
    
    result = []
    prev_key = None
    
    for key in keys:
        # Skip numeric indices (from allOf[0], items[1], etc.)
        if isinstance(key, int):
            prev_key = key
            continue
        
        str_key = str(key)
        
        # KEY CHANGE: Explicitly preserve major sections to ensure universal, self-describing paths
        # This prevents ambiguity (e.g. "400.error" vs "responses.400.error")
        if str_key in {'responses', 'requestBody', 'header', 'query', 'path', 'formData', 'parameters'}:
            result.append(str_key)
            prev_key = str_key
            continue

        # Check if it's a string HTTP status code
        if str_key.isdigit() and len(str_key) == 3:
            result.append(str_key)
            prev_key = str_key
            continue
        
        # Skip schema internals
        if str_key in SKIP_SCHEMA_INTERNALS:
            prev_key = str_key
            continue
        
        # Skip schema attributes
        if str_key in SKIP_ATTRIBUTES:
            prev_key = str_key
            continue
        
        # Skip 'required' as it's handled separately
        if str_key == 'required':
            prev_key = str_key
            continue
        
        result.append(str_key)
        prev_key = str_key
    
    return '.'.join(result)


def get_parent_field_path(deepdiff_path: str) -> str:
    """
    Get parent field path for a 'required' array change.
    
    Example:
        "root['requestBody']['properties']['products']['items']['required'][0]"
        → "requestBody.products"
    """
    path = re.sub(r"\['required'\].*$", "", deepdiff_path)
    return normalize_path(path)


# =============================================================================
# DiffAnalyzer Class
# =============================================================================

class DiffAnalyzer:
    """
    Analyzes DeepDiff results and extracts field-level changes.
    
    Handles:
    - Added/removed fields
    - Modified fields (required changes, format changes)
    - Structural schema changes (allOf reorganization)
    - False positive filtering
    """
    
    def extract_field_changes(
        self,
        diff: Dict[str, Any],
        current_schema: Optional[Dict[str, Any]] = None,
        previous_schema: Optional[Dict[str, Any]] = None
    ) -> Dict[str, str]:
        """
        Extract field changes from DeepDiff result.
        
        Args:
            diff: DeepDiff result dict
            current_schema: Current endpoint schema
            previous_schema: Previous endpoint schema
            
        Returns:
            Dict mapping field path to change type ('added', 'modified', 'removed')
        """
        changes = {}
        
        if not diff:
            return changes
        
        logger.info(f"[DIFF DEBUG] Keys: {list(diff.keys())}")
        
        # Process added items
        self._process_added_items(diff, changes, current_schema)
        
        # Process removed items
        self._process_removed_items(diff, changes, previous_schema)
        
        # Process changed values
        self._process_changed_values(diff, changes)
        
        # Process type changes
        if 'type_changes' in diff:
            for path in diff['type_changes']:
                normalized = normalize_path(path)
                if normalized:
                    changes[normalized] = 'modified'
        
        # Detect structural changes and extract real field changes
        if current_schema and previous_schema:
            structural = self._extract_structural_field_changes(
                diff, current_schema, previous_schema
            )
            for path, change_type in structural.items():
                if path not in changes:
                    changes[path] = change_type
                    logger.info(f"[STRUCTURAL] Added field change: {path} -> {change_type}")
        
        # Filter false positives
        if current_schema and changes:
            changes = self._filter_false_positive_changes(
                changes, current_schema, previous_schema
            )
        
        return changes
    
    def _process_added_items(
        self,
        diff: Dict[str, Any],
        changes: Dict[str, str],
        current_schema: Optional[Dict[str, Any]]
    ) -> None:
        """Process dictionary_item_added and iterable_item_added."""
        # Structural suffixes to skip
        structural_suffixes = (
            "['allOf']", "['anyOf']", "['oneOf']",
            "['properties']", "['items']",
            "['formData']", "['header']", "['query']", "['path']"
        )
        
        if 'dictionary_item_added' in diff:
            logger.info(f"[DIFF DEBUG] dictionary_item_added: {list(diff['dictionary_item_added'])}")
            
            for path in diff['dictionary_item_added']:
                # Skip structural elements
                if path.endswith(structural_suffixes):
                    logger.info(f"[SKIP] Structural addition: {path}")
                    continue
                
                # Format addition → mark as modified
                if path.endswith("['format']"):
                    normalized = normalize_path(path)
                    if normalized:
                        changes[normalized] = 'modified'
                    continue
                
                normalized = normalize_path(path)
                logger.info(f"[DIFF DEBUG] Added path: {path} -> normalized: {normalized}")
                
                if normalized:
                    if path.endswith("['required']"):
                        changes[normalized] = 'modified'
                        logger.info(f"[REQUIRED] Marked as modified: {normalized}")
                    else:
                        changes[normalized] = 'added'
                        
                        # Find nested fields under added complex objects
                        if current_schema:
                            # Universal check: normalized path should already contain section prefix now
                            is_response = normalized.startswith('responses.')
                            is_request_body = normalized.startswith('requestBody.')

                            if is_response or is_request_body:
                                nested = self._find_nested_fields_in_schema(current_schema, normalized)
                                if nested:
                                    logger.info(f"[NESTED] Found {len(nested)} nested fields under {normalized}")
                                    for nested_path in nested:
                                        if nested_path not in changes:
                                            changes[nested_path] = 'added'
        
        # Handle iterable_item_added (for 'required' arrays)
        if 'iterable_item_added' in diff:
            for path, value in diff['iterable_item_added'].items():
                if "'required']" in path:
                    parent = get_parent_field_path(path)
                    if isinstance(value, str):
                        field_path = f"{parent}.{value}" if parent else value
                        if field_path not in changes:
                            changes[field_path] = 'modified'
                else:
                    normalized = normalize_path(path)
                    if normalized:
                        changes[normalized] = 'added'
    
    def _process_removed_items(
        self,
        diff: Dict[str, Any],
        changes: Dict[str, str],
        previous_schema: Optional[Dict[str, Any]] = None
    ) -> None:
        """Process dictionary_item_removed and iterable_item_removed."""
        structural_suffixes = (
            "['allOf']", "['anyOf']", "['oneOf']",
            "['properties']", "['items']", "['required']",
            "['formData']", "['header']", "['query']", "['path']"
        )
        
        if 'dictionary_item_removed' in diff:
            for path in diff['dictionary_item_removed']:
                if path.endswith(structural_suffixes):
                    logger.info(f"[SKIP] Structural removal: {path}")
                    continue
                
                # Format removal → mark as modified
                if path.endswith("['format']"):
                    normalized = normalize_path(path)
                    if normalized:
                        changes[normalized] = 'modified'
                    continue
                
                normalized = normalize_path(path)
                if normalized:
                    if path.endswith("['required']"):
                        changes[normalized] = 'modified'
                    else:
                        changes[normalized] = 'removed'
                        
                        # Recursively mark nested fields as removed
                        if previous_schema:
                            # Universal check: normalized path should already contain section prefix now
                            is_response = normalized.startswith('responses.')
                            is_request_body = normalized.startswith('requestBody.')

                            if is_response or is_request_body:
                                nested = self._find_nested_fields_in_schema(previous_schema, normalized)
                                if nested:
                                    logger.info(f"[NESTED REMOVED] Found {len(nested)} nested fields under {normalized}")
                                    for nested_path in nested:
                                        if nested_path not in changes:
                                            changes[nested_path] = 'removed'
        
        if 'iterable_item_removed' in diff:
            for path, value in diff['iterable_item_removed'].items():
                if "'required']" in path:
                    parent = get_parent_field_path(path)
                    if isinstance(value, str):
                        field_path = f"{parent}.{value}" if parent else value
                        if field_path not in changes:
                            changes[field_path] = 'modified'
                elif "'allOf']" in path or "'anyOf']" in path or "'oneOf']" in path:
                    continue  # Skip composition changes
                else:
                    normalized = normalize_path(path)
                    if normalized:
                        changes[normalized] = 'removed'
    
    def _process_changed_values(
        self,
        diff: Dict[str, Any],
        changes: Dict[str, str]
    ) -> None:
        """Process values_changed entries."""
        if 'values_changed' not in diff:
            return
        
        logger.info(f"[DIFF DEBUG] values_changed: {list(diff['values_changed'].keys())}")
        
        for path, change_info in diff['values_changed'].items():
            # Format value changed
            if path.endswith("['format']"):
                normalized = normalize_path(path)
                if normalized:
                    changes[normalized] = 'modified'
                continue
            
            # Skip insignificant changes
            if self._is_insignificant_change(path, change_info):
                logger.info(f"[SKIP] Insignificant change: {path}")
                continue
            
            # Required array changes
            if "'required']" in path:
                self._extract_required_changes(path, change_info, changes)
            else:
                # Check for nested required changes in objects
                old_value = change_info.get('old_value', {})
                new_value = change_info.get('new_value', {})
                
                if isinstance(old_value, dict) and isinstance(new_value, dict):
                    old_required = set(old_value.get('required', []))
                    new_required = set(new_value.get('required', []))
                    old_props = set(old_value.get('properties', {}).keys())
                    
                    if old_required != new_required:
                        parent = normalize_path(path)
                        for field in old_required - new_required:
                            if field in old_props:
                                field_path = f"{parent}.{field}" if parent else field
                                if field_path not in changes:
                                    changes[field_path] = 'modified'
                        for field in new_required - old_required:
                            if field in old_props:
                                field_path = f"{parent}.{field}" if parent else field
                                if field_path not in changes:
                                    changes[field_path] = 'modified'
                else:
                    normalized = normalize_path(path)
                    if normalized:
                        changes[normalized] = 'modified'
    
    def _extract_required_changes(
        self,
        path: str,
        change_info: Dict[str, Any],
        changes: Dict[str, str]
    ) -> None:
        """Extract field names affected by 'required' array/boolean changes."""
        old_value = change_info.get('old_value')
        new_value = change_info.get('new_value')
        parent = get_parent_field_path(path)
        
        # Boolean required change (header/query params)
        if isinstance(old_value, bool) and isinstance(new_value, bool):
            if old_value != new_value and parent:
                if parent not in changes:
                    changes[parent] = 'modified'
            return
        
        # Array required change
        if isinstance(old_value, list) and isinstance(new_value, list):
            old_set = set(old_value)
            new_set = set(new_value)
            
            for field in old_set - new_set:
                field_path = f"{parent}.{field}" if parent else field
                if field_path not in changes:
                    changes[field_path] = 'modified'
            
            for field in new_set - old_set:
                field_path = f"{parent}.{field}" if parent else field
                if field_path not in changes:
                    changes[field_path] = 'modified'
    
    def _is_insignificant_change(self, path: str, change_info: Dict[str, Any]) -> bool:
        """Check if a value change is insignificant and should be skipped."""
        old_value = change_info.get('old_value')
        new_value = change_info.get('new_value')
        
        if "'format']" in path:
            integer_formats = {'int8', 'uint8', 'int16', 'uint16', 'int32', 'uint32', 'int64', 'uint64', 'integer'}
            float_formats = {'float', 'double', 'number'}
            semantic_formats = {'uuid', 'date', 'date-time', 'time', 'email', 'uri', 'hostname', 'ipv4', 'ipv6'}
            
            old_str = str(old_value).lower() if old_value else ''
            new_str = str(new_value).lower() if new_value else ''
            
            # Semantic format changes are significant
            if (old_str in semantic_formats) != (new_str in semantic_formats):
                return False
            
            if old_str in integer_formats and new_str in integer_formats:
                return True
            if old_str in float_formats and new_str in float_formats:
                return True
        
        if "'type']" in path:
            similar_types = [{'integer', 'number'}]
            old_str = str(old_value).lower() if old_value else ''
            new_str = str(new_value).lower() if new_value else ''
            
            for group in similar_types:
                if old_str in group and new_str in group:
                    return True
        
        return False
    
    def _find_nested_fields_in_schema(
        self,
        schema: Dict[str, Any],
        parent_field_path: str
    ) -> List[str]:
        """Find all nested fields under a given parent field path."""
        nested_fields = []
        
        # Parse parent path to determine section
        parts = parent_field_path.split('.')
        if not parts:
            return nested_fields
        
        section = parts[0]
        
        # Determine schema section and remaining path logic
        target_schema = {}
        remaining_path = ''
        
        if section == 'responses':
            if len(parts) > 1 and parts[1].isdigit():
                response_code = parts[1]
                response = schema.get('responses', {}).get(response_code, {})
                target_schema = response.get('schema', {})
                remaining_path = '.'.join(parts[2:]) if len(parts) > 2 else ''
        elif section.isdigit() and len(section) == 3:
            # Handle normalized paths starting with status code (e.g. 400.errors)
            response_code = section
            response = schema.get('responses', {}).get(response_code, {})
            target_schema = response.get('schema', {})
            remaining_path = '.'.join(parts[1:]) if len(parts) > 1 else ''
        elif section == 'requestBody':
            target_schema = schema.get('requestBody', {})
            remaining_path = '.'.join(parts[1:]) if len(parts) > 1 else ''
        else:
            # Fallback: assume requestBody with implicit prefix
            target_schema = schema.get('requestBody', {})
            remaining_path = parent_field_path

        if not target_schema:
            return nested_fields
        
        # Flatten and filter by parent path
        fields = flatten_schema_to_fields(target_schema)
        for field in fields:
            full_path = field.get('full_path', '')
            
            # If remaining_path is empty (we removed the whole section root?), take everything?
            # Usually parent_field_path points to a FIELD (e.g. 'errors'), so remaining_path is 'errors'.
            
            if full_path and full_path.startswith(remaining_path) and full_path != remaining_path:
                # Proper path construction:
                # parent_field_path: 400.errors
                # full_path: errors.message
                # remaining_path: errors
                # suffix: .message
                # Result: 400.errors.message
                
                suffix = full_path[len(remaining_path):].strip('.')
                if suffix:
                    nested_fields.append(f"{parent_field_path}.{suffix}")
        
        return nested_fields
    
    def _extract_structural_field_changes(
        self,
        diff: Dict[str, Any],
        current_schema: Dict[str, Any],
        previous_schema: Dict[str, Any]
    ) -> Dict[str, str]:
        """Extract real field changes when schema structure changes."""
        changes = {}
        
        has_structural_change = False
        affected_prefix = None
        
        # Check dictionary_item_added
        for path in diff.get('dictionary_item_added', []):
            if path.endswith("['allOf']") or path.endswith("['properties']"):
                has_structural_change = True
                if 'requestBody' in path:
                    affected_prefix = 'requestBody'
                elif 'responses' in path and "'200']" in path:
                    affected_prefix = 'responses.200'
                break
        
        # Check dictionary_item_removed
        if not has_structural_change:
            for path in diff.get('dictionary_item_removed', []):
                if path.endswith("['allOf']") or path.endswith("['properties']"):
                    has_structural_change = True
                    if 'requestBody' in path:
                        affected_prefix = 'requestBody'
                    elif 'responses' in path and "'200']" in path:
                        affected_prefix = 'responses.200'
                    break
        
        # Check values_changed
        if not has_structural_change:
            for path in diff.get('values_changed', {}):
                if "'properties']" in path and "'allOf']" in path:
                    has_structural_change = True
                    if 'requestBody' in path:
                        affected_prefix = 'requestBody'
                    elif 'responses' in path and "'200']" in path:
                        affected_prefix = 'responses.200'
                    break
        
        if not has_structural_change or not affected_prefix:
            return changes
        
        logger.info(f"[STRUCTURAL] Detected structural change in {affected_prefix}")
        
        # Get fields from both schemas
        if affected_prefix == 'requestBody':
            current_fields = flatten_schema_to_fields(current_schema.get('requestBody', {}))
            previous_fields = flatten_schema_to_fields(previous_schema.get('requestBody', {}))
        else:
            current_resp = current_schema.get('responses', {}).get('200', {})
            previous_resp = previous_schema.get('responses', {}).get('200', {})
            current_fields = flatten_schema_to_fields(current_resp.get('schema', {}))
            previous_fields = flatten_schema_to_fields(previous_resp.get('schema', {}))
        
        current_paths = {f.get('full_path', '') for f in current_fields if f.get('full_path')}
        previous_paths = {f.get('full_path', '') for f in previous_fields if f.get('full_path')}
        
        # Find added fields
        for path in current_paths - previous_paths:
            changes[f"{affected_prefix}.{path}"] = 'added'
        
        # Find removed fields
        for path in previous_paths - current_paths:
            changes[f"{affected_prefix}.{path}"] = 'removed'
        
        return changes
    
    def _filter_false_positive_changes(
        self,
        changes: Dict[str, str],
        current_schema: Dict[str, Any],
        previous_schema: Optional[Dict[str, Any]]
    ) -> Dict[str, str]:
        """Filter out false positive field changes caused by schema restructuring."""
        current_full_paths: Set[str] = set()
        previous_full_paths: Set[str] = set()
        
        # Collect current paths
        responses = current_schema.get('responses', {})
        for code in ['200', '201']:
            if code in responses:
                fields = flatten_schema_to_fields(responses[code].get('schema', {}))
                for f in fields:
                    full_path = f.get('full_path', '')
                    if full_path:
                        current_full_paths.add(f"responses.{code}.{full_path}")
        
        if 'requestBody' in current_schema:
            fields = flatten_schema_to_fields(current_schema['requestBody'])
            for f in fields:
                full_path = f.get('full_path', '')
                if full_path:
                    current_full_paths.add(f"requestBody.{full_path}")
        
        # Collect previous paths
        if previous_schema:
            prev_responses = previous_schema.get('responses', {})
            for code in ['200', '201']:
                if code in prev_responses:
                    fields = flatten_schema_to_fields(prev_responses[code].get('schema', {}))
                    for f in fields:
                        full_path = f.get('full_path', '')
                        if full_path:
                            previous_full_paths.add(f"responses.{code}.{full_path}")
            
            if 'requestBody' in previous_schema:
                fields = flatten_schema_to_fields(previous_schema['requestBody'])
                for f in fields:
                    full_path = f.get('full_path', '')
                    if full_path:
                        previous_full_paths.add(f"requestBody.{full_path}")
        
        # Filter
        filtered = {}
        for field_path, change_type in changes.items():
            # Keep response code changes
            if field_path.startswith('responses.') and field_path.count('.') == 1:
                filtered[field_path] = change_type
                continue
            
            if change_type == 'removed' and field_path in current_full_paths:
                logger.info(f"[FALSE-POSITIVE] Skipping 'removed' {field_path}")
                continue
            elif change_type == 'added' and field_path in previous_full_paths:
                logger.info(f"[FALSE-POSITIVE] Skipping 'added' {field_path}")
                continue
            
            filtered[field_path] = change_type
        
        if len(filtered) != len(changes):
            logger.info(f"[FALSE-POSITIVE] Filtered {len(changes) - len(filtered)} changes")
        
        return filtered
