"""
History Block Generator for Confluence Documentation.

Generates the "История изменений" (Change History) block with dates, Jira links, 
and detailed change descriptions.

Usage:
    from src.confluence.history_block import generate_change_history_block
    
    html = generate_change_history_block(events)
"""
import logging
from datetime import datetime
from .constants import IGNORED_KEYS, SKIP_PREFIXES
from typing import Any, Dict, List, Optional, Set, Tuple

from .macros import generate_jira_macro, generate_expand_macro, generate_macro_id
from .schema_utils import flatten_schema_to_fields

logger = logging.getLogger(__name__)


# =============================================================================
# Main Generator
# =============================================================================

def generate_change_history_block(events: List[Dict[str, Any]]) -> str:
    """
    Generate "История изменений" block with dates and Jira links.
    
    Format:
    - Дата: 23.10.2025
      Задача: LOGRETAIL-1891 (link)
      Изменения:
        • Добавлено поле unitId в тело запроса
        • Добавлен ответ с ошибкой 403
    
    Events may contain metadata:
    - _skip_reason: 'poor_schema' - show with note about incomplete spec
    - _skip_reason: 'insignificant' - don't show at all
    
    Args:
        events: List of change events with schema, diff, date, task_id
        
    Returns:
        Confluence Storage Format HTML for the history block
    """
    if not events:
        return "<p>Нет изменений</p>"
    
    expand_id = generate_macro_id()
    
    # Sort events by date (newest first)
    events_sorted = sorted(events, key=_parse_date_for_sort, reverse=True)
    history_items = []
    
    for event in events_sorted:
        # Skip events that are explicitly marked to be skipped (e.g. poor schema or insignificant)
        skip_reason = event.get('_skip_reason')
        if skip_reason == 'insignificant':
            continue
            
        task_id = event.get('task_id', 'UNKNOWN')
        date_formatted = _format_date(event.get('date', ''))
        mr_id = event.get('mr_id', '')
        link = event.get('link', '#')
        
        # if task_id == 'REVERT':
        #     continue
        
        event_type = event.get('type', '')
        
        # Generate Jira link
        jira_macro = generate_jira_macro(task_id) if task_id and task_id != 'UNKNOWN' else task_id
        
        # Determine change description
        if 'CREATED' in event_type:
            change_desc = "<li>API метод создан</li>"
        elif 'REVERT' in event_type:
             change_desc = "<li>API метод восстановлен</li>"
        elif 'DELETED' in event_type:
            change_desc = "<li>API метод удалён</li>"
        elif skip_reason == 'poor_schema':
            change_desc = "<li><em>Детализация контракта недоступна (устаревший формат спецификации)</em></li>"
        else:
            change_desc = _generate_detailed_change_description(
                event.get('diff', {}),
                event.get('schema'),
                event.get('previous_schema'),
                event.get('field_changes', {})  # Use pre-computed field_changes from Publisher
            )
        
        if not change_desc:
            # Skip empty entries if they are truly empty but keep logic sound
            # Wait, original code didn't skip if change_desc was empty?
            # Original code:
            # item = f'''...{change_desc}...'''
            # history_items.append(item)
            pass
        
        item = f'''
<p><strong>Дата: </strong>{date_formatted}<br />
<strong>Задача</strong>: {jira_macro}<br />
<strong>Изменения:</strong></p>
<ul style="list-style-type: none;"><ul>{change_desc}</ul></ul>
'''
        history_items.append(item)
    
    if not history_items:
        return "<p>Нет изменений</p>"

    content = ''.join(history_items)
    return f'''<ac:structured-macro ac:name="expand" ac:schema-version="1" ac:macro-id="{expand_id}"><ac:rich-text-body>{content}</ac:rich-text-body></ac:structured-macro>'''


# =============================================================================
# Detailed Change Description
# =============================================================================

