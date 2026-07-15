"""
Confluence Publisher for OpenAPI History Tracker.
Publishes endpoint change history to Confluence pages with full documentation.

Uses atlassian-python-api library for Confluence Server/Data Center.
Template based on reference page (439912504) with ui-tabs for each JIRA task.
"""
import os
import json
import logging
from typing import Dict, Any, Optional, List, Set, Tuple
from datetime import datetime
from atlassian import Confluence
from config import settings
from .constants import COSMETIC_FIELDS
from .template_generator import (
    ConfluenceTemplateGenerator,
    flatten_schema_to_fields,
    generate_example_from_schema
)
from .content_preserver import ContentPreserver

logger = logging.getLogger(__name__)


class ConfluencePublisher:
    """
    Publishes OpenAPI endpoint history to Confluence.
    
    Creates or updates a Confluence page with:
    - Endpoint metadata (method, path, summary)
    - UI-tabs for each JIRA task in history
    - JSON examples for request/response
    - Field tables with change highlighting (green=added, yellow=modified)
    
    Uses Confluence Storage Format (XHTML-like) for rich formatting.
    """
    
    # Pre-computed suffixes for DeepDiff path checking
    COSMETIC_SUFFIXES = tuple(f"['{field}']" for field in COSMETIC_FIELDS)
    
    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        space_key: str = "pickup",
        parent_page_id: Optional[str] = None
    ):
        """
        Initialize Confluence publisher.
        
        Args:
            base_url: Confluence server URL (from settings if not provided)
            token: Personal access token (from settings if not provided)
            space_key: Confluence space key (default: "pickup")
            parent_page_id: Optional parent page ID for new pages
        """
        self.base_url = (base_url or settings.confluence_base_url or "").rstrip("/")
        self.token = token or settings.confluence_token
        self.space_key = space_key or settings.confluence_space_key or "pickup"
        self.parent_page_id = parent_page_id or settings.confluence_parent_page_id
        
        if not self.base_url or not self.token:
            raise ValueError(
                "CONFLUENCE_BASE_URL and CONFLUENCE_TOKEN must be set "
                "either via arguments, settings, or environment variables"
            )
        
        
        # Initialize Confluence client
        self.confluence = Confluence(
            url=self.base_url,
            token=self.token,
            cloud=False  # For Confluence Server/Data Center
        )
        
        self.content_preserver = ContentPreserver()
        
        logger.info(f"Confluence publisher initialized: {self.base_url}")
    
    def check_connection(self) -> Dict[str, Any]:
        """
        Check Confluence connection and space access.
        
        Returns:
            Dict with connection status and space info
        """
        try:
            # Get space info
            space = self.confluence.get_space(self.space_key)
            return {
                "connected": True,
                "space_key": space.get("key"),
                "space_name": space.get("name"),
                "space_id": space.get("id")
            }
        except Exception as e:
            return {
                "connected": False,
                "error": str(e)
            }
    
    def list_spaces(self, limit: int = 50) -> List[Dict[str, str]]:
        """
        List available Confluence spaces.
        
        Args:
            limit: Maximum number of spaces to return
        
        Returns:
            List of dicts with key, name, type
        """
        try:
            spaces = self.confluence.get_all_spaces(limit=limit)
            result = []
            for space in spaces.get("results", []):
                result.append({
                    "key": space.get("key"),
                    "name": space.get("name"),
                    "type": space.get("type")
                })
            return result
        except Exception as e:
            logger.error(f"Error listing spaces: {e}")
            return []
    
    def find_environments_page(self) -> Optional[Dict[str, str]]:
        """
        Find environments page in parent hierarchy.
        
        Delegates to EnvironmentsBlock for configurable page lookup.
        Searches only in parent hierarchy of parent_page_id via CQL.
        
        Returns:
            Dict {'id', 'title', 'space_key'} if found, None otherwise
        """
        from .environments import EnvironmentsBlock
        
        env_block = EnvironmentsBlock(
            confluence_client=self.confluence,
            space_key=self.space_key,
            parent_page_id=self.parent_page_id  # Search in hierarchy only
        )
        env_block.find_page()
        return env_block.get_page_info()
    
    def publish_history(
        self,
        method: str,
        path: str,
        events: List[Dict[str, Any]],
        project_path: str = "",
        project_name: str = "",
        summary: str = "",
        description: str = ""
    ) -> Dict[str, Any]:
        """
        Publish endpoint change history to Confluence with full documentation.

        Args:
            method: HTTP method (GET, POST, etc.)
            path: Endpoint path (/orders/products/return, etc.)
            events: List of change events from TurboHistoryBuilder
            project_path: GitLab project path for title prefix extraction
            project_name: Project display name from config (used for title prefix)
            summary: Endpoint summary (from OpenAPI)
            description: Endpoint description
        
        Returns:
            Dict with page_id, page_url, title, success status
        """
        # Get summary/description from latest event's schema if not provided
        if not summary and events:
            latest_schema = None
            for event in reversed(events):
                if event.get('schema'):
                    latest_schema = event['schema']
                    break
            if latest_schema:
                summary = latest_schema.get('summary', '')
                # Use description from schema, fall back to default only if not present
                if not description:
                    description = latest_schema.get('description', 'Документация API метода с историей изменений')
        
        # Generate title prefix from project_name (from config) or fallback to project_path
        if project_name:
            display_name = project_name
        elif project_path:
            display_name = project_path.rstrip('/').split('/')[-1]
        else:
            display_name = "API"
        title_prefix = f"[{display_name.upper()}]"
        
        # Generate page title using summary (not path)
        title_text = summary if summary else path.replace("/", " ").strip()
        title = f"{title_prefix} {title_text} ({method.upper()} {path})"
        
        # Find environments page for MultiExcerpt include
        environments_page_info = self.find_environments_page()
        
        # Generate Confluence Storage Format content and get events with metadata
        content, events_with_metadata = self._generate_full_page_content(
            method, path, events, summary, description, environments_page_info, page_title=title,
            service_name=display_name.upper()
        )
        
        # Find existing page or create new one
        existing_page = self._find_page_by_title(title)
        
        try:
            if existing_page:
                # Update existing page
                page_id = existing_page["id"]
                
                # PRESERVE DESCRIPTIONS LOGIC
                preserved_descriptions = None
                # 1. Fetch current page content to extract existing descriptions
                try:
                    current_page = self.confluence.get_page_by_id(page_id, expand='body.storage')
                    current_body = current_page.get('body', {}).get('storage', {}).get('value', '')
                    
                    if current_body:
                        # 2. Extract detailed description (from MultiExcerpt "description")
                        # Look for <ac:parameter ac:name="Name">description</ac:parameter>...<ac:rich-text-body>...CONTENT...</ac:rich-text-body>
                        # OR extracting from the specific HTML structure if not using macros
                        # Simpler approach: Look for the specific MultiExcerpt block if we use it, 
                        # OR if we just put it in a dict, we rely on our template.
                        
                        # Let's try to extract from the generated structure in page_builder.py
                        # It puts description inside a multiexcerpt named 'description'
                        
                        # Regex to find content inside MultiExcerpt named "description"
                        # WAIT! In page_builder.py, "Detailed Description" is NOT in a MultiExcerpt.
                        # It is in a table row:
                        # <tr>
                        # <th><p>Подробное описание</p></th>
                        # <td><p>CONTENT</p></td>
                        # </tr>
                        
                        import re
                        # Try to capture the content inside the <td> following the <th>Подробное описание</th>
                        # Using non-greedy match for content.
                        # Note: Confluence storage format can be complex, but usually consistent.
                        # We look for "Подробное описание" then the next <td>...</td>
                        
                        # Pattern: <th>...Подробное описание...</th>...<td>(.*?)</td>
                        desc_pattern = r'Подробное описание.*?(?:</th>|</td>).*?<td>(.*?)</td>'
                        desc_match = re.search(desc_pattern, current_body, re.DOTALL)
                        
                        if desc_match:
                            existing_desc_raw = desc_match.group(1).strip()
                            # Clean up wrapping <p> tags if present
                            # Usually simple content is wrapped in <p>
                            if existing_desc_raw.startswith('<p>') and existing_desc_raw.endswith('</p>'):
                                existing_desc = existing_desc_raw[3:-4].strip()
                            else:
                                existing_desc = existing_desc_raw
                                
                            # If existing description is not empty and not EXACTLY the default placeholder
                            if existing_desc and existing_desc.strip() != "Документация API метода с историей изменений":
                                description = existing_desc
                                logger.info(f"Preserving existing description for page {page_id}")
                                
                        # 2b. Extract table descriptions using ContentPreserver
                        preserved_descriptions = self.content_preserver.extract_descriptions(current_body)
                        if preserved_descriptions:
                            logger.info(f"Preserver extracted edits for {len(preserved_descriptions)} tasks")
                                
                        # 3. Preserve "Краткое описание" (Summary)
                        summary_pattern = r'<ac:parameter ac:name="MultiExcerptName">Краткое описание метода</ac:parameter>.*?<ac:rich-text-body>(.*?)</ac:rich-text-body>'
                        summary_match = re.search(summary_pattern, current_body, re.DOTALL)
                        
                        if summary_match:
                            existing_summary_raw = summary_match.group(1).strip()
                            if existing_summary_raw.startswith('<p>') and existing_summary_raw.endswith('</p>'):
                                existing_summary = existing_summary_raw[3:-4].strip()
                            else:
                                existing_summary = existing_summary_raw
                                
                            if existing_summary and existing_summary != summary:
                                summary = existing_summary
                                logger.info(f"Preserving existing summary for page {page_id}")
                                
                        # 3. Extract brief summary if needed (usually it's the title, but if there's a block for it)
                        # We only have 'summary' which is usually the title.
                        # If the user meant "Brief Description" as a separate field, we should check if we render it.
                        # Based on page_builder, we render 'description' variable.
                        
                except Exception as ex:
                    logger.warning(f"Failed to extract existing content to preserve descriptions: {ex}")

                # Regenerate content with potentially preserved description
                content, events_with_metadata = self._generate_full_page_content(
                    method, path, events, summary, description, environments_page_info, 
                    page_title=title, preserved_descriptions=preserved_descriptions,
                    service_name=display_name.upper()
                )



                result = self.confluence.update_page(
                    page_id=page_id,
                    title=title,
                    body=content,
                    parent_id=self.parent_page_id,
                    type="page",
                    representation="storage"
                )
                logger.info(f"Updated Confluence page: {page_id}")
            else:
                # Create new page
                result = self.confluence.create_page(
                    space=self.space_key,
                    title=title,
                    body=content,
                    parent_id=self.parent_page_id,
                    type="page",
                    representation="storage"
                )
                page_id = result["id"]
                logger.info(f"Created Confluence page: {page_id}")
            
            page_url = f"{self.base_url}/pages/viewpage.action?pageId={page_id}"
            
            return {
                "success": True,
                "page_id": page_id,
                "page_url": page_url,
                "title": title,
                "events_count": len(events_with_metadata),
                "events": events_with_metadata  # Include events with field_changes for API
            }
        except Exception as e:
            logger.error(f"Error publishing to Confluence: {e}")
            return {
                "success": False,
                "error": str(e),
                "title": title,
                "events_count": len(events_with_metadata),
                "events": events_with_metadata  # Return events even on error for API
            }
    
    def _find_page_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """Find a Confluence page by title in the configured space."""
        try:
            # CQL query for exact title match
            cql = f'space="{self.space_key}" AND title="{title}"'
            results = self.confluence.cql(cql, limit=1)
            
            if results and "results" in results and results["results"]:
                page_info = results["results"][0]
                # CQL may return different structures
                if "id" in page_info:
                    return page_info
                elif "content" in page_info and "id" in page_info["content"]:
                    return page_info["content"]
            return None
        except Exception as e:
            logger.warning(f"Error searching for page '{title}': {e}")
            return None
    
    def _is_schema_poor(self, endpoint_schema: Dict[str, Any]) -> bool:
        """
        Check if ENTIRE endpoint schema is "poor" - truly useless/incomplete.
        
        Schema is "poor" ONLY if ALL of these are true:
        1. Response has no details (result without structure)
        2. No meaningful parameters
        3. No detailed requestBody
        
        If endpoint has ANY useful structure (params/body/response), it's NOT poor!
        
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
        # Often first allOf has generic structure, second has actual details
        if 'allOf' in success_schema:
            merged_result = {}
            for item in success_schema.get('allOf', []):
                props = item.get('properties', {})
                result = props.get('result', {})
                
                # Merge result definitions from all allOf items
                if result:
                    # If any allOf item has detailed result, merge it
                    if '$ref' in result or 'items' in result or 'properties' in result or 'allOf' in result:
                        # This allOf item has detailed result - NOT poor!
                        return False
                    
                    # Accumulate for final check
                    for key in ['type', 'properties', 'items', 'allOf']:
                        if key in result:
                            merged_result[key] = result[key]
            
            # Check merged result
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
        
        # No params, no body, no response details - truly poor
        return True

    def _generate_full_page_content(
        self,
        method: str,
        path: str,
        events: List[Dict[str, Any]],
        summary: str,
        description: str,
        environments_page_info: Optional[Dict[str, str]] = None,
        page_title: Optional[str] = None,
        preserved_descriptions: Optional[Dict[str, Dict[str, Dict[str, str]]]] = None,
        service_name: str = ""
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """
        Generate full Confluence page content with ui-tabs for each JIRA task.
        
        Skips events with "poor" schemas (incomplete OpenAPI specs from legacy format).
        For the first rich schema event, no diff highlighting is shown since previous
        schema was poor.
        
        Args:
            environments_page_info: Dict with {id, title, space_key} of "Данные для автоматизации" page
        """
        gen = ConfluenceTemplateGenerator
        
        # Group events by JIRA task (newest first for tabs)
        events_sorted = list(reversed(events))  # Newest first
        
        # Count duplicate task IDs for version suffix
        task_id_counts = {}
        for event in events_sorted:
            task_id = event.get('task_id', 'NO-TASK')
            if task_id != 'REVERT':
                task_id_counts[task_id] = task_id_counts.get(task_id, 0) + 1
        
        # Track version numbers for tasks with duplicates
        task_version_tracker = {task_id: 1 for task_id in task_id_counts}
        
        # Generate tabs for each event with schema
        tabs = []
        skipped_tasks = []
        
        # Track events with metadata for change history block
        # Each event gets a _skip_reason if it should be excluded or noted
        events_with_metadata = []
        
        # Track last "rich" schema for proper diff comparison
        # We process newest-first, so we track going backwards
        last_rich_schema_seen = None
        
        for event in events_sorted:
            task_id = event.get('task_id', 'NO-TASK')
            # if task_id == 'REVERT':
            #     continue  # Skip REVERT events
            
            # Create a copy of event with metadata
            event_meta = dict(event)
            event_meta['_skip_reason'] = None  # Default: not skipped
            
            schema = event.get('schema')
            previous_schema = event.get('previous_schema')
            diff = event.get('diff')
            
            if not schema:
                # Event without schema (e.g., DELETED or CREATED without current snapshot)
                event_type = event.get('type', '')
                if 'DELETED' in event_type:
                    mr_link = event.get('link', '')
                    mr_id = event.get('mr_id', '')
                    date = event.get('date', '')
                    author = event.get('author', '')
                    
                    tab_content = f'''
<ac:structured-macro ac:name="warning">
  <ac:rich-text-body>
    <p><strong>🗑️ API метод удалён</strong></p>
  </ac:rich-text-body>
</ac:structured-macro>
<table>
  <tbody>
    <tr>
      <td style="width: 120px;"><strong>Merge Request</strong></td>
      <td><a href="{mr_link}">!{mr_id}</a></td>
    </tr>
    <tr>
      <td><strong>Дата</strong></td>
      <td>{date}</td>
    </tr>
    <tr>
      <td><strong>Автор</strong></td>
      <td>{author}</td>
    </tr>
  </tbody>
</table>'''
                    tabs.append((task_id, tab_content))
                    events_with_metadata.append(event_meta)
                elif 'CREATED' in event_type or 'REVERT' in event_type:
                    # CREATED or REVERT event - add to history even without detailed schema
                    events_with_metadata.append(event_meta)
                    logger.info(f"[CREATED/REVERT] Task {task_id}: Added to history without detailed tab (no schema)")
                continue
            
            # Check if schema is "poor" (lacks ANY useful structure)
            # Pass FULL endpoint schema, not just response
            # For CREATED/DELETED events, still generate a tab (just without detailed structure)
            # For MODIFIED events with poor schema, skip the tab entirely
            event_type = event.get('type', '')
            is_poor_schema = self._is_schema_poor(schema)  # Pass full schema, not just response!
            
            if is_poor_schema:
                # Poor schema handling depends on event type
                if 'MODIFIED' in event_type:
                    # MODIFIED with poor schema - skip tab entirely (no meaningful changes to show)
                    event_meta['_skip_reason'] = 'poor_schema'
                    skipped_tasks.append(task_id)
                    events_with_metadata.append(event_meta)
                    logger.debug(f"[SKIP] Task {task_id}: Poor schema on MODIFIED - skipping tab")
                    continue
                elif 'CREATED' in event_type or 'REVERT' in event_type:
                    # CREATED or REVERT with poor schema - add to history but skip detailed tab
                    # This prevents duplicate addition at line 416
                    event_meta['_skip_reason'] = 'poor_schema'
                    events_with_metadata.append(event_meta)
                    logger.info(f"[CREATED-POOR] Task {task_id}: Added to history, skipping detailed tab")
                    continue  # Skip to next event - don't add twice!
                else:
                    # DELETED or other - skip tab
                    event_meta['_skip_reason'] = 'poor_schema'
                    skipped_tasks.append(task_id)
                    events_with_metadata.append(event_meta)
                    logger.debug(f"[SKIP] Task {task_id}: Poor schema on {event_type} - skipping tab")
                    continue
            
            # Check if previous_schema was poor - if so, don't show diff highlighting
            # This is the first "rich" schema, so nothing to compare against
            use_diff = True
            if previous_schema:
                # Pass FULL previous schema, not just response!
                if self._is_schema_poor(previous_schema):
                    # Previous schema was poor - don't show diff for this event
                    use_diff = False
                    logger.info(f"[NO-DIFF] Task {task_id}: Previous schema was poor - no diff highlighting")
            
            # Calculate field changes from diff only if previous was also rich
            field_changes = {}
            if use_diff and diff:
                field_changes = self._extract_field_changes(
                    diff, 
                    current_schema=schema,
                    previous_schema=previous_schema
                )
            
            # If this is a MODIFIED event but all changes were filtered out as insignificant,
            # skip this event entirely - it only had minor format changes
            event_type = event.get('type', '')
            if 'MODIFIED' in event_type:
                 # Debug logging to diagnose why skip condition fails
                 log_diff_keys = list(diff.keys()) if diff else "None"
                 log_fc_keys = list(field_changes.keys())
                 logger.debug(f"[SKIP DEBUG] Task {task_id}: use_diff={use_diff}, diff={bool(diff)}, field_changes={bool(field_changes)}")
                 logger.debug(f"[SKIP DEBUG] Diff keys: {log_diff_keys}")
                 
                 # Check if diff exists but field_changes is empty (insignificant changes)
                 # Even if use_diff is False (poor schema), if we have a diff and it looks cosmetic, skip it.
                 is_cosmetic = False
                 if diff and not field_changes:
                     # Check manually if diff is purely cosmetic (fallback if field_changes extraction didn't catch it)
                     # or if field_changes became empty after filtering false positives
                     is_cosmetic = True 
                     
                     # Verify diff is not huge/structural before skipping
                     # If diff is massive but field_changes empty (failed extraction?), we shouldn't skip blindly.
                     # But for typical "description added" cases, diff is small.
                     pass 

                 if (use_diff and diff and not field_changes) or (diff and self._is_diff_purely_cosmetic(diff)):
                    skipped_tasks.append(task_id)
                    event_meta['_skip_reason'] = 'insignificant'
                    # Don't add to events_with_metadata - completely hide insignificant changes
                    logger.debug(f"[SKIP] Task {task_id}: All changes were insignificant (cosmetic/format-only)")
                    continue
            
            # DEBUG: Print field_changes for each task
            if field_changes:
                logger.debug(f"[DEBUG] Task {task_id}: field_changes = {field_changes}")
                # Store field_changes in event metadata for API response
                event_meta['field_changes'] = field_changes
            else:
                logger.debug(f"[DEBUG] Task {task_id}: NO field_changes, use_diff={use_diff}")
            
            # This event has significant changes - add to metadata list
            events_with_metadata.append(event_meta)
            
            # Determine version suffix for duplicate task IDs
            version_suffix = ""
            if task_id_counts.get(task_id, 0) > 1:
                # This task appears multiple times - add version suffix
                version_num = task_version_tracker[task_id]
                task_version_tracker[task_id] += 1
                if version_num > 1:
                    version_suffix = f" v{version_num}"
            
            # Generate tab content for this version
            # Pass previous_schema only if it was rich (for removed fields detection)
            tab_content = self._generate_tab_content(
                schema=schema,
                previous_schema=previous_schema if use_diff else None,
                field_changes=field_changes,
                event=event,
                version_suffix=version_suffix,
                page_title=page_title,
                preserved_task_descriptions=preserved_descriptions.get(task_id) if preserved_descriptions else None
            )
            
            tabs.append((task_id, tab_content))
        
        # Log skipped tasks if any
        if skipped_tasks:
            logger.info(f"[SUMMARY] Skipped {len(skipped_tasks)} tasks: {skipped_tasks}")
        
        # If no tabs, create a single "Current" tab
        if not tabs:
            tabs.append(("Нет изменений", "<p>История изменений пуста</p>"))
        
        # Generate ui-tabs wrapper
        history_tabs_content = gen.generate_ui_tabs(tabs)
        
        # Generate full page with filtered events (only significant changes + poor schema notes)
        content = gen.generate_full_page(
            method=method,
            path=path,
            summary=summary or "API Endpoint",
            description=description,  # Already extracted from schema or set to default
            history_tabs_content=history_tabs_content,
            events=events_with_metadata,  # Pass filtered events with metadata
            environments_page_info=environments_page_info,  # For MultiExcerpt include
            service_name=service_name,
            page_title=page_title
        )
        
        # Return both content and events with metadata
        return content, events_with_metadata
    
    def _generate_tab_content(
        self,
        schema: Dict[str, Any],
        previous_schema: Optional[Dict[str, Any]],
        field_changes: Dict[str, str],
        event: Dict[str, Any],
        version_suffix: str = "",
        page_title: Optional[str] = None,
        preserved_task_descriptions: Optional[Dict[str, Dict[str, str]]] = None
    ) -> str:
        """
        Generate content for a single version tab.
        Includes removed fields from previous schema (marked with red).
        
        Args:
            schema: Current endpoint schema
            previous_schema: Previous version schema for diff highlighting
            field_changes: Dict of field path -> change type
            event: Event metadata (task_id, date, etc.)
            version_suffix: Version suffix for duplicate task IDs (e.g., " v2")
            page_title: Page title for MultiExcerpt includes (same-page references)
        """
        gen = ConfluenceTemplateGenerator
        
        # Extract header parameters (current + removed from previous)
        header_params = []
        params = schema.get('parameters', {})
        current_headers = set()
        for param_name, param_info in params.get('header', {}).items():
            current_headers.add(param_name)
            header_params.append({
                'name': param_name,
                'type': param_info.get('type', 'string'),
                'required': param_info.get('required', False),
                'description': param_info.get('description', '')
            })
        
        # Add removed headers from previous schema
        if previous_schema:
            prev_params = previous_schema.get('parameters', {})
            for param_name, param_info in prev_params.get('header', {}).items():
                if param_name not in current_headers:
                    header_params.append({
                        'name': param_name,
                        'type': param_info.get('type', 'string'),
                        'required': param_info.get('required', False),
                        'description': param_info.get('description', '')
                    })
                    field_changes[f"header.{param_name}"] = 'removed'
        
        # Extract formData parameters (Swagger 2.0 style)
        formdata_params = []
        current_formdata = set()
        for param_name, param_info in params.get('formData', {}).items():
            current_formdata.add(param_name)
            param_type = param_info.get('type', 'string')
            param_format = param_info.get('format', '')
            # Format like "file(formData)" for type=file
            type_display = f"{param_type}({param_format})" if param_format else param_type
            if param_type == 'file':
                type_display = "file(formData)"
            
            formdata_params.append({
                'name': param_name,
                'type': type_display,
                'required': param_info.get('required', False),
                'description': param_info.get('description', '')
            })
        
        # Add removed formData from previous schema
        if previous_schema:
            prev_params = previous_schema.get('parameters', {})
            for param_name, param_info in prev_params.get('formData', {}).items():
                if param_name not in current_formdata:
                    param_type = param_info.get('type', 'string')
                    type_display = f"{param_type}(formData)" if param_type == 'file' else param_type
                    formdata_params.append({
                        'name': param_name,
                        'type': type_display,
                        'required': param_info.get('required', False),
                        'description': param_info.get('description', '')
                    })
                    field_changes[f"formData.{param_name}"] = 'removed'
        
        # Extract request body fields
        request_body = schema.get('requestBody', {})
        request_fields = flatten_schema_to_fields(request_body)
        
        # Add removed request body fields from previous schema
        if previous_schema:
            prev_request_body = previous_schema.get('requestBody', {})
            prev_request_fields = flatten_schema_to_fields(prev_request_body)
            request_fields = self._merge_fields_with_removed(
                request_fields, prev_request_fields, field_changes, "requestBody"
            )
            
        # apply preserved descriptions for request fields
        if preserved_task_descriptions and 'request' in preserved_task_descriptions:
            self._apply_preserved_descriptions(request_fields, preserved_task_descriptions['request'])
        
        # Generate request example
        request_example = json.dumps(
            generate_example_from_schema(request_body),
            indent=2,
            ensure_ascii=False,
            default=str,  # tolerate non-JSON values (e.g. datetime examples)
        )
        
        # Extract success response fields (200)
        responses = schema.get('responses', {})
        success_response = responses.get('200', responses.get('201', {}))
        success_schema = success_response.get('schema', {})
        success_fields = flatten_schema_to_fields(success_schema)
        
        # Add removed success response fields
        if previous_schema:
            prev_responses = previous_schema.get('responses', {})
            prev_success = prev_responses.get('200', prev_responses.get('201', {}))
            prev_success_schema = prev_success.get('schema', {})
            prev_success_fields = flatten_schema_to_fields(prev_success_schema)
            success_fields = self._merge_fields_with_removed(
                success_fields, prev_success_fields, field_changes, "responses.200"
            )

        # apply preserved descriptions for success response fields
        if preserved_task_descriptions and 'response' in preserved_task_descriptions:
            self._apply_preserved_descriptions(success_fields, preserved_task_descriptions['response'])
        
        # Generate success example
        success_example = json.dumps(
            generate_example_from_schema(success_schema),
            indent=2,
            ensure_ascii=False,
            default=str,  # tolerate non-JSON values (e.g. datetime examples)
        )
        
        # Extract error responses
        error_examples = []
        for code, response in responses.items():
            # Response codes may be ints (unquoted YAML: `200:`); coerce to str
            code = str(code)
            if code.startswith('4') or code.startswith('5'):
                error_schema = response.get('schema', {})
                error_fields = flatten_schema_to_fields(error_schema)
                
                # Add removed error fields
                if previous_schema:
                    prev_error = previous_schema.get('responses', {}).get(code, {})
                    prev_error_schema = prev_error.get('schema', {})
                    prev_error_fields = flatten_schema_to_fields(prev_error_schema)
                    error_fields = self._merge_fields_with_removed(
                        error_fields, prev_error_fields, field_changes, f"responses.{code}"
                    )
                
                # apply preserved descriptions for error response fields
                error_key = f"responses.{code}"
                if preserved_task_descriptions and error_key in preserved_task_descriptions:
                    self._apply_preserved_descriptions(error_fields, preserved_task_descriptions[error_key])
                
                error_example = json.dumps(
                    generate_example_from_schema(error_schema),
                    indent=2,
                    ensure_ascii=False,
                    default=str,  # tolerate non-JSON values (e.g. datetime examples)
                )
                error_desc = response.get('description', f'HTTP {code} Error')
                error_examples.append((error_example, code, error_desc, error_fields))
        
        # Generate tab content
        # Generate tab content
        task_id = event.get('task_id', 'NO-TASK')
        tab_content = gen.generate_task_tab_content(
            task_id=task_id,
            request_example=request_example,
            header_params=header_params,
            formdata_params=formdata_params,
            request_fields=request_fields,
            success_example=success_example,
            success_fields=success_fields,
            error_examples=error_examples,
            field_changes=field_changes,
            version_suffix=version_suffix,
            page_title=page_title
        )
        
        # Add warning if skipped tasks exist
        skipped_tasks = event.get('skipped_tasks', [])
        if skipped_tasks:
            skipped_str = ", ".join(skipped_tasks)
            warning = gen.generate_warning_macro(
                title="⚠️ Missing Data",
                content=f"This diff includes cumulative changes from the following skipped tasks (due to missing specifications): {skipped_str}"
            )
            tab_content = warning + tab_content
            
        return tab_content
    
    def _merge_fields_with_removed(
        self,
        current_fields: List[Dict[str, Any]],
        previous_fields: List[Dict[str, Any]],
        field_changes: Dict[str, str],
        prefix: str
    ) -> List[Dict[str, Any]]:
        """
        Merge current fields with removed fields from previous schema.
        Marks removed fields with 'removed' change type.
        Preserves the original order by injecting removed fields near their neighbors.
        """
        # Get current field paths for quick lookup
        current_paths = {f.get('full_path', '') for f in current_fields}
        
        # Result starts as a copy of current fields
        result = list(current_fields)
        
        # Helper to find index of a path in result
        def find_index_in_result(path: str) -> int:
            for i, f in enumerate(result):
                if f.get('full_path') == path:
                    return i
            return -1

        # Iterate previous fields to identify and place removed ones
        for i, prev_field in enumerate(previous_fields):
            prev_path = prev_field.get('full_path', '')
            
            # If field exists in current, it's already in result (with new data)
            if prev_path in current_paths:
                continue
                
            # This field was removed. logic to find insertion point:
            # Look for the nearest PRECEDING field that exists in 'result' (anchor)
            insertion_index = -1
            
            # 1. Search backwards for an anchor
            for j in range(i - 1, -1, -1):
                anchor_path = previous_fields[j].get('full_path', '')
                anchor_idx = find_index_in_result(anchor_path)
                if anchor_idx != -1:
                    # Found an anchor! Insert after it.
                    insertion_index = anchor_idx + 1
                    break
            
            if insertion_index == -1:
                # 2. If no preceding anchor, search forwards for a SUCCEEDING anchor
                for j in range(i + 1, len(previous_fields)):
                    anchor_path = previous_fields[j].get('full_path', '')
                    anchor_idx = find_index_in_result(anchor_path)
                    if anchor_idx != -1:
                        # Found a succeeding anchor! Insert before it.
                        insertion_index = anchor_idx
                        break
            
            # 3. Fallback: Append to end if no anchors found
            if insertion_index == -1:
                insertion_index = len(result)
            
            # Prepare removed field
            removed_field = prev_field.copy()
            
            # Insert at calculated position
            result.insert(insertion_index, removed_field)
            
            # Update lookup set (so subsequent removed fields can anchor to this one)
            current_paths.add(prev_path)
            
            # Mark as removed in field_changes
            field_changes[f"{prefix}.{prev_path}"] = 'removed'
            field_changes[prev_path] = 'removed'  # Also add without prefix
        
        return result
    
    def _apply_preserved_descriptions(
        self,
        fields: List[Dict[str, Any]],
        preserved_descriptions: Dict[str, str]
    ) -> None:
        """
        Update field descriptions with user-edited preserved values.
        Prioritizes user content over schema-generated content if user content exists (is not empty).
        """
        for field in fields:
            full_path = field.get('full_path')
            if full_path and full_path in preserved_descriptions:
                user_desc = preserved_descriptions[full_path]
                if user_desc and user_desc.strip(): # Check if user description is not empty
                    # Only update if user description is meaningful
                    # Note: We could check if schema description is "better", 
                    # but user edit is Manual so it takes precedence.
                    # Unless user explicitly cleared it? If preserved is empty string, we ignore it (previous logic).
                    # If preserved is non-empty, we overwrite schema description.
                    
                    schema_desc = field.get('description', '')
                    if user_desc != schema_desc:
                         # logger.debug(f"Using preserved description for {full_path}")
                         field['description'] = user_desc
    
    def _apply_preserved_descriptions(
        self,
        fields: List[Dict[str, Any]],
        preserved_descriptions: Dict[str, str]
    ) -> None:
        """
        Update field descriptions with user-edited preserved values.
        Prioritizes user content over schema-generated content if user content exists (is not empty).
        """
        for field in fields:
            full_path = field.get('full_path')
            if full_path and full_path in preserved_descriptions:
                user_desc = preserved_descriptions[full_path]
                if user_desc and user_desc.strip(): # Check if user description is not empty
                    # Only update if user description is meaningful
                    # Note: We could check if schema description is "better", 
                    # but user edit is Manual so it takes precedence.
                    # Unless user explicitly cleared it? If preserved is empty string, we ignore it (previous logic).
                    # If preserved is non-empty, we overwrite schema description.
                    
                    schema_desc = field.get('description', '')
                    if user_desc != schema_desc:
                         # logger.debug(f"Using preserved description for {full_path}")
                         field['description'] = user_desc
    
    def _find_nested_fields_in_schema(
        self,
        schema: Dict[str, Any],
        parent_field_path: str
    ) -> List[str]:
        """
        Find all nested fields under a given parent field path in the schema.
        
        Universal approach using flatten_schema_to_fields to handle any level of nesting,
        including complex allOf/anyOf/oneOf structures.
        
        Args:
            schema: Full endpoint schema
            parent_field_path: Normalized path like "responses.200.result.data.office"
            
        Returns:
            List of nested field paths like ["responses.200.result.data.office.guid", ...]
        """
        from .template_generator import flatten_schema_to_fields
        
        # Determine which part of schema to extract
        if parent_field_path.startswith('responses.'):
            # Extract response code (e.g., "200" from "responses.200.result")
            parts = parent_field_path.split('.')
            if len(parts) >= 2:
                response_code = parts[1]  # "200"
                response_schema = schema.get('responses', {}).get(response_code, {}).get('schema', {})
                
                if response_schema:
                    # Flatten all fields in response
                    all_fields = flatten_schema_to_fields(response_schema)
                    
                    # Find fields that are children of parent_field_path
                    # Remove "responses.200." prefix to match flatten_schema_to_fields output
                    # E.g., "responses.200.result.data.office" -> "result.data.office"
                    prefix_without_response = '.'.join(parts[2:])  # "result.data.office"
                    
                    nested_fields = []
                    for field in all_fields:
                        field_path = field.get('full_path', '')
                        if field_path and field_path.startswith(f"{prefix_without_response}."):
                            # This is a nested field - add full path with responses prefix
                            full_path = f"responses.{response_code}.{field_path}"
                            nested_fields.append(full_path)
                    
                    return nested_fields
        
        elif parent_field_path.startswith('requestBody.'):
            # Extract requestBody schema
            request_body = schema.get('requestBody', {})
            
            if request_body:
                # Flatten all fields in requestBody
                all_fields = flatten_schema_to_fields(request_body)
                
                # Remove "requestBody." prefix
                # E.g., "requestBody.products.office" -> "products.office"
                prefix_without_rb = parent_field_path[len('requestBody.'):]
                
                nested_fields = []
                for field in all_fields:
                    field_path = field.get('full_path', '')
                    if field_path and field_path.startswith(f"{prefix_without_rb}."):
                        # This is a nested field
                        full_path = f"requestBody.{field_path}"
                        nested_fields.append(full_path)
                
                return nested_fields
        
        return []
    
    def _extract_field_changes(
        self, 
        diff: Dict[str, Any],
        current_schema: Optional[Dict[str, Any]] = None,
        previous_schema: Optional[Dict[str, Any]] = None
    ) -> Dict[str, str]:
        """
        Extract field changes from DeepDiff result.
        
        Returns:
            Dict mapping field path to change type ('added', 'modified', 'removed')
            Paths are normalized to match field table full_path format.
            
        Handles special cases:
        - Changes in 'required' arrays → marks affected fields as 'modified'
        - Header parameter changes → normalizes path for header table matching
        """
        changes = {}
        
        if not diff:
            return changes
        
        # DEBUG: Log all diff keys
        logger.debug(f"[DIFF DEBUG] Keys: {list(diff.keys())}")
        
        # Added fields/items
        if 'dictionary_item_added' in diff:
            logger.debug(f"[DIFF DEBUG] dictionary_item_added: {list(diff['dictionary_item_added'])}")
            for path in diff['dictionary_item_added']:
                # Skip structural schema elements (allOf, anyOf, oneOf, properties, items)
                # These are internal restructuring, not actual field additions
                if (path.endswith("['allOf']") or path.endswith("['anyOf']") or 
                    path.endswith("['oneOf']") or path.endswith("['properties']") or
                    path.endswith("['items']")):
                    logger.debug(f"[SKIP] Structural addition: {path}")
                    continue
                
                # Skip structural parameter type keys (formData, header, query, path)
                # These appear/disappear during OpenAPI format migrations but aren't field changes
                if (path.endswith("['formData']") or path.endswith("['header']") or 
                    path.endswith("['query']") or path.endswith("['path']")):
                    logger.debug(f"[SKIP] Parameter type key addition: {path}")
                    continue

                # Skip cosmetic attributes
                if (path.endswith("['x-order']") or path.endswith("['description']") or 
                    path.endswith("['example']") or path.endswith("['title']") or 
                    path.endswith("['deprecated']") or path.endswith("['readOnly']") or 
                    path.endswith("['writeOnly']")):
                    logger.debug(f"[SKIP] Cosmetic attribute addition: {path}")
                    continue
                
                # Special case: format addition → mark as modified with format note
                if path.endswith("['format']"):
                    # _normalize_path automatically strips 'format' from skip_attributes
                    normalized = self._normalize_path(path)
                    if normalized:
                        # Try to get the added value, but handle cases where it's just a set of keys
                        try:
                            added_format = diff['dictionary_item_added'][path]
                            logger.debug(f"[FORMAT] Format added for {normalized}: {added_format}")
                        except (TypeError, KeyError):
                            # If we can't access by key, it means format was added but value unknown
                            logger.debug(f"[FORMAT] Format added for {normalized}")
                        changes[normalized] = 'modified'
                    continue
                
                normalized = self._normalize_path(path)
                logger.debug(f"[DIFF DEBUG] Added path: {path} -> normalized: {normalized}")
                if normalized:
                    # Special case: if path ends with ['required'], it's a modification not addition
                    # Example: root['parameters']['header']['Authorization']['required'] 
                    # means the header became required, not that it was added
                    if path.endswith("['required']"):
                        changes[normalized] = 'modified'
                        logger.info(f"[REQUIRED] Marked as modified (required added): {normalized}")
                    else:
                        changes[normalized] = 'added'
                        
                        # If the added field is in a complex structure (responses/requestBody),
                        # check if it's an object with nested fields and mark them all as 'added'
                        # This ensures complete highlighting of new objects at any nesting level
                        if current_schema and (normalized.startswith('responses.') or normalized.startswith('requestBody.')):
                            nested_fields = self._find_nested_fields_in_schema(current_schema, normalized)
                            if nested_fields:
                                logger.info(f"[NESTED] Found {len(nested_fields)} nested fields under {normalized}")
                                for nested_path in nested_fields:
                                    if nested_path not in changes:
                                        changes[nested_path] = 'added'
                                        logger.info(f"[NESTED] Marked as added: {nested_path}")
        
        # Also handle iterable_item_added (for arrays like 'required')
        if 'iterable_item_added' in diff:
            for path, value in diff['iterable_item_added'].items():
                # Check if this is a 'required' array change
                if "'required']" in path:
                    # The value is the field name that became required
                    parent_path = self._get_parent_field_path(path)
                    if isinstance(value, str):
                        # Ensure path has section prefix from original deepdiff path
                        if parent_path:
                            field_path = f"{parent_path}.{value}"
                        else:
                            # Extract section from original path if parent is empty
                            # E.g., root['responses']['200']...['required'] → responses.200.{value}
                            base_normalized = self._normalize_path(path)
                            if base_normalized:
                                # Use base path dot value if base has content
                                field_path = f"{base_normalized}.{value}" if '.' in base_normalized else base_normalized
                            else:
                                # Skip - can't determine proper section
                                continue
                        # Don't overwrite 'added' with 'modified' - new field is still 'added'
                        if field_path not in changes:
                            changes[field_path] = 'modified'
                else:
                    normalized = self._normalize_path(path)
                    if normalized:
                        changes[normalized] = 'added'
        
        # Removed fields/items
        if 'dictionary_item_removed' in diff:
            for path in diff['dictionary_item_removed']:
                # Skip structural schema elements - these are internal restructuring, not field changes
                if (path.endswith("['allOf']") or path.endswith("['anyOf']") or 
                    path.endswith("['oneOf']") or path.endswith("['properties']") or
                    path.endswith("['items']") or path.endswith("['required']") or
                    path.endswith("['formData']") or path.endswith("['header']") or
                    path.endswith("['query']") or path.endswith("['path']")):
                    logger.debug(f"[SKIP] Structural removal: {path}")
                    continue
                
                # Skip cosmetic attributes
                if (path.endswith("['x-order']") or path.endswith("['description']") or 
                    path.endswith("['example']") or path.endswith("['title']") or 
                    path.endswith("['deprecated']") or path.endswith("['readOnly']") or 
                    path.endswith("['writeOnly']")):
                    logger.debug(f"[SKIP] Cosmetic attribute removal: {path}")
                    continue
                
                # Special case: format removal → mark as modified with format note
                if path.endswith("['format']"):
                    # _normalize_path automatically strips 'format' from skip_attributes
                    normalized = self._normalize_path(path)
                    if normalized:
                        # Try to get the removed value, but handle cases where it's just a set of keys
                        try:
                            removed_format = diff['dictionary_item_removed'][path]
                            logger.debug(f"[FORMAT] Format removed from {normalized}: {removed_format}")
                        except (TypeError, KeyError):
                            # If we can't access by key, it means format was removed but value unknown
                            logger.debug(f"[FORMAT] Format removed from {normalized}")
                        changes[normalized] = 'modified'
                    continue
                    
                normalized = self._normalize_path(path)
                if normalized:
                    # Special case: if path ends with ['required'], it's a modification not removal
                    # Example: root['parameters']['header']['Authorization']['required']
                    # means the header became optional, not that it was removed
                    if path.endswith("['required']"):
                        changes[normalized] = 'modified'
                        logger.info(f"[REQUIRED] Marked as modified (required removed): {normalized}")
                    else:
                        changes[normalized] = 'removed'
        
        # Handle iterable_item_removed (for arrays like 'required')
        if 'iterable_item_removed' in diff:
            logger.debug(f"[DIFF DEBUG] iterable_item_removed: {list(diff['iterable_item_removed'].keys())}")
            for path, value in diff['iterable_item_removed'].items():
                logger.debug(f"[DIFF DEBUG] iterable_item_removed path: {path}, value type: {type(value).__name__}")
                # Check if this is a 'required' array change
                if "'required']" in path:
                    # The value is the field name that became optional
                    parent_path = self._get_parent_field_path(path)
                    if isinstance(value, str):
                        # Ensure path has section prefix from original deepdiff path
                        if parent_path:
                            field_path = f"{parent_path}.{value}"
                        else:
                            # Extract section from original path if parent is empty
                            base_normalized = self._normalize_path(path)
                            if base_normalized:
                                field_path = f"{base_normalized}.{value}" if '.' in base_normalized else base_normalized
                            else:
                                # Skip - can't determine proper section
                                continue
                        # Don't overwrite 'added'/'removed' with 'modified'
                        if field_path not in changes:
                            changes[field_path] = 'modified'
                elif "'allOf']" in path or "'anyOf']" in path or "'oneOf']" in path:
                    # Skip schema composition changes - they're handled via values_changed
                    logger.debug(f"[DIFF DEBUG] Skipping allOf/anyOf/oneOf removal: {path}")
                    continue
                else:
                    normalized = self._normalize_path(path)
                    if normalized:
                        changes[normalized] = 'removed'
        
        # Changed values
        if 'values_changed' in diff:
            logger.debug(f"[DIFF DEBUG] values_changed: {list(diff['values_changed'].keys())}")
            for path, change_info in diff['values_changed'].items():
                logger.debug(f"[DIFF DEBUG] values_changed path: {path}, change: {change_info}")
                
                # Special case: format value changed (uuid → string, etc.)
                if path.endswith("['format']"):
                    normalized = self._normalize_path(path)
                    if normalized:
                        old_fmt = change_info.get('old_value')
                        new_fmt = change_info.get('new_value')
                        changes[normalized] = 'modified'
                        logger.debug(f"[FORMAT] Format changed for {normalized}: {old_fmt} → {new_fmt}")
                    continue
                
                # Skip insignificant format changes (e.g., int8 → uint8)
                if self._is_insignificant_change(path, change_info):
                    logger.debug(f"[SKIP] Insignificant format change: {path}")
                    continue
                
                # Check if this is a 'required' change
                if "'required']" in path:
                    # Handle required array changes - find affected fields
                    self._extract_required_changes(path, change_info, changes)
                else:
                    # Check if the changed object contains 'required' arrays
                    old_value = change_info.get('old_value', {})
                    new_value = change_info.get('new_value', {})
                    
                    # If both have 'required' arrays, extract the changes
                    if isinstance(old_value, dict) and isinstance(new_value, dict):
                        old_required = set(old_value.get('required', []))
                        new_required = set(new_value.get('required', []))
                        old_properties = set(old_value.get('properties', {}).keys())
                        
                        if old_required != new_required:
                            parent_path = self._normalize_path(path)
                            
                            # Fields that became optional (were required, now not)
                            # Only mark if field EXISTED before (was in old properties)
                            for field in old_required - new_required:
                                if field in old_properties:
                                    field_path = f"{parent_path}.{field}" if parent_path else field
                                    if field_path not in changes:
                                        changes[field_path] = 'modified'
                            
                            # Fields that became required (were optional, now required)
                            # Only mark if field EXISTED before (was in old properties)
                            for field in new_required - old_required:
                                if field in old_properties:
                                    field_path = f"{parent_path}.{field}" if parent_path else field
                                    if field_path not in changes:
                                        changes[field_path] = 'modified'
                        # Don't mark the whole object if only required changed
                    else:
                        normalized = self._normalize_path(path)
                        if normalized:
                            changes[normalized] = 'modified'
        
        # Type changes
        if 'type_changes' in diff:
            for path in diff['type_changes']:
                normalized = self._normalize_path(path)
                if normalized:
                    changes[normalized] = 'modified'
        
        # Detect structural changes (allOf added/removed) and extract real field changes
        # When schema restructures from properties to allOf or vice versa, DeepDiff only
        # reports the structural change, not the individual field additions/removals
        if current_schema and previous_schema:
            structural_field_changes = self._extract_structural_field_changes(
                diff, current_schema, previous_schema
            )
            # Merge structural field changes (don't overwrite existing)
            for path, change_type in structural_field_changes.items():
                if path not in changes:
                    changes[path] = change_type
                    logger.debug(f"[STRUCTURAL] Added field change: {path} -> {change_type}")
        
        # Filter false positives: fields marked as 'removed' or 'added' that still exist
        # This happens when schema structure changes (e.g., allOf reorganization)
        # but actual fields remain the same
        if current_schema and changes:
            changes = self._filter_false_positive_changes(changes, current_schema, previous_schema)

        # Per-field required changes (обязательный ⇄ необязательный).
        # DeepDiff encodes these inconsistently — when a field is the only
        # required one, dropping it removes the whole ['required'] key, which the
        # loops above can only attribute to the PARENT object, so the changed
        # field itself never gets highlighted. Compare flattened field
        # definitions directly and mark the specific field 'modified' with its
        # section-relative full_path — exactly the key the table matcher looks up.
        if current_schema and previous_schema:
            def _mark_required(cur_sec, prev_sec):
                if not cur_sec or not prev_sec:
                    return
                cur = {f['full_path']: f.get('required', False)
                       for f in flatten_schema_to_fields(cur_sec) if f.get('full_path')}
                prev = {f['full_path']: f.get('required', False)
                        for f in flatten_schema_to_fields(prev_sec) if f.get('full_path')}
                for fp in cur.keys() & prev.keys():
                    if cur[fp] != prev[fp] and changes.get(fp) not in ('added', 'removed'):
                        changes[fp] = 'modified'

            _mark_required(current_schema.get('requestBody', {}),
                           previous_schema.get('requestBody', {}))
            cur_resps = current_schema.get('responses', {}) or {}
            prev_resps = previous_schema.get('responses', {}) or {}
            for code in set(cur_resps) & set(prev_resps):
                _mark_required(cur_resps.get(code, {}).get('schema', {}),
                               prev_resps.get(code, {}).get('schema', {}))

        return changes
    
    def _extract_structural_field_changes(
        self,
        diff: Dict[str, Any],
        current_schema: Dict[str, Any],
        previous_schema: Dict[str, Any]
    ) -> Dict[str, str]:
        """
        Extract real field changes when schema structure changes (e.g., properties → allOf).
        
        When DeepDiff reports structural changes like:
        - dictionary_item_added: ['allOf']
        - dictionary_item_removed: ['properties'], ['required']
        
        This means the schema was restructured, but the actual fields inside may have
        changed. This method compares the flattened field lists to find real additions/removals.
        
        Returns:
            Dict of field_path -> 'added'|'removed' for actual field changes
        """
        from .template_generator import flatten_schema_to_fields
        
        changes = {}
        
        # Check if there's a structural change in requestBody
        has_structural_change = False
        affected_prefix = None
        
        for path in diff.get('dictionary_item_added', []):
            if path.endswith("['allOf']") or path.endswith("['properties']"):
                has_structural_change = True
                # Determine prefix (requestBody or responses.200)
                if 'requestBody' in path:
                    affected_prefix = 'requestBody'
                elif 'responses' in path:
                    if "'200']" in path:
                        affected_prefix = 'responses.200'
                break
        
        if not has_structural_change:
            for path in diff.get('dictionary_item_removed', []):
                if path.endswith("['allOf']") or path.endswith("['properties']"):
                    has_structural_change = True
                    if 'requestBody' in path:
                        affected_prefix = 'requestBody'
                    elif 'responses' in path:
                        if "'200']" in path:
                            affected_prefix = 'responses.200'
                    break
        
        # Also check values_changed for nested objects with properties
        # Example: allOf[0] entire object replaced with new properties
        if not has_structural_change:
            for path in diff.get('values_changed', {}):
                if "'properties']" in path and "'allOf']" in path:
                    has_structural_change = True
                    if 'requestBody' in path:
                        affected_prefix = 'requestBody'
                    elif 'responses' in path:
                        if "'200']" in path:
                            affected_prefix = 'responses.200'
                    logger.debug(f"[STRUCTURAL] Detected values_changed in nested allOf with properties: {path}")
                    break
        
        if not has_structural_change or not affected_prefix:
            return changes
        
        logger.debug(f"[STRUCTURAL] Detected structural change in {affected_prefix}")
        
        # Get fields from both schemas
        if affected_prefix == 'requestBody':
            current_fields = flatten_schema_to_fields(current_schema.get('requestBody', {}))
            previous_fields = flatten_schema_to_fields(previous_schema.get('requestBody', {}))
        else:
            # responses.200
            current_response = current_schema.get('responses', {}).get('200', {})
            previous_response = previous_schema.get('responses', {}).get('200', {})
            current_fields = flatten_schema_to_fields(current_response.get('schema', {}))
            previous_fields = flatten_schema_to_fields(previous_response.get('schema', {}))
        
        # Convert to sets of full_path
        current_paths = {f.get('full_path', ''): f for f in current_fields if f.get('full_path')}
        previous_paths = {f.get('full_path', ''): f for f in previous_fields if f.get('full_path')}
        
        # Find added fields
        for path in current_paths.keys() - previous_paths.keys():
            full_path = f"{affected_prefix}.{path}"
            changes[full_path] = 'added'
            logger.debug(f"[STRUCTURAL] Field added: {full_path}")
        
        # Find removed fields
        for path in previous_paths.keys() - current_paths.keys():
            full_path = f"{affected_prefix}.{path}"
            changes[full_path] = 'removed'
            logger.debug(f"[STRUCTURAL] Field removed: {full_path}")
        
        return changes
    
    def _filter_false_positive_changes(
        self,
        changes: Dict[str, str],
        current_schema: Dict[str, Any],
        previous_schema: Optional[Dict[str, Any]]
    ) -> Dict[str, str]:
        """
        Filter out false positive field changes caused by schema restructuring.
        
        When schema structure changes (e.g., fields move into a wrapper object like 'result'),
        DeepDiff reports fields as added/removed even though they conceptually still exist.
        
        This method checks if a "removed" field name exists anywhere in current schema
        and if an "added" field name existed anywhere in previous schema.
        
        Args:
            changes: Dict of field_path -> change_type
            current_schema: Current endpoint schema
            previous_schema: Previous endpoint schema
            
        Returns:
            Filtered changes dict with false positives removed
        """
        from .template_generator import flatten_schema_to_fields
        
        # Get all field names from both schemas (just names, not full paths)
        current_field_names = set()
        previous_field_names = set()
        
        # Extract response fields (most common source of false positives)
        responses = current_schema.get('responses', {})
        for code in ['200', '201']:
            if code in responses:
                response_schema = responses[code].get('schema', {})
                fields = flatten_schema_to_fields(response_schema)
                for f in fields:
                    full_path = f.get('full_path', '')
                    if full_path:
                        # Add the field name (last component)
                        field_name = full_path.split('.')[-1]
                        current_field_names.add(field_name)
        
        if previous_schema:
            prev_responses = previous_schema.get('responses', {})
            for code in ['200', '201']:
                if code in prev_responses:
                    response_schema = prev_responses[code].get('schema', {})
                    fields = flatten_schema_to_fields(response_schema)
                    for f in fields:
                        full_path = f.get('full_path', '')
                        if full_path:
                            field_name = full_path.split('.')[-1]
                            previous_field_names.add(field_name)
        
        logger.debug(f"[FALSE-POSITIVE] Current fields: {current_field_names}")
        logger.debug(f"[FALSE-POSITIVE] Previous fields: {previous_field_names}")
        
        # Get full paths AND definitions for comparison
        current_full_defs = {}
        previous_full_defs = {}
        
        responses = current_schema.get('responses', {})
        for code in ['200', '201']:
            if code in responses:
                response_schema = responses[code].get('schema', {})
                fields = flatten_schema_to_fields(response_schema)
                for f in fields:
                    full_path = f.get('full_path', '')
                    if full_path:
                        # Store full path with responses prefix
                        current_full_defs[f"responses.{code}.{full_path}"] = f
        
        # Also check requestBody
        if 'requestBody' in current_schema:
            fields = flatten_schema_to_fields(current_schema['requestBody'])
            for f in fields:
                full_path = f.get('full_path', '')
                if full_path:
                    current_full_defs[f"requestBody.{full_path}"] = f
        
        if previous_schema:
            prev_responses = previous_schema.get('responses', {})
            for code in ['200', '201']:
                if code in prev_responses:
                    response_schema = prev_responses[code].get('schema', {})
                    fields = flatten_schema_to_fields(response_schema)
                    for f in fields:
                        full_path = f.get('full_path', '')
                        if full_path:
                            previous_full_defs[f"responses.{code}.{full_path}"] = f
            
            if 'requestBody' in previous_schema:
                fields = flatten_schema_to_fields(previous_schema['requestBody'])
                for f in fields:
                    full_path = f.get('full_path', '')
                    if full_path:
                        previous_full_defs[f"requestBody.{full_path}"] = f
        
        # Filter false positives based on FULL paths properties
        filtered = {}
        
        # Helper to clean definition for comparison
        def clean_def(d):
            return {k: v for k, v in d.items() if k not in 
                ['x-order', 'description', 'example', 'title', 'full_path']}

        for field_path, change_type in changes.items():
            # Skip response code changes (like responses.403)
            if field_path.startswith('responses.') and field_path.count('.') == 1:
                filtered[field_path] = change_type
                continue
            
            if change_type == 'removed':
                # Field marked as removed - check if EXACT path exists in current schema
                if field_path in current_full_defs:
                    logger.debug(f"[FALSE-POSITIVE] Skipping 'removed' {field_path} - path exists in current")
                    continue
            elif change_type == 'added':
                # Field marked as added - check if EXACT path existed in previous schema
                if field_path in previous_full_defs:
                    # Also check if it's identical (ignoring cosmetics)
                    curr_def = current_full_defs.get(field_path, {})
                    prev_def = previous_full_defs[field_path]
                    if clean_def(curr_def) == clean_def(prev_def):
                        logger.debug(f"[FALSE-POSITIVE] Skipping 'added' {field_path} - identical path existed")
                        continue
            elif change_type == 'modified':
                # NEW: Check if 'modified' field is actually identical (e.g. moved to allOf)
                if field_path in current_full_defs and field_path in previous_full_defs:
                    curr_def = current_full_defs[field_path]
                    prev_def = previous_full_defs[field_path]
                    if clean_def(curr_def) == clean_def(prev_def):
                        logger.debug(f"[FALSE-POSITIVE] Skipping 'modified' {field_path} - identical definition")
                        continue
            
            filtered[field_path] = change_type
        
        if len(filtered) != len(changes):
            logger.info(f"[FALSE-POSITIVE] Filtered {len(changes) - len(filtered)} false positive changes")
        
        return filtered
    
    def _is_insignificant_change(self, path: str, change_info: Dict[str, Any]) -> bool:
        """
        Check if a value change is insignificant and should be skipped.
        
        Insignificant changes include:
        - Cosmetic attributes (description, example, title, x-order)
        - Format changes within the same base type (e.g., int8 → uint8, int32 → int64)
        - These are implementation details, not contract changes
        
        Significant changes that should NOT be skipped:
        - uuid → string (or vice versa) - this is a real contract change
        - date-time → string - format removal is significant
        
        Args:
            path: DeepDiff path to the changed value
            change_info: Dict with 'old_value' and 'new_value'
            
        Returns:
            True if change should be skipped
        """
        old_value = change_info.get('old_value')
        new_value = change_info.get('new_value')

        # Check for cosmetic attributes
        if path.endswith(self.COSMETIC_SUFFIXES):
            return True
        
        # Check if this is a format change
        if "'format']" in path:
            # Format changes within same base type family are insignificant
            # Integer formats: int8, uint8, int16, uint16, int32, uint32, int64, uint64
            # Float formats: float, double
            integer_formats = {'int8', 'uint8', 'int16', 'uint16', 'int32', 'uint32', 'int64', 'uint64', 'integer'}
            float_formats = {'float', 'double', 'number'}
            # Semantic formats that are SIGNIFICANT when added/removed
            semantic_formats = {'uuid', 'date', 'date-time', 'time', 'email', 'uri', 'hostname', 'ipv4', 'ipv6'}
            
            old_str = str(old_value).lower() if old_value else ''
            new_str = str(new_value).lower() if new_value else ''
            
            # Semantic format changes (uuid → string, date-time → string) are SIGNIFICANT
            # If one side is a semantic format and the other is not - it's significant
            old_is_semantic = old_str in semantic_formats
            new_is_semantic = new_str in semantic_formats
            if old_is_semantic != new_is_semantic:
                return False  # This is a significant change
            
            # Both are integer formats - insignificant
            if old_str in integer_formats and new_str in integer_formats:
                return True
            
            # Both are float formats - insignificant
            if old_str in float_formats and new_str in float_formats:
                return True
        
        # Check if this is a type change between similar types
        if "'type']" in path:
            # integer vs number are similar enough
            similar_types = [
                {'integer', 'number'},
            ]
            old_str = str(old_value).lower() if old_value else ''
            new_str = str(new_value).lower() if new_value else ''
            
            for group in similar_types:
                if old_str in group and new_str in group:
                    return True
        
        return False
    
    def _is_diff_purely_cosmetic(self, diff: Dict[str, Any]) -> bool:
        """
        Check if the diff contains ONLY cosmetic changes (description, example, title).
        Used to forcefully skip tasks that have a diff but seemingly no field changes.
        """
        if not diff:
            return False 
            
        # Check added items
        for path in diff.get('dictionary_item_added', []):
            if not path.endswith(self.COSMETIC_SUFFIXES):
                 return False
                 
        # Check removed items
        for path in diff.get('dictionary_item_removed', []):
            if not path.endswith(self.COSMETIC_SUFFIXES):
                 return False
    
        # Check changed values
        for path in diff.get('values_changed', {}):
            if not path.endswith(self.COSMETIC_SUFFIXES):
                 return False
                 
        # Check other change types
        if diff.get('iterable_item_added'): return False
        # iterable_item_removed might be cosmetic (e.g. required removed?), but safest to assume not.
        if diff.get('iterable_item_removed'): return False
        if diff.get('type_changes'): return False
        
        return True
    
    def _get_parent_field_path(self, deepdiff_path: str) -> str:
        """
        Get parent field path for a 'required' array change.
        
        Example:
            "root['requestBody']['properties']['products']['items']['required'][0]"
            → "requestBody.products"
        """
        import re
        # Remove the 'required' part and everything after
        path = re.sub(r"\['required'\].*$", "", deepdiff_path)
        return self._normalize_path(path)
    
    def _extract_required_changes(
        self, 
        path: str, 
        change_info: Dict[str, Any],
        changes: Dict[str, str]
    ) -> None:
        """
        Extract field names affected by 'required' array changes or boolean changes.
        
        Handles two cases:
        1. Arrays: When required changes from ['guid', 'quantity'] to ['guid'],
           we mark 'quantity' as modified.
        2. Booleans: When required changes from True to False (or vice versa),
           typically for header/query parameters, we mark the parent field as modified.
        """
        old_value = change_info.get('old_value')
        new_value = change_info.get('new_value')
        parent_path = self._get_parent_field_path(path)
        
        # Case 1: Boolean required change (typically for header/query params)
        # Path: root['parameters']['header']['X-Trace-Id']['required']
        # parent_path will be: header.X-Trace-Id
        if isinstance(old_value, bool) and isinstance(new_value, bool):
            if old_value != new_value and parent_path:
                # Mark the header/parameter as modified (not added!)
                if parent_path not in changes:
                    changes[parent_path] = 'modified'
                    logger.info(f"[REQUIRED] Boolean change: {parent_path} required: {old_value} -> {new_value}")
            return
        
        # Case 2: Array required change (for object properties)
        if isinstance(old_value, list) and isinstance(new_value, list):
            old_set = set(old_value)
            new_set = set(new_value)
            
            # Fields that became optional (removed from required)
            for field in old_set - new_set:
                field_path = f"{parent_path}.{field}" if parent_path else field
                # Don't overwrite 'added'/'removed' with 'modified'
                if field_path not in changes:
                    changes[field_path] = 'modified'
            
            # Fields that became required (added to required)
            for field in new_set - old_set:
                field_path = f"{parent_path}.{field}" if parent_path else field
                # Don't overwrite 'added'/'removed' with 'modified'
                if field_path not in changes:
                    changes[field_path] = 'modified'
    
    @staticmethod
    def _normalize_path(deepdiff_path: str) -> str:
        """
        Normalize DeepDiff path to field path for table matching.

        Pure string helper (no instance state) — safe to call without
        constructing a publisher / Confluence credentials.
        
        Uses DeepDiff's parse_path for reliable path parsing.
        
        Example: 
            "root['requestBody']['properties']['order']['properties']['guid']"
            → "requestBody.order.guid"
            
            "root['responses']['200']['schema']['properties']['success']"
            → "responses.200.success"
            
            "root['parameters']['header']['X-Request-ID']"
            → "header.X-Request-ID"
            
            "root['requestBody']['allOf'][1]['properties']['officeGuid']"
            → "requestBody.officeGuid"
            
            "root['responses']['403']"
            → "responses.403"
        """
        from deepdiff import parse_path
        
        try:
            # Parse path into list of keys using DeepDiff's built-in parser
            keys = parse_path(deepdiff_path)
        except Exception:
            return ""
        
        if not keys:
            return ""
        
        # Schema internal elements to skip
        skip_schema_internals = {
            'properties', 'items', 'schema', 'content', 'application/json',
            'allOf', 'anyOf', 'oneOf', 'parameters'
        }
        
        # Schema attributes that should not be part of field path
        # When format/type/example change, we want to highlight the FIELD, not the attribute
        skip_attributes = {
            'format', 'type', 'example', 'enum', 'minimum', 'maximum',
            'minLength', 'maxLength', 'pattern', 'default', 'nullable',
            'description', 'title', 'deprecated', 'readOnly', 'writeOnly',
            'minItems', 'maxItems', 'uniqueItems', 'multipleOf'
        }
        
        result = []
        prev_key = None
        
        for key in keys:
            # Skip numeric indices (from allOf[0], items[1], etc.)
            # But keep HTTP status codes (3-digit strings after 'responses')
            if isinstance(key, int):
                prev_key = key
                continue
            
            # Check if it's a string HTTP status code (like '403')
            if isinstance(key, str) and key.isdigit() and len(key) == 3:
                if prev_key == 'responses':
                    result.append(key)
                prev_key = key
                continue
            
            # Skip schema internals
            if key in skip_schema_internals:
                prev_key = key
                continue
            
            # Skip schema attributes ONLY if they are at the end of the path
            # (i.e. we are changing the attribute of a field, not a field named 'type')
            if key in skip_attributes:
                # Check if this is the last significant key
                # If there are remaining keys, and they are NOT all integers/internal, then this is a field name
                is_last_significant = True
                for next_key in keys[keys.index(key)+1:]:
                    if isinstance(next_key, int):
                        continue
                    if next_key in skip_schema_internals:
                         continue
                    # Found a significant following key -> 'key' is a path segment (field name)
                    is_last_significant = False
                    break
                
                if is_last_significant:
                    prev_key = key
                    continue
            
            # Skip 'required' as it's handled separately
            if key == 'required':
                prev_key = key
                continue
            
            result.append(str(key))
            prev_key = key
        
        return '.'.join(result)


def publish_to_confluence(
    method: str,
    path: str,
    events: List[Dict[str, Any]],
    space_key: Optional[str] = None,
    parent_page_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Convenience function to publish endpoint history to Confluence.
    
    Args:
        method: HTTP method
        path: Endpoint path
        events: List of change events from TurboHistoryBuilder
        space_key: Optional space key override
        parent_page_id: Optional parent page ID override
    
    Returns:
        Dict with success status and page info
    """
    try:
        publisher = ConfluencePublisher(
            space_key=space_key or settings.confluence_space_key,
            parent_page_id=parent_page_id or settings.confluence_parent_page_id
        )
        
        return publisher.publish_history(
            method=method,
            path=path,
            events=events
        )
    except Exception as e:
        logger.error(f"Error in publish_to_confluence: {e}")
        return {
            "success": False,
            "error": str(e)
        }


