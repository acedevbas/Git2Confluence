"""
Confluence Template Generator - FACADE for backward compatibility.

This module delegates to the new modular implementation while maintaining
the original API for backward compatibility.

New code should import directly from:
- src.confluence.macros
- src.confluence.schema_utils
- src.confluence.tables
- src.confluence.history_block
- src.confluence.page_builder

DEPRECATED: This class will be removed in a future version.
"""
import json
import uuid
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# Import from new modules
from .macros import (
    generate_macro_id,
    generate_status_macro,
    generate_code_macro,
    generate_jira_macro,
    generate_multiexcerpt,
    generate_multiexcerpt_include,
    generate_hidden_excerpt,
    generate_expand_macro,
    generate_warning_macro,
    JIRA_SERVER_NAME,
    JIRA_SERVER_ID,
)
from .schema_utils import (
    flatten_schema_to_fields,
    generate_example_from_schema,
    is_schema_poor,
)
from .tables import (
    generate_header_params_table,
    generate_formdata_params_table,
    generate_fields_table,
    COLOR_ADDED,
    COLOR_MODIFIED,
    COLOR_REMOVED,
)
from .history_block import (
    generate_change_history_block,
    _generate_detailed_change_description,
    _parse_diff_path,
    _extract_full_field_path,
)
from .page_builder import (
    generate_full_page,
    generate_task_tab_content,
    generate_ui_tabs,
    generate_ui_tab,
)

logger = logging.getLogger(__name__)


class ConfluenceTemplateGenerator:
    """
    DEPRECATED: Use functions from submodules directly.
    
    This class is a facade for backward compatibility.
    New code should import from:
    - src.confluence.macros
    - src.confluence.schema_utils
    - src.confluence.tables
    - src.confluence.history_block
    - src.confluence.page_builder
    """
    
    # Constants (delegated)
    JIRA_SERVER_NAME = JIRA_SERVER_NAME
    JIRA_SERVER_ID = JIRA_SERVER_ID
    COLOR_ADDED = COLOR_ADDED
    COLOR_MODIFIED = COLOR_MODIFIED
    COLOR_REMOVED = COLOR_REMOVED
    
    # =========================================================================
    # Macros (delegated to macros.py)
    # =========================================================================
    
    @staticmethod
    def generate_macro_id() -> str:
        return generate_macro_id()
    
    @staticmethod
    def generate_status_macro(method: str) -> str:
        return generate_status_macro(method)
    
    @staticmethod
    def generate_code_macro(code: str, language: str = 'json', collapse: bool = True, title: str = None) -> str:
        return generate_code_macro(code, language, collapse, title)
    
    @classmethod
    def generate_jira_macro(cls, task_key: str) -> str:
        return generate_jira_macro(task_key)
    
    @classmethod
    def generate_multiexcerpt(cls, name: str, content: str, output_type: str = "INLINE") -> str:
        return generate_multiexcerpt(name, content, output_type)
    
    @classmethod
    def generate_multiexcerpt_include(cls, name: str, page_title: Optional[str] = None) -> str:
        return generate_multiexcerpt_include(name, page_title)
    
    @classmethod
    def generate_hidden_excerpt(cls, content: str) -> str:
        return generate_hidden_excerpt(content)
    
    @classmethod
    def generate_warning_macro(cls, title: Optional[str], content: str) -> str:
        return generate_warning_macro(title, content)
    
    # =========================================================================
    # Tables (delegated to tables.py)
    # =========================================================================
    
    @classmethod
    def generate_header_params_table(cls, params: List[Dict[str, Any]], field_changes: Dict[str, str] = None) -> str:
        return generate_header_params_table(params, field_changes)
    
    @classmethod
    def generate_formdata_params_table(cls, params: List[Dict[str, Any]], field_changes: Dict[str, str] = None) -> str:
        return generate_formdata_params_table(params, field_changes)
    
    @classmethod
    def generate_fields_table(cls, fields: List[Dict[str, Any]], field_changes: Dict[str, str] = None, prefix: str = "") -> str:
        return generate_fields_table(fields, field_changes, prefix)
    
    # =========================================================================
    # History Block (delegated to history_block.py)
    # =========================================================================
    
    @classmethod
    def generate_change_history_block(cls, events: List[Dict[str, Any]]) -> str:
        return generate_change_history_block(events)
    
    @classmethod
    def _generate_detailed_change_description(
        cls,
        diff: Dict[str, Any],
        current_schema: Optional[Dict[str, Any]] = None,
        previous_schema: Optional[Dict[str, Any]] = None
    ) -> str:
        return _generate_detailed_change_description(diff, current_schema, previous_schema)
    
    @classmethod
    def _parse_diff_path(cls, path: str) -> Tuple[Optional[str], Optional[str]]:
        return _parse_diff_path(path)
    
    @classmethod
    def _extract_full_field_path(cls, deepdiff_path: str) -> Tuple[Optional[str], Optional[str]]:
        return _extract_full_field_path(deepdiff_path)
    
    # =========================================================================
    # Page Builder (delegated to page_builder.py)
    # =========================================================================
    
    @classmethod
    def generate_ui_tab(cls, title: str, content: str) -> str:
        return generate_ui_tab(title, content)
    
    @classmethod
    def generate_ui_tabs(cls, tabs: List[Tuple[str, str]]) -> str:
        return generate_ui_tabs(tabs)
    
    @classmethod
    def generate_task_tab_content(
        cls,
        task_id: str,
        request_example: str,
        header_params: List[Dict[str, Any]],
        formdata_params: List[Dict[str, Any]] = None,
        request_fields: List[Dict[str, Any]] = None,
        success_example: str = "",
        success_fields: List[Dict[str, Any]] = None,
        error_examples: List[Tuple[str, str, str, List[Dict[str, Any]]]] = None,
        field_changes: Optional[Dict[str, str]] = None,
        version_suffix: str = "",
        page_title: Optional[str] = None
    ) -> str:
        return generate_task_tab_content(
            task_id, request_example, header_params, formdata_params,
            request_fields, success_example, success_fields, error_examples,
            field_changes, version_suffix, page_title
        )
    
    @classmethod
    def generate_full_page(
        cls,
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
        return generate_full_page(
            method, path, summary, description,
            history_tabs_content, events, environments_page_info,
            service_name, page_title
        )


# Re-export for backward compatibility at module level
__all__ = [
    'ConfluenceTemplateGenerator',
    'flatten_schema_to_fields',
    'generate_example_from_schema',
]