def _generate_detailed_change_description(
    diff: Dict[str, Any],
    current_schema: Optional[Dict[str, Any]] = None,
    previous_schema: Optional[Dict[str, Any]] = None,
    field_changes: Optional[Dict[str, str]] = None
) -> str:
    """
    Generate detailed change description grouped by sections.
    
    Uses pre-computed field_changes from Publisher as primary source.
    Falls back to diff parsing for edge cases (format changes, etc.)
    
    Sections:
    - Заголовки запроса (header)
    - Тело запроса (requestBody)
    - Успешный ответ (responses.200)
    - Ответы с ошибками (responses.400, 403, etc.)
    """
    # Structural suffixes to skip during diff parsing
    STRUCTURAL_SUFFIXES = ("['allOf']", "['anyOf']", "['oneOf']", "['properties']", "['items']")
    
    # Categorize changes by section
    sections = {
        'header': {'added': [], 'removed': [], 'modified': []},
        'query': {'added': [], 'removed': [], 'modified': []},
        'path': {'added': [], 'removed': [], 'modified': []},
        'formData': {'added': [], 'removed': [], 'modified': []},
        'requestBody': {'added': [], 'removed': [], 'modified': []},
        'responses.200': {'added': [], 'removed': [], 'modified': []},
        'responses.error': {'added': [], 'removed': [], 'modified': []},
        'general': {'added': [], 'removed': [], 'modified': []},
    }
    
    # =========================================================================
    # PRIMARY: Use pre-computed field_changes from Publisher
    # =========================================================================
    if field_changes:
        for field_path, change_type in field_changes.items():
            # Parse field_path like "responses.200.result.contactData.phone"
            section = _get_section_from_field_path(field_path)
            
            # Extract just the field name (last part or meaningful suffix)
            field_name = _get_display_name_from_field_path(field_path)
            
            # Skip technical fields
            if field_name.endswith('x-nullable'):
                continue
            
            if section in sections:
                display_str = field_name
                
                # If this is an error response, try to extract the status code to make it explicit
                # "responses.error" is generic, so we want "error (400)" or "message (в ответе 400)"
                if section == 'responses.error':
                    # Parse code from field_path: responses.400.error -> 400
                    parts = field_path.split('.')
                    if len(parts) > 1 and parts[0] == 'responses' and parts[1].isdigit():
                         code = parts[1]
                         display_str = f"{field_name} (в ответе {code})"
                
                # If modified, try to find explanation if not already clear
                if change_type == 'modified' and current_schema and previous_schema:
                    # Lazy load definitions if needed
                    # We use a simple helper here to avoid dependency circularity or overhead if not needed.
                    # But since we need full path matching, we need flatten_schema_to_fields.
                    from .template_generator import flatten_schema_to_fields

                    # Helper to find field definition
                    def find_field_def(schema, target_path):
                        # This is expensive if run for every field, but we only do it for modified fields
                        # A better approach is to build a map once, but let's keep it simple for now as N is small
                        root_fields = flatten_schema_to_fields(schema)
                        for f in root_fields:
                            if f.get('full_path') == target_path:
                                return f
                        
                        # Fallback for responses/requestBody prefix stripping
                        # field_path "responses.200.result" -> target in schema "result" (if inside response)
                        # This logic is complex to replicate here perfectly, so we use a simplified check
                        # If target_path starts with responses.200., we look inside that response
                        if target_path.startswith('responses.'):
                            parts = target_path.split('.')
                            if len(parts) >= 3 and parts[1].isdigit():
                                code = parts[1]
                                resp = schema.get('responses', {}).get(code, {}).get('schema', {})
                                resp_fields = flatten_schema_to_fields(resp)
                                sub_path = '.'.join(parts[2:])
                                for rf in resp_fields:
                                    if rf.get('full_path') == sub_path:
                                        return rf
                        
                        # Added for requestBody handling
                        if target_path.startswith('requestBody.'):
                            rb = schema.get('requestBody', {})
                            
                            # Try standard OpenAPI structure first
                            content = rb.get('content', {}).get('application/json', {})
                            rb_schema = content.get('schema', {})
                            
                            if not rb_schema:
                                # Fallback: if 'content' is missing, maybe rb IS the schema (simplified/flattened)
                                # Check for common schema keywords
                                if 'type' in rb or 'properties' in rb or 'items' in rb or 'allOf' in rb:
                                    rb_schema = rb
                                else:
                                    rb_schema = rb.get('schema', {})
                            
                            if rb_schema:
                                rb_fields = flatten_schema_to_fields(rb_schema)
                                sub_path = target_path.replace('requestBody.', '', 1)
                                
                                # DEBUG: Log what we are looking for
                                # logger.warning(f"DEBUG: lookup '{sub_path}' in {len(rb_fields)} fields. Schema keys: {list(rb_schema.keys())}")
                                
                                for rf in rb_fields:
                                    if rf.get('full_path') == sub_path:
                                        return rf
                        
                        # Added for parameter handling (header, query, path, cookie)
                        if target_path.startswith(('header.', 'query.', 'path.', 'cookie.')):
                            param_type, param_name = target_path.split('.', 1)
                            params = schema.get('parameters', [])
                            if isinstance(params, list):
                                for p in params:
                                     if not isinstance(p, dict):
                                         continue
                                     if p.get('in') == param_type and p.get('name') == param_name:
                                         return p
                            elif isinstance(params, dict):
                                # Handle simplified structure: params[type][name]
                                type_group = params.get(param_type)
                                if isinstance(type_group, dict):
                                    return type_group.get(param_name)
                        
                        return None

                    curr_def = find_field_def(current_schema, field_path)
                    prev_def = find_field_def(previous_schema, field_path)
                    
                    explanation = []
                    
                    if curr_def and prev_def:
                        # Check type change
                        old_type = prev_def.get('type', getattr(prev_def, 'type', None))
                        new_type = curr_def.get('type', getattr(curr_def, 'type', None))
                        
                        # Use schema type if parameter wraps it (common in OpenAPI 3)
                        # Param: { name: foo, in: query, schema: { type: integer } }
                        if not old_type and 'schema' in prev_def:
                             old_type = prev_def['schema'].get('type')
                        if not new_type and 'schema' in curr_def:
                             new_type = curr_def['schema'].get('type')

                        if old_type and new_type and old_type != new_type:
                            explanation.append(f"тип: {old_type} → {new_type}")
                        
                        # Check required status change
                        old_req = prev_def.get('required', False)
                        new_req = curr_def.get('required', False)
                        if old_req != new_req:
                            status = "обязательным" if new_req else "необязательным"
                            explanation.append(f"стало {status}")

                        if not explanation:
                            # Explicitly check format if type string is identical (e.g. both 'string')
                            old_fmt = prev_def.get('format', getattr(prev_def, 'format', '')) 
                            new_fmt = curr_def.get('format', getattr(curr_def, 'format', '')) 
                             # Param schema format check
                            if not old_fmt and 'schema' in prev_def:
                                 old_fmt = prev_def['schema'].get('format')
                            if not new_fmt and 'schema' in curr_def:
                                 new_fmt = curr_def['schema'].get('format')
                            
                            if old_fmt and new_fmt and old_fmt != new_fmt:
                                explanation.append(f"формат: {old_fmt} → {new_fmt}")
                        
                        if explanation:
                            display_str += " (" + ", ".join(explanation) + ")"
                    
                    # DEBUG LOGGING (moved outside 'if curr_def and prev_def' check if possible? No, variables are local)
                    # But we want to know if definitions matched.
                    if 'Authorization' in field_path:
                         logger.warning(f"DEBUG {field_path}: found_curr={bool(curr_def)}, found_prev={bool(prev_def)}")
                         if curr_def and prev_def:
                             logger.warning(f"DEBUG {field_path}: details prev_req={prev_def.get('required')}, curr_req={curr_def.get('required')}")

                sections[section][change_type].append(display_str)
    
    # =========================================================================
    # SECONDARY: Parse diff for additional details (format/required changes)
    # =========================================================================
    # Note: field_changes already captures most changes; diff parsing adds format/required details
    
    # Collect existing fields from previous schema
    previously_existing_fields = _collect_previous_field_names(previous_schema)
    
    # Process added items from diff (for format changes that field_changes doesn't capture)
    for path in diff.get('dictionary_item_added', []):
        if path.endswith(STRUCTURAL_SUFFIXES):
            continue
        
        # Format addition is already handled by field_changes (Primary Loop)
        if path.endswith("['format']"):
            continue
        
        # Required addition → modified
        if path.endswith("['required']"):
            # Old parse_diff_path logic was here, removed
            pass # We handle required arrays properly below
    
    # Process removed items - ONLY for format removal annotations
    for path in diff.get('dictionary_item_removed', []):
        if path.endswith(STRUCTURAL_SUFFIXES) or path.endswith("['required']"):
            continue
        
        # Format removal is already handled by field_changes (Primary Loop)
        if path.endswith("['format']"):
            continue
    
    # Process changed values - analyze old_value/new_value for details.
    # _normalize_path is a pure static helper — call it directly instead of
    # constructing a ConfluencePublisher (which would require Confluence
    # credentials that aren't available in this rendering context).
    from .publisher import ConfluencePublisher

    for path, change_info in diff.get('values_changed', {}).items():
        normalized = ConfluencePublisher._normalize_path(path)
        if not normalized:
            continue
            
        section = _get_section_from_field_path(normalized)
        if section not in sections:
            section = 'requestBody'
            
        # Skip cosmetic attribute changes (example, description, etc.)
        cosmetic_attrs = ("['example']", "['description']", "['title']", "['deprecated']")
        if path.endswith(cosmetic_attrs):
            continue
        
        # Try to extract detailed attribute change info (format, type, etc.)
        attr_match = _extract_changed_attribute(path, change_info)
        if attr_match:
            field_name, attr_desc = attr_match
            sections.get(section, sections['requestBody'])['modified'].append(f"{field_name} ({attr_desc})")

    # Process iterable changes (required arrays)
    all_added_fields = set()
    for section_data in sections.values():
        all_added_fields.update(section_data['added'])
    
    for path, value in diff.get('iterable_item_added', {}).items():
        if 'allOf' in path or 'anyOf' in path or 'oneOf' in path:
            continue
            
        # Handle required array additions
        if path.endswith("['required']"):
            import re
            parent_path = re.sub(r"\['required'\]$", "", path)
            normalized_parent = ConfluencePublisher._normalize_path(parent_path)
            
            if normalized_parent:
                section = _get_section_from_field_path(normalized_parent)
                field_name = value
                if isinstance(value, str) and not value.isdigit():
                     if value not in all_added_fields:
                        sections.get(section, sections['requestBody'])['modified'].append(f"{value} (стало обязательным)")

    for path, value in diff.get('iterable_item_removed', {}).items():
        if 'allOf' in path or 'anyOf' in path or 'oneOf' in path:
            continue
            
        # Handle required array removals
        if path.endswith("['required']"):
            import re
            parent_path = re.sub(r"\['required'\]$", "", path)
            normalized_parent = ConfluencePublisher._normalize_path(parent_path)
            
            if normalized_parent:
                section = _get_section_from_field_path(normalized_parent)
                if isinstance(value, str) and not value.isdigit():
                    sections.get(section, sections['requestBody'])['modified'].append(f"{value} (стало необязательным)")
    
    # Required-attribute changes (обязательный ⇄ необязательный) are detected by
    # comparing flattened field definitions directly. This is robust regardless
    # of how DeepDiff encodes the change: when a field is the only required one,
    # dropping it removes the whole `['required']` key (a dictionary_item_removed
    # that the diff-parsing above intentionally skips), so relying on the diff
    # alone misses it. Schema comparison catches every case.
    if current_schema and previous_schema:
        _extract_required_changes(sections, current_schema, previous_schema)
    
    # Enrich added fields with child field info for nested objects
    if current_schema:
        _enrich_with_nested_fields(sections, current_schema)
    
    # Generate human-readable description
    return _format_changes_as_html(sections)


