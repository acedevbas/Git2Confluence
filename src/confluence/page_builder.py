"""
Page Builder for Confluence Documentation.

Generates complete Confluence pages with: environments, method info, history tabs, 
change legend, and task-specific content.

Usage:
    from src.confluence.page_builder import generate_full_page, generate_task_tab_content
    
    page_html = generate_full_page(method, path, summary, description, tabs_content, events)
"""
import json
from typing import Any, Dict, List, Optional, Tuple

from .macros import (
    generate_macro_id,
    generate_status_macro,
    generate_code_macro,
    generate_jira_macro,
    generate_multiexcerpt,
    generate_multiexcerpt_include,
    generate_hidden_excerpt,
    generate_multiexcerpt_include_by_id,
)
from .tables import (
    generate_header_params_table,
    generate_formdata_params_table,
    generate_fields_table,
    COLOR_ADDED,
    COLOR_MODIFIED,
    COLOR_REMOVED,
)
from .history_block import generate_change_history_block


# =============================================================================
# UI Tabs Generation
# =============================================================================

def generate_ui_tab(title: str, content: str) -> str:
    """Generate a single ui-tab."""
    macro_id = generate_macro_id()
    return f'''<ac:structured-macro ac:name="ui-tab" ac:schema-version="1" ac:macro-id="{macro_id}"><ac:parameter ac:name="title">{title}</ac:parameter><ac:rich-text-body>{content}</ac:rich-text-body></ac:structured-macro>'''


def generate_ui_tabs(tabs: List[Tuple[str, str]]) -> str:
    """
    Generate ui-tabs with multiple tabs.
    
    Args:
        tabs: [(title, content), ...]
        
    Returns:
        Confluence Storage Format HTML for ui-tabs
    """
    tabs_content = ''.join([generate_ui_tab(title, content) for title, content in tabs])
    macro_id = generate_macro_id()
    
    return f'''<ac:structured-macro ac:name="ui-tabs" ac:schema-version="1" ac:macro-id="{macro_id}"><ac:rich-text-body>{tabs_content}</ac:rich-text-body></ac:structured-macro>'''


# =============================================================================
# Task Tab Content
# =============================================================================

