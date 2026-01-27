# Confluence publishing
from .publisher import ConfluencePublisher
from .template_generator import ConfluenceTemplateGenerator  # DEPRECATED facade
from .client import ConfluenceClient
from .environments import EnvironmentsBlock

# New modular components
from . import macros
from . import schema_utils
from . import tables
from . import history_block
from . import page_builder

__all__ = [
    'ConfluencePublisher',
    'ConfluenceTemplateGenerator',  # DEPRECATED
    'ConfluenceClient',
    'EnvironmentsBlock',
    'macros',
    'schema_utils', 
    'tables',
    'history_block',
    'page_builder',
]
