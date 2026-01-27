"""
Shared constants for Confluence processing logic.
Centralizes lists of skipped keys, attributes, and prefixes to ensure consistency across modules.
"""

# Structural keys in OpenAPI schema to skip during path traversal/normalization
STRUCTURAL_KEYS = {
    'allOf', 'anyOf', 'oneOf', 'properties', 'items', 'schema',
    'content', 'application/json', 'requestBody', 'responses',
    'parameters', 'header', 'query', 'path', 'formData'
}

# Fields considered cosmetic or insignificant for change tracking
# Used by Publisher and history generation to filter out noise
COSMETIC_FIELDS = {
    'description', 'example', 'examples', 'title', 'deprecated',
    'summary', 'tags', 'operationId', 'externalDocs', 'x-order',
    'readOnly', 'writeOnly', 'x-nullable', 'x-omitempty',
    'default', 'enum'
}

# Validation keywords that shouldn't appear as field names usually
VALIDATION_KEYWORDS = {
    'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum',
    'minLength', 'maxLength', 'pattern',
    'minItems', 'maxItems', 'uniqueItems',
    'multipleOf', 'required', 'nullable'
}

# Technical keywords to ignore when extracting field names
IGNORED_KEYS = STRUCTURAL_KEYS | COSMETIC_FIELDS | VALIDATION_KEYWORDS | {
    'type', 'format', 'атрибуты полей'
}

SKIP_PREFIXES = {
    # OpenApi / Swagger structural prefixes
    'responses', 'requestBody', 'parameters', 'schema', 'content',
    'properties', 'items', 'allOf', 'anyOf', 'oneOf'
}