def _format_changes_as_html(sections: Dict[str, Dict[str, List[str]]]) -> str:
    """Format sections into HTML list items."""
    # Section labels
    section_labels_accusative = {
        'general': 'общее описании',
        'path': 'path-параметры',
        'query': 'query-параметры',
        'header': 'заголовки запроса',
        'formData': 'form-data параметры',
        'requestBody': 'тело запроса',
        'responses.200': 'тело ответа',
        'responses.error': 'ответы с ошибками'
    }
    # For removal (Genitive case: "Deleted from...")
    section_labels_genitive = {
        'general': 'общего описания',
        'path': 'path-параметров',
        'query': 'query-параметров',
        'header': 'заголовков запроса',
        'formData': 'form-data параметров',
        'requestBody': 'тела запроса',
        'responses.200': 'тела ответа',
        'responses.error': 'ответов с ошибками'
    }
    # For modification (Prepositional case: "Modified in...")
    section_labels_prepositional = {
        'general': 'общем описании',
        'path': 'path-параметрах',
        'query': 'query-параметрах',
        'header': 'заголовках запроса',
        'formData': 'form-data параметрах',
        'requestBody': 'теле запроса',
        'responses.200': 'теле ответа',
        'responses.error': 'ответах с ошибками'
    }
    
    changes_list = []
    
    # Helper to parse error code suffix
    import re
    def parse_error_suffix(f: str) -> Tuple[str, Optional[str]]:
        match = re.search(r'\(в ответе (\d+)\)$', f.strip())
        if match:
            code = match.group(1)
            base = f.replace(f"(в ответе {code})", "").strip()
            return base, code
        return f, None

    # =========================================================================
    # PASS 1: All REMOVALS first (across all sections)
    # =========================================================================
    for section_key in section_labels_accusative.keys():
        section_data = sections[section_key]
        if section_data['removed']:
            fields = _dedupe_fields(section_data['removed'])
            label = section_labels_genitive[section_key]
            for field in fields:
                if section_key == 'responses.error':
                    if field.isdigit():
                        changes_list.append(f"<li>Удалён ответ с ошибкой <code>{field}</code></li>")
                        continue
                    
                    base, code = parse_error_suffix(field)
                    if code:
                        changes_list.append(f"<li>Удалено поле <code>{base}</code> из ответа с кодом {code}</li>")
                    else:
                        changes_list.append(f"<li>Удалено поле <code>{field}</code> из {label}</li>")
                else:
                    changes_list.append(f"<li>Удалено поле <code>{field}</code> из {label}</li>")
    
    # =========================================================================
    # PASS 2: All ADDITIONS (across all sections)
    # =========================================================================
    for section_key in section_labels_accusative.keys():
        section_data = sections[section_key]
        if section_data['added']:
            fields = _dedupe_fields(section_data['added'])
            label = section_labels_accusative[section_key]
            if section_key == 'responses.error':
                for field in fields:
                    if field.isdigit():
                        changes_list.append(f"<li>Добавлен ответ с ошибкой <code>{field}</code></li>")
                        continue

                    base, code = parse_error_suffix(field)
                    if code:
                        changes_list.append(f"<li>Добавлено поле <code>{base}</code> в ответ с кодом {code}</li>")
                    else:
                        changes_list.append(f"<li>Добавлено поле <code>{field}</code> в {label}</li>")
            else:
                # Separate objects (with children) from simple fields
                objects = [f for f in fields if '(' in f and ')' in f]
                simple = [f for f in fields if '(' not in f]
                
                # Extract parent names AND their children from objects to filter out
                parent_names = set()
                child_names = set()
                for obj in objects:
                    name = obj.split(' (')[0]
                    parent_names.add(name)
                    if ' (' in obj and ')' in obj:
                        children_str = obj.split(' (')[1].rstrip(')')
                        for child in children_str.split(','):
                            child_names.add(child.strip())
                
                # Filter out fields that are children of shown objects
                filtered_simple = []
                for field in simple:
                    is_child = False
                    for parent in parent_names:
                        if field.startswith(parent + '.'):
                            is_child = True
                            break
                    if not is_child and field in child_names:
                        is_child = True
                    if not is_child:
                        filtered_simple.append(field)
                
                # Format objects: "Добавлен объект X (child1, child2)"
                for obj in objects:
                    name = obj.split(' (')[0]
                    children_str = obj.split(' (')[1].rstrip(')')
                    children_list = [c.strip() for c in children_str.split(',')]
                    children_formatted = ', '.join(f"<code>{c}</code>" for c in children_list)
                    changes_list.append(f"<li>Добавлен объект <code>{name}</code> ({children_formatted}) в {label}</li>")
                
                # Format simple fields
                for field in filtered_simple:
                    changes_list.append(f"<li>Добавлено поле <code>{field}</code> в {label}</li>")
    
    # =========================================================================
    # PASS 3: All MODIFICATIONS (across all sections)
    # =========================================================================
    for section_key in section_labels_accusative.keys():
        section_data = sections[section_key]
        if section_data['modified']:
            fields = _dedupe_fields(section_data['modified'])
            label = section_labels_prepositional[section_key]
            for f in fields:
                if section_key == 'responses.error':
                    # Check for explicit code suffix
                    base, code = parse_error_suffix(f)
                    if code:
                        # Handle annotations (e.g. type changed) that might be attached
                        # parse_error_suffix handles simple suffix but if there are other annotations like (тип: ...)
                        # we need to be careful. The suffix " (в ответе XXX)" was appended directly to name.
                        # Type annotations are usually appended AFTER.
                        # But wait, my previous code appends " (в ответе XXX)" to display_str.
                        # Then explanation is appended: display_str += " (" + explained + ")"
                        # So it becomes "field (в ответе 400) (тип: string -> int)"
                        # parse_error_suffix needs to match inside the string or stricter logic.
                        
                        # Let's improve parse logic inside the loop if needed, but for now specific check:
                        if '(в ответе' in f:
                             # Regex search for code
                             m = re.search(r'\(в ответе (\d+)\)', f)
                             if m:
                                 c = m.group(1)
                                 # Remove the code tag
                                 clean = f.replace(f"(в ответе {c})", "").strip()
                                 # Format
                                 # Check if there are other annotations (brackets)
                                 # If clean matches "field (change desc)", we want "Поле field (change desc) в ответ с кодом 400"
                                 # Or "Изменено поле field в ответ с кодом 400"
                                 
                                 # If clean has NO other parens:
                                 if '(' not in clean:
                                     changes_list.append(f"<li>Изменено поле <code>{clean}</code> в ответе с кодом {c}</li>")
                                 else:
                                     # Case: name (type: X->Y)
                                     # Extract base name
                                     base_name = clean.split(' (')[0]
                                     rest = clean[len(base_name):] # start with ' ('
                                     changes_list.append(f"<li>Поле <code>{base_name}</code> в ответе с кодом {c} {rest.strip()}</li>")
                                 continue

                if '(стал' in f:
                    base = f.split(' (')[0]
                    annotation = f.split('(')[1].rstrip(')')
                    changes_list.append(f"<li>Поле <code>{base}</code> {annotation}</li>")
                else:
                    changes_list.append(f"<li>Изменено поле <code>{f}</code> в {label}</li>")
    
    return ''.join(changes_list) if changes_list else "<li>Изменения в контракте</li>"