def generate_task_tab_content(
    task_id: str,
    request_example: str,
    header_params: List[Dict[str, Any]],
    formdata_params: Optional[List[Dict[str, Any]]] = None,
    request_fields: Optional[List[Dict[str, Any]]] = None,
    success_example: str = "",
    success_fields: Optional[List[Dict[str, Any]]] = None,
    error_examples: Optional[List[Tuple[str, str, str, List[Dict[str, Any]]]]] = None,
    field_changes: Optional[Dict[str, str]] = None,
    version_suffix: str = "",
    page_title: Optional[str] = None
) -> str:
    """
    Generate content for a single JIRA task tab with MultiExcerpt macros.
    
    Args:
        task_id: JIRA task key
        request_example: JSON request example
        header_params: Header parameters list
        formdata_params: FormData parameters (Swagger 2.0 style)
        request_fields: Request body fields
        success_example: Success response JSON
        success_fields: Success response fields
        error_examples: [(example, code, desc, fields), ...]
        field_changes: {path: 'added'|'modified'|'removed'}
        version_suffix: Suffix for duplicate tasks (e.g., " v2")
        page_title: Page title for MultiExcerpt include
        
    Returns:
        Confluence Storage Format HTML for the tab content
    """
    field_changes = field_changes or {}
    formdata_params = formdata_params or []
    request_fields = request_fields or []
    success_fields = success_fields or []
    error_examples = error_examples or []
    
    # Generate tables
    header_table = generate_header_params_table(header_params, field_changes)
    
    formdata_table = ""
    if formdata_params:
        formdata_table = generate_formdata_params_table(formdata_params, field_changes)
    
    request_fields_table = generate_fields_table(
        request_fields, field_changes, prefix="requestBody"
    )
    
    success_fields_table = generate_fields_table(
        success_fields, field_changes, prefix="responses.200"
    )
    
    # Code macros
    request_code = generate_code_macro(request_example, title="Request")
    success_code = generate_code_macro(success_example, title="Response")
    
    # Wrap in MultiExcerpts
    request_body_multiexcerpt = ""
    
    # Check if request body is effectively empty
    has_request_body = False
    if request_example:
        try:
            # Check for standard empty JSON structures
            parsed = json.loads(request_example)
            if parsed:  # Truthy check handles {}, [], "", None
                has_request_body = True
        except (json.JSONDecodeError, TypeError):
             # If it's not JSON but has content, treat as valid body
            if request_example.strip():
                has_request_body = True
    
    if has_request_body:
        request_code = generate_code_macro(request_example, title="Request")
        request_body_multiexcerpt = generate_multiexcerpt(
            name=f"{task_id}{version_suffix} Request Body",
            content=request_code
        )
    
    header_multiexcerpt = generate_multiexcerpt(
        name=f"{task_id}{version_suffix} Header Description",
        content=header_table
    )
    
    request_desc_multiexcerpt = ""
    if request_fields and request_fields_table:
        request_desc_multiexcerpt = generate_multiexcerpt(
            name=f"{task_id}{version_suffix} Request Description",
            content=request_fields_table
        )
    
    response_body_multiexcerpt = generate_multiexcerpt(
        name=f"{task_id}{version_suffix} Response Body",
        content=success_code
    )
    
    response_desc_multiexcerpt = generate_multiexcerpt(
        name=f"{task_id}{version_suffix} Response Description",
        content=success_fields_table
    )
    
    # FormData section
    formdata_section = ""
    if formdata_table:
        formdata_section = f"<p><strong>Параметры FormData</strong></p>\n{formdata_table}"
    
    # Request body section
    request_body_section = ""
    if request_desc_multiexcerpt:
        request_body_section = f"<p><strong>Тело запроса</strong></p>\n{request_desc_multiexcerpt}"
    
    # Error sections
    error_sections = []
    for error_example, error_code, error_desc, error_fields in error_examples:
        error_code_macro = generate_code_macro(error_example, title=f"Error {error_code}")
        error_fields_table = generate_fields_table(
            error_fields, field_changes, prefix=f"responses.{error_code}"
        )
        
        error_body_multiexcerpt = generate_multiexcerpt(
            name=f"{task_id}{version_suffix} Error {error_code} Body",
            content=error_code_macro
        )
        
        error_desc_multiexcerpt = generate_multiexcerpt(
            name=f"{task_id}{version_suffix} Error {error_code} Description",
            content=error_fields_table
        )
        
        error_sections.append(f'''<tr class="">
<th>Пример негативного ответа (HTTP {error_code})<br /><em>{error_desc}</em></th>
<th>Описание негативного ответа</th>
</tr>
<tr class="">
<td><div class="content-wrapper">{error_body_multiexcerpt}</div></td>
<td><div class="content-wrapper">{error_desc_multiexcerpt}</div></td>
</tr>''')
    
    error_content = '\n'.join(error_sections)
    
    # Generate Jira link
    jira_macro = generate_jira_macro(task_id) if task_id and task_id != 'NO-TASK' else task_id
    task_header = f'<p><strong>Задача:</strong> {jira_macro}</p>'
    
    # Build main table
    main_table = f'''<table class="wrapped" data-mce-resize="false">
<tbody class="">
<tr class="">
<th>Пример запроса</th>
<th>Описание запроса</th>
</tr>
<tr class="">
<td><div class="content-wrapper">{request_body_multiexcerpt}</div></td>
<td>
<p><strong>Параметры</strong></p>
{header_multiexcerpt}
{formdata_section}
{request_body_section}
</td>
</tr>
<tr class="">
<th>Пример успешного ответа</th>
<th>Описание ответа</th>
</tr>
<tr class="">
<td><div class="content-wrapper">{response_body_multiexcerpt}</div></td>
<td><div class="content-wrapper">{response_desc_multiexcerpt}</div></td>
</tr>
{error_content}
</tbody>
</table>'''
    
    # Wrap in structure MultiExcerpt
    structure_multiexcerpt = generate_multiexcerpt(
        name=f"{task_id}{version_suffix} Структура обмена + Описание",
        content=main_table
    )
    
    # Hidden "Контракт" MultiExcerpt
    contract_includes = f'''<ul>
<li><strong><em>API метод: </em></strong>{generate_multiexcerpt_include("Метод и endpoint", page_title=page_title)}</li>
<li><em><strong>Описание метода: </strong></em>{generate_multiexcerpt_include("Краткое описание метода", page_title=page_title)}</li>
</ul>
<p>{generate_multiexcerpt_include(f"{task_id}{version_suffix} Структура обмена + Описание", page_title=page_title)}</p>'''
    
    contract_multiexcerpt = generate_multiexcerpt(
        name=f"{task_id}{version_suffix} Контракт",
        content=contract_includes
    )
    
    hidden_contract = generate_hidden_excerpt(contract_multiexcerpt)
    
    return task_header + structure_multiexcerpt + hidden_contract


