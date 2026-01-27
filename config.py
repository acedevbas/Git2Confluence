"""
Конфигурация OpenAPI History Tracker.
Использует Pydantic Settings для валидации и поддержки env variables.
"""
from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional
import os


class Settings(BaseSettings):
    """Централизованная конфигурация приложения."""
    
    
    # GitLab
    gitlab_url: str = Field(
        default="https://git.vseinstrumenti.net",
        description="GitLab instance URL"
    )
    gitlab_token: Optional[str] = Field(
        default=None,
        description="GitLab personal access token (from env GITLAB_TOKEN or via API request)"
    )
    project_path: Optional[str] = Field(
        default=None,
        description="GitLab project path (now passed via API request)"
    )
    gitlab_ssl_verify: bool = Field(
        default=False, 
        description="Verify SSL certificates for GitLab connection"
    )
    
    # Cache settings
    cache_backend: str = Field(
        default="disk",
        description="Cache backend: 'disk' (DiskCache)"
    )
    cache_dir: str = Field(
        default="./cache_data",
        description="Directory for DiskCache storage"
    )
    
    # Target endpoint for analysis
    target_endpoint: str = Field(
        default="POST /orders/products",
        description="Target endpoint to track (format: 'METHOD /path')"
    )
    
    # Processing
    mr_limit: Optional[int] = Field(default=None, description="Maximum number of MRs to process (None for unlimited)")
    flush_cache_on_start: bool = Field(
        default=False,
        description="Clear cache on startup"
    )
    
    # Regression Filter - фильтрация временных регрессий
    grace_period_days: int = Field(
        default=7, 
        description="Grace period in days before confirming deletion"
    )
    filter_temporary_regressions: bool = Field(
        default=True,
        description="Filter out deletions that were restored within grace period"
    )
    show_regression_details: bool = Field(
        default=True,
        description="Show filtered regressions in a separate section"
    )
    min_regression_duration_hours: float = Field(
        default=1.0,
        description="Minimum duration (hours) for counting as regression"
    )
    
    # Jira integration
    jira_base_url: str = Field(
        default="https://jira.vseinstrumenti.net/browse",
        description="Jira browse URL for task links"
    )
    
    # Confluence integration
    confluence_base_url: str = Field(
        default="https://kb.vseinstrumenti.ru",
        description="Confluence server URL"
    )
    confluence_token: Optional[str] = Field(
        default=None,
        description="Confluence personal access token (from env CONFLUENCE_TOKEN)"
    )
    confluence_space_key: str = Field(
        default="API",
        description="Confluence space key for documentation"
    )
    confluence_parent_page_id: Optional[str] = Field(
        default=None,
        description="Parent page ID for new pages"
    )
    publish_to_confluence: bool = Field(
        default=False,
        description="Publish results to Confluence after analysis"
    )
    
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore"
    }
    
    @property
    def target_method(self) -> str:
        """Extracts HTTP method from target_endpoint."""
        return self.target_endpoint.split(' ', 1)[0] if ' ' in self.target_endpoint else "GET"
    
    @property
    def target_path(self) -> str:
        """Extracts path from target_endpoint."""
        return self.target_endpoint.split(' ', 1)[1] if ' ' in self.target_endpoint else self.target_endpoint


# Singleton instance
settings = Settings()