def _enrich_with_nested_fields(
    sections: Dict[str, Dict[str, List[str]]], 
    current_schema: Dict[str, Any]
) -> None:
    """
    Enrich added field names with child field info for nested objects.
    
    Transforms: "approvedPrice" -> "approvedPrice (updatedDate, value)"
    """
    # Build field hierarchy from schema
    field_hierarchy = _build_field_hierarchy(current_schema)
    
    # Process each section's added fields
    for section_key in ['requestBody', 'responses.200', 'responses.error', 'header']:
        if section_key not in sections:
            continue
        
        added = sections[section_key]['added']
        enriched = []
        
        for field_name in added:
            # Check if this field has children
            # Handle dot-separated paths (e.g. "items.discountPrice" should lookup "discountPrice")
            lookup_key = field_name.split('.')[-1] if '.' in field_name else field_name
            children = field_hierarchy.get(lookup_key, [])
            
            if children:
                # Limit to 4 children
                child_names = children[:4]
                if len(children) > 4:
                    child_names.append('...')
                enriched.append(f"{field_name} ({', '.join(child_names)})")
            else:
                enriched.append(field_name)
        
        sections[section_key]['added'] = enriched


def _build_field_hierarchy(schema: Dict[str, Any]) -> Dict[str, List[str]]:
    """
    Build a mapping of parent field names to their immediate children.
    
    Returns: {"approvedPrice": ["updatedDate", "value"], ...}
    """
    hierarchy = {}
    
    # Flatten all sections
    for section_key, section_data in [
        ('requestBody', schema.get('requestBody', {})),
        ('responses.200', schema.get('responses', {}).get('200', {}).get('schema', {})),
    ]:
        if not section_data:
            continue
        
        fields = flatten_schema_to_fields(section_data)
        
        for field in fields:
            full_path = field.get('full_path', '')
            if '.' in full_path:
                parts = full_path.split('.')
                # Get parent and child
                parent = parts[-2]
                child = parts[-1]
                
                if parent not in hierarchy:
                    hierarchy[parent] = []
                if child not in hierarchy[parent]:
                    hierarchy[parent].append(child)
    
    return hierarchy