# =============================================================================
# Full Page Generation
# =============================================================================

def generate_full_page(
    method: str,
    path: str,
    summary: str,
    description: str,
    history_tabs_content: str,
    events: Optional[List[Dict[str, Any]]] = None,
    environments_page_info: Optional[Dict[str, str]] = None,
    service_name: str = "",
    page_title: Optional[str] = None
) -> str:
    """
    Generate complete documentation page.
    
    Args:
        method: HTTP method (GET, POST, etc.)
        path: Endpoint path
        summary: Short description
        description: Detailed description
        history_tabs_content: ui-tabs with version history
        events: Change events for history block
        environments_page_info: Dict with {id, title, space_key} of "Данные для автоматизации" page
        
    Returns:
        Complete Confluence Storage Format HTML page
    """
    if not description:
        description = 'Документация API метода с историей изменений'
    
    # Generate macro IDs
    details_id = generate_macro_id()
    multiexcerpt_summary_id = generate_macro_id()
    multiexcerpt_endpoint_id = generate_macro_id()
    multiexcerpt_contract_id = generate_macro_id()
    multiexcerpt_main_contract_id = generate_macro_id()
    excerpt_hidden_id = generate_macro_id()
    
    status_macro = generate_status_macro(method)
    
    # Change history block
    change_history_block = generate_change_history_block(events or [])
    
    # Environments section
    environments_include = _generate_environments_section(environments_page_info)
    
    return f'''<ac:layout>{environments_include}
<ac:layout-section ac:type="single">
<ac:layout-cell>
<ac:structured-macro ac:name="details" ac:schema-version="1" ac:macro-id="{details_id}"><ac:rich-text-body>
<table class="relative-table wrapped" style="width: 64.804%;">
<colgroup>
<col style="width: 11.9124%;" />
<col style="width: 88.0876%;" />
</colgroup>
<tbody>
<tr>
<th>Краткое описание</th>
<td>
<div class="content-wrapper">
<ac:structured-macro ac:name="multiexcerpt" ac:schema-version="1" ac:macro-id="{multiexcerpt_summary_id}"><ac:parameter ac:name="MultiExcerptName">Краткое описание метода</ac:parameter><ac:parameter ac:name="atlassian-macro-output-type">INLINE</ac:parameter><ac:rich-text-body><p>{summary}</p></ac:rich-text-body></ac:structured-macro>
</div>
</td>
</tr>
<tr>
<th>
<p>Метод и endpoint</p>
</th>
<td>
<div class="content-wrapper">
<ac:structured-macro ac:name="multiexcerpt" ac:schema-version="1" ac:macro-id="{multiexcerpt_endpoint_id}"><ac:parameter ac:name="MultiExcerptName">Метод и endpoint</ac:parameter><ac:parameter ac:name="atlassian-macro-output-type">INLINE</ac:parameter><ac:rich-text-body><p>{status_macro} {path}</p></ac:rich-text-body></ac:structured-macro>
<p style="letter-spacing: 0.0px;"> </p>
</div>
</td>
</tr>
<tr>
<th>
<p>Описание последних изменений</p>
</th>
<td>
<div class="content-wrapper">{change_history_block}</div>
</td>
</tr>
<tr>
<th>
<p>Подробное описание</p>
</th>
<td>
<p>{description}</p>
</td>
</tr>
<tr>
<th>Легенда изменений</th>
<td>
<p><span style="background-color: {COLOR_ADDED}; padding: 2px 8px;">🟢 Добавлено</span> &nbsp; <span style="background-color: {COLOR_MODIFIED}; padding: 2px 8px;">🟡 Изменено</span> &nbsp; <span style="background-color: {COLOR_REMOVED}; padding: 2px 8px;">🔴 Удалено</span></p>
<p><em>Цвета показывают изменения относительно предыдущей версии</em></p>
</td>
</tr>
<tr>
<th>Пример использования</th>
<td>
<div class="content-wrapper">
<ac:structured-macro ac:name="multiexcerpt" ac:schema-version="1" ac:macro-id="{multiexcerpt_contract_id}"><ac:parameter ac:name="MultiExcerptName">Версии контракта</ac:parameter><ac:parameter ac:name="atlassian-macro-output-type">INLINE</ac:parameter><ac:rich-text-body>{history_tabs_content}</ac:rich-text-body></ac:structured-macro>
</div>
</td>
</tr>
</tbody>
</table>
</ac:rich-text-body></ac:structured-macro>
</ac:layout-cell>
</ac:layout-section>
<ac:layout-section ac:type="single">
<ac:layout-cell>
<ac:structured-macro ac:name="excerpt" ac:schema-version="1" ac:macro-id="{excerpt_hidden_id}"><ac:parameter ac:name="hidden">true</ac:parameter><ac:rich-text-body>
<ac:structured-macro ac:name="multiexcerpt" ac:schema-version="1" ac:macro-id="{multiexcerpt_main_contract_id}"><ac:parameter ac:name="MultiExcerptName">Контракт</ac:parameter><ac:parameter ac:name="atlassian-macro-output-type">INLINE</ac:parameter><ac:rich-text-body>
<table class="wrapped">
<colgroup>
<col style="width: 20%;" />
<col style="width: 80%;" />
</colgroup>
<tbody>
<tr>
<th>Сервис</th>
<td>{service_name}</td>
</tr>
<tr>
<th>API метод</th>
<td><div class="content-wrapper">{generate_multiexcerpt_include("Метод и endpoint", page_title=page_title)}</div></td>
</tr>
<tr>
<th>Описание метода</th>
<td><div class="content-wrapper">{generate_multiexcerpt_include("Краткое описание метода", page_title=page_title)}</div></td>
</tr>
<tr>
<th>Контракт</th>
<td><div class="content-wrapper">{generate_multiexcerpt_include("Версии контракта", page_title=page_title)}</div></td>
</tr>
</tbody>
</table>
</ac:rich-text-body></ac:structured-macro>
</ac:rich-text-body></ac:structured-macro>
</ac:layout-cell>
</ac:layout-section>
</ac:layout>'''