if __name__ == "__main__":
    # Test the publisher
    import sys
    
    print("Confluence Publisher Test")
    print("=" * 50)
    
    try:
        publisher = ConfluencePublisher()
        
        # Check connection
        print("\n1. Checking connection...")
        status = publisher.check_connection()
        if status["connected"]:
            print(f"   ✅ Connected to space: {status['space_name']} ({status['space_key']})")
        else:
            print(f"   ❌ Connection failed: {status.get('error')}")
            print("\n   Available spaces:")
            for space in publisher.list_spaces(limit=20):
                print(f"     - {space['key']}: {space['name']}")
            sys.exit(1)
        
        # Create test page
        print("\n2. Creating test page...")
        
        # Sample event with schema
        test_schema = {
            "summary": "Test endpoint",
            "description": "Test description",
            "parameters": {
                "header": {
                    "X-Request-ID": {
                        "type": "string",
                        "required": False,
                        "description": "Request trace ID"
                    }
                },
                "query": {},
                "path": {}
            },
            "requestBody": {
                "type": "object",
                "properties": {
                    "order": {
                        "type": "object",
                        "properties": {
                            "guid": {"type": "string", "format": "uuid", "description": "Order GUID"},
                            "number": {"type": "string", "description": "Order number"}
                        },
                        "required": ["guid", "number"]
                    },
                    "products": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "guid": {"type": "string", "format": "uuid"},
                                "quantity": {"type": "integer"}
                            }
                        }
                    }
                },
                "required": ["order", "products"]
            },
            "responses": {
                "200": {
                    "description": "Success",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "success": {"type": "boolean"},
                            "message": {"type": "string"}
                        }
                    }
                },
                "400": {
                    "description": "Bad Request",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "error": {"type": "string"},
                            "details": {"type": "array", "items": {"type": "string"}}
                        }
                    }
                }
            }
        }
        
        test_events = [
            {
                "date": "2024-01-01",
                "task_id": "TEST-123",
                "mr_id": 100,
                "author": "test.user",
                "type": "🆕 CREATED",
                "changes": "Endpoint Created",
                "diff": None,
                "schema": test_schema,
                "previous_schema": None,
                "link": "https://gitlab.example.com/merge_requests/100",
                "jira_link": "https://jira.example.com/browse/TEST-123"
            },
            {
                "date": "2024-01-15",
                "task_id": "TEST-456",
                "mr_id": 150,
                "author": "another.user",
                "type": "📝 MODIFIED",
                "changes": "Added field products.returnReason",
                "diff": {
                    "dictionary_item_added": ["root['requestBody']['properties']['products']['items']['properties']['returnReason']"]
                },
                "schema": {
                    **test_schema,
                    "requestBody": {
                        **test_schema["requestBody"],
                        "properties": {
                            **test_schema["requestBody"]["properties"],
                            "products": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "guid": {"type": "string", "format": "uuid"},
                                        "quantity": {"type": "integer"},
                                        "returnReason": {"type": "string", "description": "Reason for return"}
                                    }
                                }
                            }
                        }
                    }
                },
                "previous_schema": test_schema,
                "link": "https://gitlab.example.com/merge_requests/150",
                "jira_link": "https://jira.example.com/browse/TEST-456"
            }
        ]
        
        result = publisher.publish_history(
            method="POST",
            path="/test/example",
            events=test_events,
            summary="Test Example Endpoint",
            description="This is a test page for verifying Confluence publisher"
        )
        
        if result["success"]:
            print(f"   ✅ Page published successfully!")
            print(f"   Page ID: {result['page_id']}")
            print(f"   URL: {result['page_url']}")
        else:
            print(f"   ❌ Failed: {result.get('error')}")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