# =============================================================================
# Helper Functions
# =============================================================================

def _parse_date_for_sort(event: Dict[str, Any]) -> datetime:
    """Parse event date for sorting."""
    date_str = event.get('date', '')
    try:
        if date_str:
            return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return datetime.min
    except:
        try:
            return datetime.strptime(date_str[:10], '%Y-%m-%d')
        except:
            return datetime.min


def _format_date(date_str: str) -> str:
    """Format date string to DD.MM.YYYY."""
    try:
        if date_str:
            dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            return dt.strftime('%d.%m.%Y')
        return 'Неизвестно'
    except:
        return date_str[:10] if date_str else 'Неизвестно'


def _parse_diff_path(path: str) -> Tuple[Optional[str], Optional[str]]:
    """Parse DeepDiff path to section and field name."""
    if not path:
        return (None, None)
    
    normalized = path.replace("root", "").replace("['", ".").replace("']", "").strip(".")
    parts = normalized.split(".")
    
    if not parts:
        return (None, None)
    
    skip_keys = {'properties', 'items', 'allOf', 'anyOf', 'oneOf', 'schema',
                 'content', 'application/json', 'requestBody', 'responses',
                 'parameters', 'header', 'query', 'path', 'required', 'example'}
    
    section = None
    field_name = None
    
    if 'parameters' in parts:
        idx = parts.index('parameters')
        if idx + 1 < len(parts):
            param_type = parts[idx + 1]  # header, query, path, formData
            if param_type in ['header', 'query', 'path', 'formData']:
                section = param_type
                if idx + 2 < len(parts):
                    field_name = parts[idx + 2]
    elif 'header' in parts: # Fallback if direct header access (legacy?)
        section = 'header'
        idx = parts.index('header')
        if idx + 1 < len(parts):
            field_name = parts[idx + 1]
    elif 'requestBody' in parts:
        section = 'requestBody'
        for part in reversed(parts):
            if part.isdigit() or part in skip_keys:
                continue
            if part:
                field_name = part
                break
    elif 'responses' in parts:
        try:
            idx = parts.index('responses')
            if idx + 1 < len(parts):
                status_code = parts[idx + 1]
                if status_code == '200' or status_code == '201':
                    section = 'responses.200'
                    # Extract field name for success response too!
                    for part in reversed(parts):
                        if part.isdigit() or part in skip_keys:
                            continue
                        if part:
                            field_name = part
                            break
                elif status_code.isdigit():
                    section = 'responses.error'
                    if idx + 2 >= len(parts) or parts[idx + 2] in ['schema', 'content', 'description']:
                        field_name = status_code
                    else:
                        for part in reversed(parts):
                            if part.isdigit() or part in skip_keys:
                                continue
                            if part:
                                field_name = part
                                break
        except:
            pass
    elif 'summary' in parts:
        section = 'general'
        field_name = 'summary'
    elif 'description' in parts:
        section = 'general'
        field_name = 'description'
    elif 'deprecated' in parts:
        section = 'general'
        field_name = 'deprecated'
    
    return (section, field_name)


