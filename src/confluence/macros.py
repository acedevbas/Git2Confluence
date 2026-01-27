"""
Confluence Macro Generators.

Pure functions for generating Confluence Storage Format macros.
All functions are stateless and return HTML strings.

Usage:
    from src.confluence.macros import generate_status_macro, generate_code_macro
    
    html = generate_status_macro("POST")
    code_html = generate_code_macro('{"key": "value"}', language="json")
"""
import uuid
from typing import Optional


# =============================================================================
# Constants
# =============================================================================

# Jira server configuration (from reference page)
JIRA_SERVER_NAME = "Jira Software"
JIRA_SERVER_ID = "f07fdead-9301-3f71-bd8f-d9ef673b9368"

# HTTP method colors
METHOD_COLORS = {
    'GET': 'Blue',
    'POST': 'Green',
    'PUT': 'Yellow',
    'PATCH': 'Yellow',
    'DELETE': 'Red'
}


# =============================================================================
# Core Macro Functions
# =============================================================================

def generate_macro_id() -> str:
    """Generate unique ID for Confluence macro."""
    return str(uuid.uuid4())


def generate_status_macro(method: str) -> str:
    """
    Generate colored badge for HTTP method.
    
    Args:
        method: HTTP method (GET, POST, PUT, PATCH, DELETE)
        
    Returns:
        Confluence Storage Format HTML for status macro
    """
    color = METHOD_COLORS.get(method.upper(), 'Grey')
    macro_id = generate_macro_id()
    
    return f'''<ac:structured-macro ac:name="status" ac:schema-version="1" ac:macro-id="{macro_id}"><ac:parameter ac:name="colour">{color}</ac:parameter><ac:parameter ac:name="title">{method.upper()}</ac:parameter></ac:structured-macro>'''


def generate_code_macro(
    code: str,
    language: str = 'json',
    collapse: bool = True,
    title: Optional[str] = None
) -> str:
    """
    Generate code block macro for Confluence.
    
    Args:
        code: Code content to display
        language: Syntax highlighting language
        collapse: Whether to collapse the code block
        title: Optional title for the code block
        
    Returns:
        Confluence Storage Format HTML for code macro
    """
    if not code:
        code = '{}'
    
    macro_id = generate_macro_id()
    collapse_param = '<ac:parameter ac:name="collapse">true</ac:parameter>' if collapse else ''
    title_param = f'<ac:parameter ac:name="title">{title}</ac:parameter>' if title else ''
    
    # Escape CDATA
    code_escaped = code.replace("]]>", "]]]]><![CDATA[>")
    
    return f'''<ac:structured-macro ac:name="code" ac:schema-version="1" ac:macro-id="{macro_id}"><ac:parameter ac:name="language">{language}</ac:parameter>{title_param}{collapse_param}<ac:plain-text-body><![CDATA[{code_escaped}]]></ac:plain-text-body></ac:structured-macro>'''


def generate_jira_macro(task_key: str) -> str:
    """
    Generate Jira issue link macro.
    
    Args:
        task_key: Jira issue key (e.g., "LOGRETAIL-1891")
        
    Returns:
        Confluence Storage Format HTML for Jira macro
    """
    macro_id = generate_macro_id()
    return f'''<ac:structured-macro ac:name="jira" ac:schema-version="1" ac:macro-id="{macro_id}"><ac:parameter ac:name="server">{JIRA_SERVER_NAME}</ac:parameter><ac:parameter ac:name="serverId">{JIRA_SERVER_ID}</ac:parameter><ac:parameter ac:name="key">{task_key}</ac:parameter></ac:structured-macro>'''


# =============================================================================
# MultiExcerpt Macros
# =============================================================================

def generate_multiexcerpt(
    name: str,
    content: str,
    output_type: str = "INLINE"
) -> str:
    """
    Generate MultiExcerpt macro for content reuse.
    
    Args:
        name: MultiExcerpt name (e.g., "LOGRETAIL-1870 Request Body")
        content: HTML content inside the macro
        output_type: "INLINE" or "BLOCK"
        
    Returns:
        Confluence Storage Format HTML for MultiExcerpt macro
    """
    macro_id = generate_macro_id()
    return f'''<ac:structured-macro ac:name="multiexcerpt" ac:schema-version="1" ac:macro-id="{macro_id}"><ac:parameter ac:name="MultiExcerptName">{name}</ac:parameter><ac:parameter ac:name="atlassian-macro-output-type">{output_type}</ac:parameter><ac:rich-text-body>{content}</ac:rich-text-body></ac:structured-macro>'''


