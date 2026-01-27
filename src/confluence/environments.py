"""
Environments Block Module - Handles inclusion of environment information (prod/test URLs).

This module provides a clean interface for:
- Finding the environments source page by configurable title
- Generating MultiExcerpt Include macros

Usage:
    from src.confluence.environments import EnvironmentsBlock
    
    block = EnvironmentsBlock(confluence_client, space_key="pickup")
    page_id = block.find_page()
    html = block.generate_include(page_id)
"""
import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)


class EnvironmentsBlock:
    """
    Handles environments section (prod/test URLs) inclusion via MultiExcerpt.
    
    Attributes:
        DEFAULT_PAGE_TITLE: Default title of the source page
        DEFAULT_EXCERPT_NAME: Default MultiExcerpt name to include
    """
    
    DEFAULT_PAGE_TITLE = "УРЛЫ прод+стенды"
    DEFAULT_EXCERPT_NAME = "environments"
    
    def __init__(
        self,
        confluence_client,
        space_key: str,
        parent_page_id: Optional[str] = None,
        page_title: Optional[str] = None,
        excerpt_name: Optional[str] = None
    ):
        """
        Initialize environments block handler.
        
        Args:
            confluence_client: Atlassian Confluence API client
            space_key: Confluence space key to search in
            parent_page_id: Page ID where we publish docs (search starts from here upward)
            page_title: Custom source page title (default: "Данные для автоматизации")
            excerpt_name: Custom MultiExcerpt name (default: "environments")
        """
        self.confluence = confluence_client
        self.space_key = space_key
        self.parent_page_id = parent_page_id
        self.page_title = page_title or self.DEFAULT_PAGE_TITLE
        self.excerpt_name = excerpt_name or self.DEFAULT_EXCERPT_NAME
        self._cached_page_id: Optional[str] = None
        self._cached_page_title: Optional[str] = None
        self._cached_space_key: Optional[str] = None
    
    def find_page(self, title: Optional[str] = None) -> Optional[str]:
        """
        Find the environments source page by title.
        
        If parent_page_id is set, searches only in parent hierarchy (upward).
        Otherwise falls back to space-wide search.
        
        Args:
            title: Page title to search for (uses configured title if None)
            
        Returns:
            Page ID if found, None otherwise
        """
        search_title = title or self.page_title
        
        try:
            # If parent_page_id is set, search in hierarchy only
            if self.parent_page_id:
                page_data = self._find_in_parent_hierarchy(search_title)
                if page_data:
                    self._cached_page_id = page_data['id']
                    self._cached_page_title = page_data.get('title')
                    self._cached_space_key = page_data.get('space_key')
                    return self._cached_page_id
                
                logger.warning(
                    f"Environments page '{search_title}' not found in parent hierarchy of {self.parent_page_id}"
                )
                return None  # Strict check: do not fall back to space search if parent is specified
            
            # Fallback: search in entire space
            # Use CQL contains search for consistency with hierarchy search
            cql = f'title ~ "{search_title}" AND space = "{self.space_key}"'
            results = self.confluence.cql(cql, limit=1)
            
            if results and results.get('results'):
                page = results['results'][0]
                page_id = page['content']['id']
                logger.info(f"Found environments page '{search_title}': {page_id}")
                
                self._cached_page_id = page_id
                self._cached_page_title = page['content']['title']
                self._cached_space_key = self.space_key
                return page_id
            else:
                logger.warning(
                    f"Environments page '{search_title}' not found in space '{self.space_key}'"
                )
                return None
        except Exception as e:
            logger.error(f"Error finding environments page: {e}")
            return None
    
    def _find_in_parent_hierarchy(self, title: str) -> Optional[Dict[str, str]]:
        """
        Search for page in parent hierarchy using CQL.
        
        Iterates up the ancestor chain and for each ancestor, searches for 
        a descendant page with the matching title using CQL.
        
        Query: title ~ "..." AND ancestor = {ancestor_id}
        
        Args:
            title: Page title to find
            
        Returns:
            Dict {'id', 'title', 'space_key'} if found, None otherwise
        """
        current_id = self.parent_page_id
        
        # 1. Collect all ancestors first
        ancestors_ids = []
        try:
            page_info = self.confluence.get_page_by_id(current_id, expand='ancestors')
            if page_info and 'ancestors' in page_info:
                ancestors_ids = [a['id'] for a in page_info['ancestors']]
            
            # Search from closest parent up
            ancestors_ids.reverse()
            
        except Exception as e:
            logger.warning(f"Error fetching ancestors for {current_id}: {e}")
            return None
            
        # 2. Iterate ancestors and search in their subtrees
        for ancestor_id in ancestors_ids:
            try:
                # Use 'ancestor' but validate depth to prevent infinite wide scope
                # Using ~ for "contains" search as requested
                cql = f'title ~ "{title}" AND ancestor = {ancestor_id}'
                logger.info(f"Searching environments page with CQL: {cql}")
                
                results = self.confluence.cql(cql, limit=1)
                
                if results and results.get('results'):
                    page = results['results'][0]
                    content_obj = page.get('content', page)
                    page_id = content_obj.get('id')
                    
                    # Validate depth: Check if found page is close enough to the ancestor
                    # We don't want to find pages deep in other project trees sharing a root.
                    try:
                        # Fetch full page with ancestors to calculate depth
                        full_page = self.confluence.get_page_by_id(page_id, expand='ancestors')
                        ancestors = full_page.get('ancestors', [])
                        
                        ancestor_ids = [str(a['id']) for a in ancestors]
                        logger.info(f"Page {page_id} ancestors: {ancestor_ids} (looking for {ancestor_id})")
                        
                        # Find distance between ancestor_id and page
                        depth = -1
                        for i, anc in enumerate(reversed(ancestors)):
                            if str(anc['id']) == str(ancestor_id):
                                depth = i + 1  # 1 = direct parent
                                break
                        
                        MAX_DEPTH = 4  # Increased from 2 to 4 to allow nested folder structures
                        
                        if depth == -1 or depth > MAX_DEPTH:
                            logger.info(f"Skipping page {page_id} found via ancestor {ancestor_id}: "
                                      f"Depth {depth} exceeds limit (max {MAX_DEPTH}). It might be in a deep sibling structure.")
                            continue
                            
                        logger.info(f"Page {page_id} passed depth check: {depth} levels from {ancestor_id}")
                    
                        page_title = content_obj.get('title')
                        
                        # Try to extract space key
                        extracted_space_key = None
                        if 'space' in content_obj:
                             extracted_space_key = content_obj['space'].get('key')
                        elif 'result' in page and 'space' in page['result']:
                             extracted_space_key = page['result']['space'].get('key')
                        
                        # Fallback to current space
                        final_space_key = extracted_space_key or self.space_key
                        
                        logger.info(f"Found environments page '{title}' in subtree of ancestor {ancestor_id}: ID={page_id}, Title='{page_title}', Space='{final_space_key}'")
                        
                        return {
                            'id': page_id,
                            'title': page_title,
                            'space_key': final_space_key
                        }
                        
                    except Exception as e:
                        logger.warning(f"Error validating depth for page {page_id}: {e}")
                        continue
                    
            except Exception as e:
                logger.warning(f"Error searching with CQL for ancestor {ancestor_id}: {e}")
                continue
        
        return None
    
    def get_page_info(self) -> Optional[Dict[str, str]]:
        """
        Get full page info (uses cached value or searches).
        
        Returns:
            Dict {'id', 'title', 'space_key'} if found, None otherwise
        """
        if self._cached_page_id:
            return {
                'id': self._cached_page_id,
                'title': self._cached_page_title,
                'space_key': self._cached_space_key
            }
        
        if self.find_page():
            return {
                'id': self._cached_page_id,
                'title': self._cached_page_title,
                'space_key': self._cached_space_key
            }
        
        return None
    
    @staticmethod
    def generate_multiexcerpt_include_by_id(
        page_id: str,
        excerpt_name: str = "environments"
    ) -> str:
        """
        Generate MultiExcerpt Include macro HTML using page ID.
        
        Args:
            page_id: Confluence page ID containing the MultiExcerpt
            excerpt_name: Name of the MultiExcerpt to include
            
        Returns:
            Confluence Storage Format HTML for the include macro
        """
        import uuid
        macro_id = str(uuid.uuid4())
        
        return f'''<ac:structured-macro ac:name="multiexcerpt-include" ac:schema-version="1" ac:macro-id="{macro_id}"><ac:parameter ac:name="PageWithExcerpt"><ri:page ri:content-id="{page_id}" /></ac:parameter><ac:parameter ac:name="MultiExcerptName">{excerpt_name}</ac:parameter></ac:structured-macro>'''
    
    def generate_include(self, page_id: Optional[str] = None) -> str:
        """
        Generate the environments include block.
        
        Args:
            page_id: Source page ID (uses cached/searched if None)
            
        Returns:
            Confluence Storage Format HTML for the include, or empty string if no page
        """
        target_page_id = page_id or self.get_page_id()
        
        if not target_page_id:
            logger.warning("Cannot generate environments include - no page ID")
            return ""
        
        return self.generate_multiexcerpt_include_by_id(
            target_page_id,
            self.excerpt_name
        )


# Convenience function for simple usage
def create_environments_block(
    confluence_client,
    space_key: str,
    page_title: Optional[str] = None
) -> EnvironmentsBlock:
    """
    Create an EnvironmentsBlock with default settings.
    
    Args:
        confluence_client: Confluence API client
        space_key: Space key to search in
        page_title: Optional custom page title
        
    Returns:
        Configured EnvironmentsBlock instance
    """
    return EnvironmentsBlock(
        confluence_client=confluence_client,
        space_key=space_key,
        page_title=page_title
    )
