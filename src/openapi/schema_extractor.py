import datetime as _dt
from typing import Dict, Any, List


def _json_safe(obj: Any) -> Any:
    """
    Normalize an extracted schema into a canonical, JSON-serializable form.

    Real-world OpenAPI/Swagger specs (parsed from YAML) contain values that
    break downstream JSON serialization and string-key assumptions:
      * date/date-time ``example`` values parse into Python datetime objects
        (not JSON-serializable) -> converted to ISO strings.
      * unquoted response codes (``200:``) parse as ints -> callers do
        ``code.startswith('4')`` and ``responses.get('200')`` which assume
        strings; keys are coerced to str.

    Normalizing here, at the extraction boundary, keeps every consumer
    (diffing, hashing, Confluence rendering) simple and correct.
    """
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (_dt.datetime, _dt.date, _dt.time)):
        return obj.isoformat()
    return obj


class SchemaExtractor:
    @staticmethod
    def extract(spec: Dict[str, Any], method: str, path: str) -> Dict[str, Any]:
        """
        Extracts a simplified 'Effective Schema' for a specific endpoint.
        Returns a dictionary containing headers, query params, request body, and responses.
        """
        method = method.lower()
        if path not in spec.get('paths', {}):
            return None
        
        path_item = spec['paths'][path]
        if method not in path_item:
            return None
            
        operation = path_item[method]
        
        snapshot = {
            "summary": operation.get('summary', ''),
            "description": operation.get('description', ''),
            "parameters": {
                "header": {},
                "query": {},
                "path": {},
                "formData": {}  # Support for Swagger 2.0 formData parameters
            },
            "requestBody": {},
            "responses": {}
        }
        
        # 1. Parameters (Path, Query, Header, Body)
        # Merge path-level and operation-level parameters
        all_params = path_item.get('parameters', []) + operation.get('parameters', [])
        
        for param in all_params:
            # Prance should have resolved refs, so we expect dicts
            if '$ref' in param:
                continue
                
            name = param.get('name')
            in_ = param.get('in')
            schema = param.get('schema', {})
            
            # Handle Swagger 2.0 "in: body"
            if in_ == 'body':
                # Treat as requestBody
                # The schema is directly in 'schema' field of the param
                snapshot['requestBody'] = SchemaExtractor._simplify_schema(schema)
                continue

            # Simplify schema for display
            # For non-body params in Swagger 2.0, type/format are direct fields, not in 'schema'
            if in_ in ['query', 'header', 'path', 'formData']:
                if 'schema' not in param:
                    # Swagger 2.0 style: type, format, items, etc. are at param level
                    # We construct a fake schema object to simplify
                    param_schema = {k: v for k, v in param.items() if k in ['type', 'format', 'items', 'enum', 'default', 'description']}
                    simple_schema = SchemaExtractor._simplify_schema(param_schema)
                else:
                    # OpenAPI 3.0 style
                    simple_schema = SchemaExtractor._simplify_schema(schema)
                
                if param.get('required'):
                    simple_schema['required'] = True
                    
                if in_ in snapshot['parameters']:
                    snapshot['parameters'][in_][name] = simple_schema

        # 2. Request Body (OpenAPI 3.0)
        if 'requestBody' in operation:
            rb = operation['requestBody']
            content = rb.get('content', {})
            if 'application/json' in content:
                schema = content['application/json'].get('schema', {})
                snapshot['requestBody'] = SchemaExtractor._simplify_schema(schema)
            elif 'multipart/form-data' in content:
                 schema = content['multipart/form-data'].get('schema', {})
                 snapshot['requestBody'] = SchemaExtractor._simplify_schema(schema)
                 snapshot['requestBody']['_content_type'] = 'multipart/form-data'

        # 3. Responses
        for code, response in operation.get('responses', {}).items():
            resp_snapshot = {"description": response.get('description', '')}
            
            # Swagger 2.0: schema is direct
            if 'schema' in response:
                resp_snapshot['schema'] = SchemaExtractor._simplify_schema(response['schema'])
            # OpenAPI 3.0: content.application/json.schema
            elif 'content' in response:
                content = response.get('content', {})
                if 'application/json' in content:
                    schema = content['application/json'].get('schema', {})
                    resp_snapshot['schema'] = SchemaExtractor._simplify_schema(schema)
            
            snapshot['responses'][code] = resp_snapshot

        # Canonicalize: string response-code keys + JSON-safe values
        # (datetime examples -> ISO strings). See _json_safe docstring.
        return _json_safe(snapshot)
    
    @staticmethod
    def extract_all(spec: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """
        Extract schemas for ALL endpoints in spec.
        
        Used during cache warming for pre-computing endpoint history.
        Much more efficient than calling extract() multiple times.
        
        Args:
            spec: Full OpenAPI spec
            
        Returns:
            Dict mapping endpoint key to schema:
            {
                "POST /orders": {...schema...},
                "GET /orders/{id}": {...schema...},
                ...
            }
        """
        endpoints = {}
        
        paths = spec.get('paths', {})
        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            
            # Check each HTTP method
            for method in ['get', 'post', 'put', 'patch', 'delete', 'options', 'head']:
                if method in path_item:
                    endpoint_key = f"{method.upper()} {path}"
                    schema = SchemaExtractor.extract(spec, method, path)
                    if schema:
                        endpoints[endpoint_key] = schema
        
        return endpoints

    @staticmethod
    def _simplify_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
        """
        Recursive function to prepare JSON Schema for display.
        Retains descriptions, types, and structure.
        """
        if not schema:
            return {}
            
        # Create a copy to avoid modifying original
        simple = schema.copy()
        
        # We want to keep most fields, but maybe clean up internal technical keys if any.
        # For now, let's keep everything relevant for documentation.
        
        # Recurse into nested structures
        if 'items' in simple:
            simple['items'] = SchemaExtractor._simplify_schema(simple['items'])
            
        if 'properties' in simple:
            # Build a new dict: 'properties' is shared with the source schema
            # after the shallow copy above, and must not be mutated in place
            simple['properties'] = {
                prop_name: SchemaExtractor._simplify_schema(prop_schema)
                for prop_name, prop_schema in simple['properties'].items()
            }
                
        if 'additionalProperties' in simple and isinstance(simple['additionalProperties'], dict):
             simple['additionalProperties'] = SchemaExtractor._simplify_schema(simple['additionalProperties'])

        # Handle allOf/oneOf/anyOf
        for key in ['allOf', 'oneOf', 'anyOf']:
            if key in simple:
                simple[key] = [SchemaExtractor._simplify_schema(s) for s in simple[key]]

        return simple

if __name__ == "__main__":
    # Test
    spec = {
        "paths": {
            "/test": {
                "post": {
                    "parameters": [{"name": "id", "in": "query", "schema": {"type": "integer"}}],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "tags": {"type": "array", "items": {"type": "string"}}
                                    },
                                    "required": ["name"]
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"status": {"type": "string"}}}
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    import json
    print(json.dumps(SchemaExtractor.extract(spec, "post", "/test"), indent=2))