def _get_section_from_field_path(field_path: str) -> str:
    """
    Get section name from a field path like 'responses.200.result.contactData'.
    
    Returns one of: 'header', 'query', 'path', 'formData', 'requestBody', 
                    'responses.200', 'responses.error', 'general'
    """
    if not field_path:
        return 'requestBody'
    
    parts = field_path.split('.')
    
    # Check for parameters (header, query, path, formData)
    # Handle direct prefixes (header.Auth) and nested paths (parameters.header.Auth)
    if parts[0] in ['header', 'query', 'path', 'formData']:
        return parts[0]

    if 'parameters' in parts or field_path.startswith('parameters.'):
        for section_type in ['header', 'query', 'path', 'formData']:
            if section_type in parts:
                return section_type
        return 'header'  # Default parameter type
    
    # Check for responses
    if parts[0] == 'responses':
        if len(parts) > 1:
            status = parts[1]
            if status in ['200', '201']:
                return 'responses.200'
            elif status.isdigit():
                return 'responses.error'
        return 'responses.200'
    
    # FIX: Remap orphan error fields to responses.error (handles cache pollution or incorrect parsing)
    if field_path in ['errors', 'error', 'message', 'code'] or field_path.startswith('errors.'):
        return 'responses.error'

    # Check for requestBody
    if 'requestBody' in parts or 'body' in parts:
        return 'requestBody'
    
    # Check for 'result' - this is response-specific (APIs return { result: ... })
    if 'result' in parts:
        return 'responses.200'
    
    # Default to requestBody for truly unknown paths
    return 'requestBody'


def _get_display_name_from_field_path(field_path: str) -> str:
    """
    Get display-friendly field name from a field path.
    
    Examples: 
    - 'responses.200.result.contactData.phone' -> 'contactData.phone'
    - 'responses.200.result.contactData' -> 'contactData'
    - 'requestBody.officeGuid' -> 'officeGuid'
    """
    if not field_path:
        return 'unknown'
    
    parts = field_path.split('.')
    
    # Remove common prefixes
    from .constants import SKIP_PREFIXES
    
    meaningful_parts = []
    for part in parts:
        if part in SKIP_PREFIXES or part.isdigit() or part in {'200', '201', '400', '500'}:
            continue
        meaningful_parts.append(part)
    
    if not meaningful_parts:
        # Fallback to last part
        return parts[-1] if parts else 'unknown'
    
    # Return last 2 parts max for readability: "contactData.phone" not "result.contactData.phone"
    if len(meaningful_parts) > 2:
        return '.'.join(meaningful_parts[-2:])
    
    return '.'.join(meaningful_parts)


