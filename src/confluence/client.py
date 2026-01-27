"""
Confluence Client Wrapper.

Thin wrapper around Atlassian Confluence API for page operations.

Usage:
    from src.confluence.client import ConfluenceClient
    
    client = ConfluenceClient(base_url, token, space_key)
    client.create_or_update_page(title, body, parent_page_id)
"""
import logging
from typing import Any, Dict, List, Optional

from atlassian import Confluence
from config import settings

logger = logging.getLogger(__name__)


class ConfluenceClient:
    """
    Wrapper for Atlassian Confluence API operations.
    
    Handles connection, page lookup, and create/update operations.
    """
    
    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        space_key: str = "pickup",
        parent_page_id: Optional[str] = None
    ):
        """
        Initialize Confluence client.
        
        Args:
            base_url: Confluence server URL (from settings if not provided)
            token: Personal access token (from settings if not provided)
            space_key: Confluence space key
            parent_page_id: Parent page ID for new pages
        """
        self.base_url = base_url or settings.CONFLUENCE_URL
        self.token = token or settings.CONFLUENCE_PAT
        self.space_key = space_key
        self.parent_page_id = parent_page_id
        
        # Initialize Atlassian Confluence client
        self.confluence = Confluence(
            url=self.base_url,
            token=self.token
        )
        
        logger.info(f"ConfluenceClient initialized for space '{space_key}'")
    
    def check_connection(self) -> Dict[str, Any]:
        """
        Check Confluence connection and space access.
        
        Returns:
            Dict with connection status and space info
        """
        try:
            space = self.confluence.get_space(self.space_key)
            return {
                "connected": True,
                "space_key": self.space_key,
                "space_name": space.get("name", "Unknown"),
                "base_url": self.base_url
            }
        except Exception as e:
            logger.error(f"Connection check failed: {e}")
            return {
                "connected": False,
                "error": str(e),
                "base_url": self.base_url
            }
    
    def list_spaces(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        List available Confluence spaces.
        
        Args:
            limit: Maximum number of spaces to return
            
        Returns:
            List of dicts with key, name, type
        """
        try:
            response = self.confluence.get_all_spaces(limit=limit)
            spaces = response.get("results", [])
            return [
                {
                    "key": s.get("key"),
                    "name": s.get("name"),
                    "type": s.get("type")
                }
                for s in spaces
            ]
        except Exception as e:
            logger.error(f"Error listing spaces: {e}")
            return []
    
    def find_page_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """
        Find a Confluence page by title in the configured space.
        
        Args:
            title: Page title to search for
            
        Returns:
            Page dict if found, None otherwise
        """
        try:
            if self.confluence.page_exists(self.space_key, title):
                page_id = self.confluence.get_page_id(self.space_key, title)
                return {
                    "id": page_id,
                    "title": title,
                    "space_key": self.space_key
                }
            return None
        except Exception as e:
            logger.error(f"Error finding page '{title}': {e}")
            return None
    
    def get_page_by_id(self, page_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a Confluence page by ID.
        
        Args:
            page_id: Page ID
            
        Returns:
            Page dict if found, None otherwise
        """
        try:
            page = self.confluence.get_page_by_id(page_id, expand="body.storage,version")
            return page
        except Exception as e:
            logger.error(f"Error getting page {page_id}: {e}")
            return None
    
    def create_page(
        self,
        title: str,
        body: str,
        parent_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a new Confluence page.
        
        Args:
            title: Page title
            body: Page content in Storage Format
            parent_id: Parent page ID (uses default if not provided)
            
        Returns:
            Created page dict with id, title, url
        """
        parent = parent_id or self.parent_page_id
        
        try:
            result = self.confluence.create_page(
                space=self.space_key,
                title=title,
                body=body,
                parent_id=parent,
                type="page",
                representation="storage"
            )
            
            page_id = result.get("id")
            page_url = f"{self.base_url}/pages/viewpage.action?pageId={page_id}"
            
            logger.info(f"Created page: {title} (ID: {page_id})")
            
            return {
                "id": page_id,
                "title": title,
                "url": page_url,
                "created": True
            }
        except Exception as e:
            logger.error(f"Error creating page '{title}': {e}")
            raise
    
    def update_page(
        self,
        page_id: str,
        title: str,
        body: str
    ) -> Dict[str, Any]:
        """
        Update an existing Confluence page.
        
        Args:
            page_id: Page ID to update
            title: New page title
            body: New page content in Storage Format
            
        Returns:
            Updated page dict with id, title, url
        """
        try:
            result = self.confluence.update_page(
                page_id=page_id,
                title=title,
                body=body,
                representation="storage"
            )
            
            page_url = f"{self.base_url}/pages/viewpage.action?pageId={page_id}"
            
            logger.info(f"Updated page: {title} (ID: {page_id})")
            
            return {
                "id": page_id,
                "title": title,
                "url": page_url,
                "updated": True
            }
        except Exception as e:
            logger.error(f"Error updating page {page_id}: {e}")
            raise
    
    def create_or_update_page(
        self,
        title: str,
        body: str,
        parent_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a page or update if it already exists.
        
        Args:
            title: Page title
            body: Page content in Storage Format
            parent_id: Parent page ID for new pages
            
        Returns:
            Result dict with id, title, url, and created/updated flag
        """
        existing = self.find_page_by_title(title)
        
        if existing:
            return self.update_page(existing["id"], title, body)
        else:
            return self.create_page(title, body, parent_id)
    
    def page_exists(self, title: str) -> bool:
        """Check if a page with the given title exists."""
        try:
            return self.confluence.page_exists(self.space_key, title)
        except Exception:
            return False
    
    def get_page_id(self, title: str) -> Optional[str]:
        """Get page ID by title."""
        try:
            return self.confluence.get_page_id(self.space_key, title)
        except Exception:
            return None
