"""
Schema Utilities for OpenAPI/Confluence integration.

Functions for analyzing, flattening, and generating examples from OpenAPI schemas.

Usage:
    from src.confluence.schema_utils import flatten_schema_to_fields, is_schema_poor
    
    fields = flatten_schema_to_fields(schema)
    is_poor = is_schema_poor(endpoint_schema)
"""
from typing import Any, Dict, List, Optional
from datetime import datetime, date


# =============================================================================
# Schema Analysis
# =============================================================================

def is_schema_poor(endpoint_schema: Dict[str, Any]) -> bool:
    """
    Check if endpoint schema is "poor" - truly useless/incomplete.
    
    Schema is "poor" ONLY if ALL of these are true:
    1. Response has no details (result without structure)
    2. No meaningful parameters
    3. No detailed requestBody
    
    If endpoint has ANY useful structure (params/body/response), it's NOT poor!
    
    Args:
        endpoint_schema: Full endpoint schema dict
        
    Returns:
        True ONLY for truly incomplete schemas (old legacy format)
    """
    if not endpoint_schema:
        return True
    
    # Check 1: Does endpoint have meaningful parameters?
    parameters = endpoint_schema.get('parameters', {})
    if parameters:
        for param_type in ['header', 'query', 'path']:
            params = parameters.get(param_type, {})
            if params and isinstance(params, dict) and len(params) > 0:
                return False  # Has parameters - NOT poor!
    
    # Check 2: Does endpoint have detailed requestBody?
    request_body = endpoint_schema.get('requestBody', {})
    if request_body and isinstance(request_body, dict):
        body_props = request_body.get('properties', {})
        if body_props and isinstance(body_props, dict) and len(body_props) > 0:
            return False  # Has requestBody - NOT poor!
    
    # Check 3: Does response have detailed structure?
    responses = endpoint_schema.get('responses', {})
    success_response = responses.get('200', responses.get('201', {}))
    success_schema = success_response.get('schema', {})
    
    if not success_schema:
        return True  # No response and no params/body - truly poor
    
    # Check if response uses allOf - need to merge all items!
    if 'allOf' in success_schema:
        merged_result = {}
        for item in success_schema.get('allOf', []):
            props = item.get('properties', {})
            result = props.get('result', {})
            
            if result:
                if '$ref' in result or 'items' in result or 'properties' in result or 'allOf' in result:
                    return False  # Has detailed result - NOT poor!
                
                for key in ['type', 'properties', 'items', 'allOf']:
                    if key in result:
                        merged_result[key] = result[key]
        
        if merged_result.get('properties') or merged_result.get('items') or merged_result.get('allOf'):
            return False  # Has details across allOf - NOT poor!
    
    # Check direct response properties
    props = success_schema.get('properties', {})
    result = props.get('result', {})
    
    if result:
        result_type = result.get('type', '')
        if result_type == 'object':
            if 'properties' in result or 'allOf' in result or '$ref' in result:
                return False  # Detailed result - NOT poor!
        elif result_type == 'array' and 'items' in result:
            return False  # Detailed array - NOT poor!
    
    return True  # No params, no body, no response details - truly poor


# =============================================================================
# Schema Flattening
# =============================================================================