def _extract_full_field_path(deepdiff_path: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract full field path from DeepDiff path."""
    import re
    
    if not deepdiff_path:
        return (None, None)
    
    normalized = deepdiff_path.replace("root", "")
    normalized = normalized.replace("['", ".").replace("']", "")
    normalized = re.sub(r'\[\d+\]', '', normalized)
    normalized = normalized.strip(".")
    parts = normalized.split(".")
    
    if not parts:
        return (None, None)
    
    skip_keys = IGNORED_KEYS
    
    section = None
    field_parts = []
    
    if 'requestBody' in parts:
        section = 'requestBody'
        try:
            idx = parts.index('requestBody')
            for part in parts[idx+1:]:
                if part.isdigit() or part in skip_keys:
                    continue
                field_parts.append(part)
        except:
            pass
    elif 'responses' in parts:
        try:
            idx = parts.index('responses')
            if idx + 1 < len(parts):
                status_code = parts[idx + 1]
                if status_code == '200' or status_code == '201':
                    section = 'responses.200'
                elif status_code.isdigit():
                    section = 'responses.error'
                
                for part in parts[idx+2:]:
                    if part.isdigit() or part in skip_keys:
                        continue
                    field_parts.append(part)
        except:
            pass
    elif 'parameters' in parts:
        # handle parameters extraction
        try:
            idx = parts.index('parameters')
            if idx + 1 < len(parts):
                param_type = parts[idx+1]
                if param_type in ['header', 'query', 'path', 'formData']:
                    section = param_type
                    if idx + 2 < len(parts):
                        field_parts.append(parts[idx+2])
        except:
            pass
    elif 'header' in parts:
        section = 'header'
        try:
             idx = parts.index('header')
             if idx + 1 < len(parts):
                 field_parts.append(parts[idx+1])
        except:
            pass
    
    field_path = '.'.join(field_parts) if field_parts else None
    return (section, field_path)


def _collect_previous_field_names(previous_schema: Optional[Dict[str, Any]]) -> Set[str]:
    """Collect all field names from previous schema."""
    if not previous_schema:
        return set()
    
    field_names = set()
    
    prev_responses = previous_schema.get('responses', {})
    for code in ['200', '201']:
        prev_resp = prev_responses.get(code, {}).get('schema', {})
        if prev_resp:
            fields = flatten_schema_to_fields(prev_resp)
            for field in fields:
                full_path = field.get('full_path', '')
                if full_path:
                    field_name = full_path.split('.')[-1] if '.' in full_path else full_path
                    field_names.add(field_name)
    
    prev_rb = previous_schema.get('requestBody', {})
    if prev_rb:
        fields = flatten_schema_to_fields(prev_rb)
        for field in fields:
            full_path = field.get('full_path', '')
            if full_path:
                field_name = full_path.split('.')[-1] if '.' in full_path else full_path
                field_names.add(field_name)
    
    return field_names


def _extract_structural_changes(
    diff: Dict[str, Any],
    sections: Dict[str, Dict[str, List[str]]],
    current_schema: Dict[str, Any],
    previous_schema: Dict[str, Any]
) -> None:
    """Extract field changes from structural schema changes."""
    STRUCTURAL_SUFFIXES = ("['allOf']", "['anyOf']", "['oneOf']", "['properties']")
    
    has_structural_change = False
    for path in diff.get('dictionary_item_added', []):
        if path.endswith(STRUCTURAL_SUFFIXES):
            has_structural_change = True
            break
    
    if not has_structural_change:
        for path in diff.get('dictionary_item_removed', []):
            if path.endswith(STRUCTURAL_SUFFIXES):
                has_structural_change = True
                break
    
    if not has_structural_change:
        return
    
    # Compare requestBody
    current_rb = current_schema.get('requestBody', {})
    previous_rb = previous_schema.get('requestBody', {})
    
    if current_rb and previous_rb:
        current_fields = flatten_schema_to_fields(current_rb)
        previous_fields = flatten_schema_to_fields(previous_rb)
        
        current_paths = {f.get('full_path', '') for f in current_fields if f.get('full_path')}
        previous_paths = {f.get('full_path', '') for f in previous_fields if f.get('full_path')}
        
        for path in current_paths - previous_paths:
            field_name = path.split('.')[-1] if '.' in path else path
            if field_name not in sections['requestBody']['added']:
                sections['requestBody']['added'].append(field_name)
        
        for path in previous_paths - current_paths:
            field_name = path.split('.')[-1] if '.' in path else path
            if field_name not in sections['requestBody']['removed']:
                sections['requestBody']['removed'].append(field_name)
    
    # Compare responses.200
    current_responses = current_schema.get('responses', {})
    previous_responses = previous_schema.get('responses', {})
    
    for code in ['200', '201']:
        current_resp = current_responses.get(code, {}).get('schema', {})
        previous_resp = previous_responses.get(code, {}).get('schema', {})
        
        if current_resp and previous_resp:
            current_fields = flatten_schema_to_fields(current_resp)
            previous_fields = flatten_schema_to_fields(previous_resp)
            
            current_paths = {f.get('full_path', '') for f in current_fields if f.get('full_path')}
            previous_paths = {f.get('full_path', '') for f in previous_fields if f.get('full_path')}
            
            section_key = 'responses.200'
            
            for path in current_paths - previous_paths:
                field_name = path.split('.')[-1] if '.' in path else path
                if field_name not in sections[section_key]['added']:
                    sections[section_key]['added'].append(field_name)
            
            for path in previous_paths - current_paths:
                field_name = path.split('.')[-1] if '.' in path else path
                if field_name not in sections[section_key]['removed']:
                    sections[section_key]['removed'].append(field_name)


def _extract_required_changes(
    sections: Dict[str, Dict[str, List[str]]],
    current_schema: Dict[str, Any],
    previous_schema: Dict[str, Any]
) -> None:
    """Extract required attribute changes via schema comparison."""
    # Compare requestBody
    current_rb = current_schema.get('requestBody', {})
    previous_rb = previous_schema.get('requestBody', {})
    
    if current_rb and previous_rb:
        current_fields = {f.get('full_path', ''): f for f in flatten_schema_to_fields(current_rb) if f.get('full_path')}
        previous_fields = {f.get('full_path', ''): f for f in flatten_schema_to_fields(previous_rb) if f.get('full_path')}
        
        common_paths = current_fields.keys() & previous_fields.keys()
        sec_mod = sections['requestBody']['modified']

        for path in common_paths:
            current_required = current_fields[path].get('required', False)
            previous_required = previous_fields[path].get('required', False)

            if current_required != previous_required:
                field_name = path.split('.')[-1] if '.' in path else path
                annotated = (f"{field_name} (стало обязательным)" if current_required
                             else f"{field_name} (стало необязательным)")
                # Replace any generic (un-annotated) entry for this field with the
                # specific "стало (не)обязательным" wording (dedupe-safe).
                sec_mod[:] = [m for m in sec_mod if m != field_name]
                if annotated not in sec_mod:
                    sec_mod.append(annotated)
    
    # Compare responses (200/201) - THIS WAS MISSING!
    current_responses = current_schema.get('responses', {})
    previous_responses = previous_schema.get('responses', {})
    
    for code in ['200', '201']:
        current_resp = current_responses.get(code, {}).get('schema', {})
        previous_resp = previous_responses.get(code, {}).get('schema', {})
        
        if current_resp and previous_resp:
            current_fields = {f.get('full_path', ''): f for f in flatten_schema_to_fields(current_resp) if f.get('full_path')}
            previous_fields = {f.get('full_path', ''): f for f in flatten_schema_to_fields(previous_resp) if f.get('full_path')}
            
            section_key = 'responses.200'
            common_paths = current_fields.keys() & previous_fields.keys()
            sec_mod = sections[section_key]['modified']

            for path in common_paths:
                current_required = current_fields[path].get('required', False)
                previous_required = previous_fields[path].get('required', False)

                if current_required != previous_required:
                    field_name = path.split('.')[-1] if '.' in path else path
                    annotated = (f"{field_name} (стало обязательным)" if current_required
                                 else f"{field_name} (стало необязательным)")
                    sec_mod[:] = [m for m in sec_mod if m != field_name]
                    if annotated not in sec_mod:
                        sec_mod.append(annotated)


def _dedupe_fields(fields: List[str], max_count: int = 5) -> List[str]:
    """
    Remove duplicates and technical names, limit count.
    
    Also handles prefix variants: 'result.contactData.phone' and 'contactData.phone' 
    are treated as the same field - keeps the version with annotation if available.
    """
    skip_keys = IGNORED_KEYS
    
    # Normalize field names by extracting the LAST meaningful part
    # This handles: 'pricing.discountReason' → 'discountReason'
    #              'result.contactData.phone' → 'phone'
    def normalize(field: str) -> str:
        """Normalize field name for deduplication - use last part of path."""
        if '.' not in field:
            return field
        parts = field.split('.')
        # Skip common prefixes and return last part
        skip_prefixes = SKIP_PREFIXES
        meaningful = [p for p in parts if p not in skip_prefixes]
        return meaningful[-1] if meaningful else parts[-1]
    
    # Track normalized names -> (base_field, annotation)
    seen_normalized = {}  # normalized -> (normalized_base, annotation)
    
    for f in fields:
        if f in skip_keys:
            continue
        if '[' in f and ']' in f:
            continue
        
        # Split field name from annotation like "(формат удалён)"
        base_field = f.split(' (')[0] if ' (' in f else f
        annotation = f[len(base_field):] if ' (' in f else ''
        
        normalized = normalize(base_field)
        
        if normalized in seen_normalized:
            existing_base, existing_annotation = seen_normalized[normalized]
            
            if annotation and not existing_annotation:
                # Replace with annotated version
                seen_normalized[normalized] = (normalized, annotation)
            elif annotation and existing_annotation:
                # Helper to parse annotation parts
                def parse_parts(ann):
                    if not ann.strip().startswith('(') or not ann.strip().endswith(')'):
                        return [ann]
                    # Check for " (" prefix from splitting
                    clean = ann.strip()
                    if clean.startswith('(') and clean.endswith(')'):
                        content = clean[1:-1]
                        return [p.strip() for p in content.split(',') if p.strip()]
                    return [ann]

                # Extract parts from both
                existing_parts = parse_parts(existing_annotation)
                new_parts = parse_parts(annotation)
                
                # Combine unique parts maintaining order
                combined_parts = list(existing_parts)
                for part in new_parts:
                    if part not in combined_parts:
                        combined_parts.append(part)
                
                # Reconstruct annotation
                combined_str = f" ({', '.join(combined_parts)})"
                seen_normalized[normalized] = (normalized, combined_str)
        else:
            seen_normalized[normalized] = (normalized, annotation)
    
    # Build result list
    result = [base + annotation for base, annotation in seen_normalized.values()]
    
    return result[:max_count]


def _extract_changed_attribute(path: str, change_info: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """
    Extract detailed info about changed field attribute.
    
    For paths like:
    - root['...']['properties']['type']['properties']['id']['format']
    
    Returns:
        (field_name, attribute_description) or None
        
    Example:
        ("type.id", "формат: uuid → string")
    """
    import re
    
    # Extract the last attribute name from path
    attr_match = re.search(r"\['(format|type|enum|minimum|maximum|pattern|default)'\]$", path)
    if not attr_match:
        return None
    
    attr_name = attr_match.group(1)
    
    # Find field name - look for the last property name before the attribute
    # Pattern: ['properties']['fieldName']['attribute']
    field_match = re.search(r"\['properties'\]\['([^']+)'\]\['" + attr_name + r"'\]$", path)
    if not field_match:
        # Try nested: ['properties']['parent']['properties']['child']['attribute']
        field_match = re.search(r"\['([^']+)'\]\['" + attr_name + r"'\]$", path)
    
    if not field_match:
        return None
    
    field_name = field_match.group(1)
    
    # Get old and new values
    old_val = change_info.get('old_value', '')
    new_val = change_info.get('new_value', '')
    
    # Special handling for format: only show if both old and new exist
    # UPDATE: Skip format changes here entirely, as they are covered by 'type' string changes in Primary loop
    # which includes format info (e.g. "integer (int8)").
    if attr_name == 'format':
        return None
    
    # Generate description based on attribute type
    attr_descriptions = {
        'format': f"формат: {old_val} → {new_val}",
        'type': f"тип: {old_val} → {new_val}",
        'enum': "изменены допустимые значения",
        'minimum': f"минимум: {old_val} → {new_val}",
        'maximum': f"максимум: {old_val} → {new_val}",
        'pattern': "изменён паттерн валидации",
        'default': f"значение по умолчанию: {old_val} → {new_val}",
    }
    
    desc = attr_descriptions.get(attr_name, f"{attr_name} изменён")
    return (field_name, desc)