def _generate_environments_section(environments_page_info: Optional[Dict[str, str]]) -> str:
    """Generate environments section with MultiExcerpt include or fallback table."""
    if environments_page_info:
        page_title = environments_page_info.get('title')
        space_key = environments_page_info.get('space_key')
        multiexcerpt_include_id = generate_macro_id()
        
        # Use Title + Space Key for robust linking
        # Conditionally add space-key if present
        space_attr = f' ri:space-key="{space_key}"' if space_key else ''
        
        return f'''
<ac:layout-section ac:type="single">
<ac:layout-cell>
<ac:structured-macro ac:name="multiexcerpt-include" ac:schema-version="1" ac:macro-id="{multiexcerpt_include_id}">
<ac:parameter ac:name="MultiExcerptName">Адреса стендов</ac:parameter>
<ac:parameter ac:name="PageWithExcerpt">
<ac:link>
<ri:page ri:content-title="{page_title}"{space_attr} />
</ac:link>
</ac:parameter>
</ac:structured-macro>
</ac:layout-cell>
</ac:layout-section>
'''
    else:
        return f'''
<ac:layout-section ac:type="single">
<ac:layout-cell>
<table class="wrapped">
<colgroup>
<col />
<col />
</colgroup>
<tbody>
<tr>
<th>Система</th>
<th colspan="1">-</th>
</tr>
</tbody>
</table>
</ac:layout-cell>
</ac:layout-section>
'''