def flatten_schema_to_fields(
    schema: Dict[str, Any],
    definitions: Optional[Dict[str, Any]] = None,
    parent_key: str = "",
    level: int = 0,
    required_fields: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """
    Recursively extract fields from OpenAPI schema for table display.
    
    Args:
        schema: OpenAPI schema dict
        definitions: Schema definitions for $ref resolution
        parent_key: Parent path for nested fields
        level: Nesting level (0 = root)
        required_fields: List of required field names
        
    Returns:
        List of field dicts with: key, nested_key, type, description, required, full_path, level
    """
    definitions = definitions or {}
    required_fields = required_fields or []
    fields = []
    
    if not schema:
        return fields
    
    # Resolve $ref
    if '$ref' in schema:
        ref = schema['$ref']
        ref_name = ref.split('/')[-1]
        if ref_name in definitions:
            schema = definitions[ref_name]
        else:
            return fields
    
    # Handle allOf - merge properties from all items
    merged_properties = {}
    merged_required = list(required_fields)
    
    if 'allOf' in schema:
        for sub_schema in schema['allOf']:
            sub_props = sub_schema.get('properties', {})
            for prop_name, prop_schema in sub_props.items():
                if prop_name not in merged_properties:
                    merged_properties[prop_name] = prop_schema
                else:
                    # Merge: prefer the one with more detail
                    existing = merged_properties[prop_name]
                    if 'items' in prop_schema and 'items' not in existing:
                        merged_properties[prop_name] = prop_schema
                    elif 'properties' in prop_schema and 'properties' not in existing:
                        merged_properties[prop_name] = prop_schema
                    elif 'allOf' in prop_schema and 'allOf' not in existing:
                        merged_properties[prop_name] = prop_schema
            # Collect required
            sub_required = sub_schema.get('required', [])
            for req in sub_required:
                if req not in merged_required:
                    merged_required.append(req)
    
    # Add properties directly on schema
    direct_properties = schema.get('properties', {})
    for prop_name, prop_schema in direct_properties.items():
        if prop_name not in merged_properties:
            merged_properties[prop_name] = prop_schema
        else:
            existing = merged_properties[prop_name]
            if 'items' in prop_schema and 'items' not in existing:
                merged_properties[prop_name] = prop_schema
            elif 'properties' in prop_schema and 'properties' not in existing:
                merged_properties[prop_name] = prop_schema
    
    # Add direct required
    direct_required = schema.get('required', [])
    for req in direct_required:
        if req not in merged_required:
            merged_required.append(req)
    
    schema_type = schema.get('type', 'object')
    properties = merged_properties
    schema_required = merged_required
    
    # Handle array
    if schema_type == 'array' and 'items' in schema:
        items = schema['items']
        fields.extend(flatten_schema_to_fields(
            items, definitions, parent_key, level, schema_required
        ))
        return fields
    
    # Handle object properties
    for prop_name, prop_schema in properties.items():
        full_path = f"{parent_key}.{prop_name}" if parent_key else prop_name
        is_required = prop_name in schema_required or prop_name in required_fields
        
        # Resolve $ref in property
        if '$ref' in prop_schema:
            ref = prop_schema['$ref']
            ref_name = ref.split('/')[-1]
            if ref_name in definitions:
                prop_schema = {**definitions[ref_name], **{k: v for k, v in prop_schema.items() if k != '$ref'}}
        
        prop_type = prop_schema.get('type', 'object')
        prop_format = prop_schema.get('format', '')
        
        # Format type string
        type_str = prop_type
        if prop_format:
            type_str = f"{prop_type} ({prop_format})"
        
        if prop_type == 'object' and 'properties' in prop_schema:
            type_str = 'object'
        elif prop_type == 'array':
            type_str = 'array'
        
        # Determine key columns based on level
        if level == 0:
            key = prop_name
            nested_key = ""
        else:
            key = ""
            nested_key = prop_name
        
        fields.append({
            'key': key,
            'nested_key': nested_key,
            'type': type_str,
            'description': prop_schema.get('description', ''),
            'required': is_required,
            'full_path': full_path,
            'level': level
        })
        
        # Recurse into nested objects/arrays
        has_nested_structure = 'properties' in prop_schema or 'allOf' in prop_schema
        if prop_type == 'object' and has_nested_structure:
            nested_required = prop_schema.get('required', [])
            fields.extend(flatten_schema_to_fields(
                prop_schema, definitions, full_path, level + 1, nested_required
            ))
        elif prop_type == 'array' and 'items' in prop_schema:
            items = prop_schema['items']
            nested_required = items.get('required', [])
            fields.extend(flatten_schema_to_fields(
                items, definitions, full_path, level + 1, nested_required
            ))
    
    return fields


# =============================================================================
# Example Generation
# =============================================================================

def _make_serializable(obj: Any) -> Any:
    """Helper to convert objects to JSON-serializable format."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, float) and obj.is_integer():
        return int(obj)
    if isinstance(obj, dict):
        return {k: _make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_make_serializable(x) for x in obj]
    return obj

def generate_example_from_schema(
    schema: Dict[str, Any],
    definitions: Optional[Dict[str, Any]] = None
) -> Any:
    """
    Generate example JSON from OpenAPI schema.
    
    Args:
        schema: OpenAPI schema dict
        definitions: Schema definitions for $ref resolution
        
    Returns:
        Example value (dict, list, or primitive)
    """
    definitions = definitions or {}
    
    if not schema:
        return {}
    
    # Handle example in schema
    if 'example' in schema:
        return _make_serializable(schema['example'])
    
    # Resolve $ref
    if '$ref' in schema:
        ref = schema['$ref']
        ref_name = ref.split('/')[-1]
        if ref_name in definitions:
            return generate_example_from_schema(definitions[ref_name], definitions)
        return {}
    
    # Handle allOf
    if 'allOf' in schema:
        result = {}
        for sub_schema in schema['allOf']:
            sub_example = generate_example_from_schema(sub_schema, definitions)
            if isinstance(sub_example, dict):
                result.update(sub_example)
        return result
    
    schema_type = schema.get('type', 'object')
    
    if schema_type == 'object':
        result = {}
        for prop_name, prop_schema in schema.get('properties', {}).items():
            result[prop_name] = generate_example_from_schema(prop_schema, definitions)
        return result
    
    elif schema_type == 'array':
        items = schema.get('items', {})
        return [generate_example_from_schema(items, definitions)]
    
    elif schema_type == 'string':
        fmt = schema.get('format', '')
        if fmt == 'uuid':
            return "00000000-0000-0000-0000-000000000000"
        elif fmt == 'date':
            return "2024-01-01"
        elif fmt == 'date-time':
            return "2024-01-01T00:00:00Z"
        return "string"
    
    elif schema_type == 'integer':
        return 0
    
    elif schema_type == 'number':
        return 0.0
    
    elif schema_type == 'boolean':
        return True
    
    return {}


# =============================================================================
# Field Merging Utilities
# =============================================================================

def merge_fields_with_removed(
    current_fields: List[Dict[str, Any]],
    previous_fields: List[Dict[str, Any]],
    field_changes: Dict[str, str],
    prefix: str
) -> List[Dict[str, Any]]:
    """
    Merge current fields with removed fields from previous schema.
    Marks removed fields with 'removed' change type.
    
    Args:
        current_fields: Fields from current schema
        previous_fields: Fields from previous schema
        field_changes: Dict to update with removed field info
        prefix: Path prefix for field_changes keys
        
    Returns:
        Merged list of fields with removed fields added
    """
    current_paths = {f.get('full_path', '') for f in current_fields}
    
    result = list(current_fields)
    for prev_field in previous_fields:
        prev_path = prev_field.get('full_path', '')
        if prev_path and prev_path not in current_paths:
            # This field was removed
            removed_field = prev_field.copy()
            # Red cell highlighting indicates removal - no prefix needed
            result.append(removed_field)
            # Mark as removed in field_changes
            field_changes[f"{prefix}.{prev_path}"] = 'removed'
            field_changes[prev_path] = 'removed'
    
    return result
