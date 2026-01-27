"""
Table Generators for Confluence Documentation.

Generates HTML tables for headers, form data, and field tables with change highlighting.

Usage:
    from src.confluence.tables import generate_fields_table, generate_header_params_table
    
    table_html = generate_fields_table(fields, field_changes, prefix="requestBody")
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# Constants - Change Highlight Colors
# =============================================================================

COLOR_ADDED = "rgb(212,237,218)"      # Green
COLOR_MODIFIED = "rgb(255,243,205)"    # Yellow
COLOR_REMOVED = "rgb(248,215,218)"     # Red


# =============================================================================
# Table Generators
# =============================================================================

def generate_header_params_table(
    parameters: List[Dict[str, Any]],
    field_changes: Optional[Dict[str, str]] = None
) -> str:
    """
    Generate header parameters table with change highlighting.
    
    Args:
        parameters: List of params [{name, type, required, description}]
        field_changes: {param_name: 'added'|'modified'|'removed'}
        
    Returns:
        Confluence Storage Format HTML for the table
    """
    field_changes = field_changes or {}
    rows = []
    
    for param in parameters:
        name = param.get('name', '')
        param_type = param.get('type', 'string')
        required = 'Да' if param.get('required') else 'Нет'
        description = param.get('description', '')
        
        # Determine row style based on changes
        change_type = field_changes.get(f"header.{name}") or field_changes.get(name)
        row_style = _get_row_style(change_type)
        
        rows.append(f'''<tr{row_style}>
<td>{name}</td>
<td>{param_type}</td>
<td>{required}</td>
<td>{description}</td>
</tr>''')
    
    if not rows:
        rows.append('<tr class=""><td colspan="4">Нет параметров</td></tr>')
    
    return f'''<table class="wrapped" data-mce-resize="false">
<tbody class="">
<tr class="">
<th>Параметр</th>
<th>Тип данных</th>
<th>Обязательный?</th>
<th>Описание</th>
</tr>
{chr(10).join(rows)}
</tbody>
</table>'''


def generate_formdata_params_table(
    parameters: List[Dict[str, Any]],
    field_changes: Optional[Dict[str, str]] = None
) -> str:
    """
    Generate formData parameters table with change highlighting.
    
    Args:
        parameters: List of params [{name, type, required, description}]
        field_changes: {param_name: 'added'|'modified'|'removed'}
        
    Returns:
        Confluence Storage Format HTML for the table, or empty string if no params
    """
    field_changes = field_changes or {}
    rows = []
    
    for param in parameters:
        name = param.get('name', '')
        param_type = param.get('type', 'string')
        required = 'Да' if param.get('required') else 'Нет'
        description = param.get('description', '')
        
        change_type = field_changes.get(f"formData.{name}") or field_changes.get(name)
        row_style = _get_row_style(change_type)
        
        rows.append(f'''<tr{row_style}>
<td>{name}</td>
<td>{param_type}</td>
<td>{required}</td>
<td>{description}</td>
</tr>''')
    
    if not rows:
        return ''  # Don't show table if no formData params
    
    return f'''<table class="wrapped" data-mce-resize="false">
<tbody class="">
<tr class="">
<th>Параметр</th>
<th>Формат</th>
<th>Обязательный?</th>
<th>Описание</th>
</tr>
{chr(10).join(rows)}
</tbody>
</table>'''


def generate_fields_table(
    fields: List[Dict[str, Any]],
    field_changes: Optional[Dict[str, str]] = None,
    prefix: str = ""
) -> str:
    """
    Generate fields table with dynamic columns for nested fields.
    
    Each nesting level is displayed in a separate column:
    - Level 0: first column "Ключ"
    - Level 1: second column
    - Level 2: third column
    - etc.
    
    Args:
        fields: List of fields [{key, nested_key, type, description, required, full_path, level}]
        field_changes: {full_path: 'added'|'modified'|'removed'}
        prefix: Prefix for change lookup (e.g., 'requestBody' or 'responses.200')
        
    Returns:
        Confluence Storage Format HTML for the table
    """
    field_changes = field_changes or {}
    
    if not fields:
        return '<p>Нет полей</p>'
    
    # Determine max nesting level
    max_level = max(field.get('level', 0) for field in fields)
    num_key_columns = max_level + 1
    
    # Check if entire section is marked
    section_change_type = field_changes.get(prefix) if prefix else None
    if section_change_type:
        logger.info(f"[TABLE DEBUG] Section {prefix} marked as: {section_change_type}")
    
    rows = []
    for field in fields:
        field_name = field.get('key', '') or field.get('nested_key', '')
        field_type = field.get('type', 'string')
        description = field.get('description', '')
        required = field.get('required', False)
        full_path = field.get('full_path', field_name)
        level = field.get('level', 0)
        
        # Search for change by multiple path variants
        change_type = _find_field_change(
            field_name, full_path, prefix, field_changes
        )
        
        # Inherit from section if no specific match
        if not change_type:
            change_type = section_change_type
        
        row_style = _get_row_style(change_type)
        
        # Format field name
        name_formatted = f'<span style="color: rgb(31,41,55);">{field_name}</span>'
        required_text = 'Да' if required else 'Нет'
        
        # Create cells for key columns - field goes in column matching its level
        key_cells = []
        for col in range(num_key_columns):
            if col == level:
                key_cells.append(f'<td>{name_formatted}</td>')
            else:
                key_cells.append('<td></td>')
        
        rows.append(f'''<tr{row_style}>
{''.join(key_cells)}
<td>{field_type}</td>
<td>{required_text}</td>
<td>{description}</td>
</tr>''')
    
    # Generate headers - first is "Ключ", rest empty
    header_cells = ['<th>Ключ</th>']
    for _ in range(num_key_columns - 1):
        header_cells.append('<th />')
    
    return f'''<table class="wrapped" data-mce-resize="false">
<tbody class="">
<tr class="">
{''.join(header_cells)}
<th>Формат</th>
<th>Обязательный?</th>
<th>Описание</th>
</tr>
{chr(10).join(rows)}
</tbody>
</table>'''


# =============================================================================
# Helper Functions
# =============================================================================

def _get_row_style(change_type: Optional[str]) -> str:
    """Get HTML style attribute for row based on change type."""
    if change_type == 'added':
        return f' style="background-color: {COLOR_ADDED};"'
    elif change_type == 'modified':
        return f' style="background-color: {COLOR_MODIFIED};"'
    elif change_type == 'removed':
        return f' style="background-color: {COLOR_REMOVED};"'
    return ''


def _find_field_change(
    field_name: str,
    full_path: str,
    prefix: str,
    field_changes: Dict[str, str]
) -> Optional[str]:
    """Find change type for a field by checking multiple path variants."""
    search_paths = [
        full_path,
        f"{prefix}.{full_path}" if prefix else full_path,
        field_name,
        f"{prefix}.{field_name}" if prefix and field_name else None,
    ]
    
    for path in search_paths:
        if path and path in field_changes:
            return field_changes[path]
            
    # Fuzzy match: try to match by cleaning up paths (ignoring items, properties, etc.)
    # This handles discrepancies where one side includes 'items' and the other doesn't
    if prefix:
        def clean_path_tokens(p: str) -> str:
            from .constants import STRUCTURAL_KEYS
            # Remove schema keywords to compare structural path only
            tokens = p.split('.')
            return '.'.join([t for t in tokens if t not in STRUCTURAL_KEYS])
            
        target_full = f"{prefix}.{full_path}"
        cleaned_target = clean_path_tokens(target_full)
        
        for change_path, change_type in field_changes.items():
            if change_path.startswith(prefix):
                if clean_path_tokens(change_path) == cleaned_target:
                    logger.debug(f"[_find_field_change] Fuzzy match success: {change_path} -> {target_full}")
                    return change_type
            
    # Debug logging for failed lookups on potentially modified fields
    # logger.debug(f"[_find_field_change] Failed to find match. Search paths: {search_paths}. Keys: {list(field_changes.keys())}")
    return None