def generate_multiexcerpt_include(
    name: str,
    page_title: Optional[str] = None
) -> str:
    """
    Generate MultiExcerpt Include macro to insert content from another MultiExcerpt.
    
    Args:
        name: MultiExcerpt name to include
        page_title: Source page title (None for current page)
        
    Returns:
        Confluence Storage Format HTML for MultiExcerpt Include macro
    """
    macro_id = generate_macro_id()
    
    if page_title:
        page_param = f'''<ac:parameter ac:name="PageWithExcerpt"><ac:link><ri:page ri:content-title="{page_title}" /></ac:link></ac:parameter>'''
    else:
        page_param = ''
    
    return f'''<ac:structured-macro ac:name="multiexcerpt-include" ac:schema-version="1" ac:macro-id="{macro_id}"><ac:parameter ac:name="MultiExcerptName">{name}</ac:parameter>{page_param}</ac:structured-macro>'''


def generate_multiexcerpt_include_by_id(
    page_id: str,
    excerpt_name: str = "environments"
) -> str:
    """
    Generate MultiExcerpt Include macro using page ID instead of title.
    
    Args:
        page_id: Confluence page ID containing the MultiExcerpt
        excerpt_name: Name of the MultiExcerpt to include
        
    Returns:
        Confluence Storage Format HTML for the include macro
    """
    macro_id = generate_macro_id()
    
    return f'''<ac:structured-macro ac:name="multiexcerpt-include" ac:schema-version="1" ac:macro-id="{macro_id}"><ac:parameter ac:name="PageWithExcerpt"><ri:page ri:content-id="{page_id}" /></ac:parameter><ac:parameter ac:name="MultiExcerptName">{excerpt_name}</ac:parameter></ac:structured-macro>'''


def generate_hidden_excerpt(content: str) -> str:
    """
    Generate hidden Excerpt macro (for hidden MultiExcerpt "Контракт").
    
    Args:
        content: HTML content inside the macro (usually another MultiExcerpt)
        
    Returns:
        Confluence Storage Format HTML for hidden Excerpt macro
    """
    macro_id = generate_macro_id()
    return f'''<ac:structured-macro ac:name="excerpt" ac:schema-version="1" ac:macro-id="{macro_id}"><ac:parameter ac:name="hidden">true</ac:parameter><ac:parameter ac:name="atlassian-macro-output-type">INLINE</ac:parameter><ac:rich-text-body>{content}</ac:rich-text-body></ac:structured-macro>'''


def generate_expand_macro(content: str, title: Optional[str] = None) -> str:
    """
    Generate expand/collapse macro.
    
    Args:
        content: HTML content to wrap
        title: Optional title for expand section
        
    Returns:
        Confluence Storage Format HTML for expand macro
    """
    macro_id = generate_macro_id()
    title_param = f'<ac:parameter ac:name="title">{title}</ac:parameter>' if title else ''
    
    return f'''<ac:structured-macro ac:name="expand" ac:schema-version="1" ac:macro-id="{macro_id}">{title_param}<ac:rich-text-body>{content}</ac:rich-text-body></ac:structured-macro>'''


def generate_warning_macro(title: Optional[str], content: str) -> str:
    """
    Generate warning macro (red box).
    
    Args:
        title: Optional title for the warning
        content: HTML content inside the body
        
    Returns:
        Confluence Storage Format HTML for warning macro
    """
    macro_id = generate_macro_id()
    title_param = f'<ac:parameter ac:name="title">{title}</ac:parameter>' if title else ''
    
    return f'''<ac:structured-macro ac:name="warning" ac:schema-version="1" ac:macro-id="{macro_id}">{title_param}<ac:rich-text-body><p>{content}</p></ac:rich-text-body></ac:structured-macro>'''
