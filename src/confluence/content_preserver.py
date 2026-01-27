import re
import logging
from typing import Dict, Optional, Tuple, List
from bs4 import BeautifulSoup
from .constants import SKIP_PREFIXES

logger = logging.getLogger(__name__)

class ContentPreserver:
    """
    Parses existing Confluence page content to preserve user-edited descriptions.
    
    Structure of extracted data:
    {
        "TASK-KEY": {
            "request": { "field.path": "User description" },
            "response": { "field.path": "User description" },
            "response_error_400": { "field.path": "User description" }
        },
        "NO-TASK": { ... }
    }
    """

    def extract_descriptions(self, storage_html: str) -> Dict[str, Dict[str, Dict[str, str]]]:
        """
        Main entry point. Parses HTML and returns nested dictionary of descriptions.
        """
        if not storage_html:
            return {}

        soup = BeautifulSoup(storage_html, 'html.parser')
        preserved_data = {}

        # Find all MultiExcerpts that contain descriptions
        # Confluence formatting: <ac:parameter ac:name="MultiExcerptName">NAME</ac:parameter>
        # We look for the containing macro
        macros = soup.find_all('ac:structured-macro', attrs={'ac:name': 'multiexcerpt'})
        
        for macro in macros:
            name_param = macro.find('ac:parameter', attrs={'ac:name': 'MultiExcerptName'})
            if not name_param:
                continue
                
            excerpt_name = name_param.get_text()
            
            # Parse name to identify context: "{TASK_ID} {TYPE} Description"
            # Examples: "LOGRETAIL-1742 Request Description", "LOGRETAIL-1742 Header Description"
            task_id, section_type = self._parse_excerpt_name(excerpt_name)
            
            if not task_id or not section_type:
                continue
                
            # Extract table from this macro
            table = macro.find('table')
            if not table:
                continue
                
            field_descriptions = self._parse_fields_table(table)
            
            if field_descriptions:
                if task_id not in preserved_data:
                    preserved_data[task_id] = {}
                preserved_data[task_id][section_type] = field_descriptions
                
        return preserved_data

    def _parse_excerpt_name(self, name: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Extracts Task ID and section type from MultiExcerpt name.
        Returns: (task_id, section_key)
        section_key specific values: 'request', 'response', 'header', 'error_400' etc
        """
        # Common patterns
        # "LOGRETAIL-1742 Request Description"
        # "LOGRETAIL-1742 Response Description"
        # "LOGRETAIL-1742 Header Description"
        # "LOGRETAIL-1742 Error 400 Description"
        
        parts = name.split()
        if len(parts) < 3:
            return None, None
            
        task_id = parts[0]
        # Check if first part looks like a task ID (e.g. UPPER-123) or "NO-TASK"
        # Since titles can change, we might iterate parts. 
        # But our generator makes them strictly "{task_id} ..."
        
        suffix = " ".join(parts[1:])
        
        if "Request Description" in suffix:
            return task_id, "request"
        elif "Response Description" in suffix:
            return task_id, "response"
        elif "Header Description" in suffix:
            return task_id, "header"
        elif "Error" in suffix and "Description" in suffix:
            # e.g. "Error 400 Description" -> "error_400"
            # Extract code
            match = re.search(r'Error (\d+) Description', suffix)
            if match:
                return task_id, f"responses.{match.group(1)}"
                
        return None, None

    def _parse_fields_table(self, table) -> Dict[str, str]:
        """
        Parses a field table and extracts descriptions with reconstructed paths.
        Logic:
        - Determine indentation columns (first N columns are for Keys)
        - Last column is Description
        - Reconstruct nested path based on which column has the key
        """
        descriptions = {}
        rows = table.find_all('tr')
        if not rows:
            return {}
            
        # Determine headers to find "Description" column index and number of key columns
        # However, headers in our generator are: <th>Ключ</th> <th/> <th/> ... <th>Формат</th> <th>Обязательный?</th> <th>Описание</th>
        headers = rows[0].find_all('th')
        if not headers:
            return {}
            
        # Find "Описание" or "Description" index
        desc_idx = -1
        for i, th in enumerate(headers):
            text = th.get_text(strip=True).lower()
            if 'описание' in text or 'description' in text:
                desc_idx = i
                break
        
        if desc_idx == -1:
            return {}
            
        # Structure stack for path reconstruction: list of keys at each level
        # level -> key
        path_stack: List[str] = []
        
        # Iterate data rows
        for row in rows[1:]:
            cells = row.find_all('td')
            if not cells:
                continue
                
            # If cells count < desc_idx, skip
            if len(cells) <= desc_idx:
                continue
                
            # Extract description
            desc_cell = cells[desc_idx]
            # Convert simple string, keeping basic text but ignoring empty HTML
            # Use get_text() but carefully
            description = desc_cell.get_text(" ", strip=True)
            
            # Identify key and level
            # We assume the first filled cell before 'Format' column matches level
            # Our table generator:
            # Key cols... | Format | Required | Description
            # Let's count how many key cols based on header (cols before Format/Type)
            # Or simpler: Just find the first non-empty cell in the first few columns
            
            key_text = ""
            level = 0
            
            # Iterate cells up to desc_idx - 2 (assuming Format, Required, Desc are last 3)
            # Actually better to rely on known column count or iterate until we find text
            
            # Our generator puts keys in specific columns 0..N
            found_key = False
            for i in range(desc_idx): # Scan columns before description
                cell_text = cells[i].get_text(strip=True)
                if cell_text:
                    # Found the key at this level
                    key_text = cell_text
                    level = i
                    found_key = True
                    break
            
            if not found_key:
                continue
                
            # Update stack
            # If level is deeper, append
            # If level is same, replace last
            # If level is shallower, pop until match
            
            # Ensure stack is large enough (fill gaps if skipped levels - theoretically shouldn't happen with valid tree)
            while len(path_stack) > level:
                path_stack.pop()
                
            path_stack.append(key_text)
            
            # Construct full path
            full_path = ".".join(path_stack)
            
            if description:
                descriptions[full_path] = description
                
        return descriptions
