"""
Pydantic schemas for API requests and responses.
"""
from typing import Optional, List, Dict
from pydantic import BaseModel, Field
from datetime import datetime, date


class DocumentationRequest(BaseModel):
    """Request body for documentation generation."""
    
    target_endpoint: str = Field(
        ...,
        description="Target endpoint to document in format 'METHOD /path'",
        example="POST /orders/products/return"
    )
    confluence_parent_page_id: str = Field(
        ...,
        description="Confluence parent page ID where documentation will be created",
        example="169804502"
    )
    confluence_token: Optional[str] = Field(
        default=None,
        description="Confluence API token for authentication (uses CONFLUENCE_TOKEN from .env if not provided)"
    )
    confluence_space_key: Optional[str] = Field(
        default=None,
        description="Confluence space key (uses CONFLUENCE_SPACE_KEY from .env if not provided)"
    )
    gitlab_token: Optional[str] = Field(
        default=None,
        description="GitLab personal access token (REQUIRED - no default value for security)"
    )
    project_path: str = Field(
        ...,
        description="GitLab project path (e.g., 'logistic/retail/rms/rms-api')",
        example="logistic/retail/rms/rms-api"
    )
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "target_endpoint": "POST /orders/products/return",
                    "project_path": "logistic/retail/rms/rms-api",
                    "confluence_parent_page_id": "169804502",
                    "confluence_token": "your-confluence-token",
                    "confluence_space_key": "pickup",
                    "gitlab_token": "glpat-xxxxxxxxxxxxxxxxxxxx"
                }
            ]
        }
    }


class HistoryEventResponse(BaseModel):
    """Single history event in the response."""
    
    event_type: str = Field(..., description="CREATED, MODIFIED, or DELETED")
    task_id: str = Field(..., description="JIRA task ID")
    mr_iid: int = Field(..., description="GitLab MR IID")
    author: str = Field(..., description="Author name")
    merged_at: datetime = Field(..., description="When MR was merged")
    field_changes: Optional[Dict[str, str]] = Field(None, description="Field changes: path -> change_type")


class DocumentationResponse(BaseModel):
    """Response after documentation generation."""
    
    success: bool = Field(..., description="Whether generation was successful")
    page_id: Optional[str] = Field(None, description="Created/updated Confluence page ID")
    page_url: Optional[str] = Field(None, description="URL to the Confluence page")
    events_count: int = Field(0, description="Number of history events found")
    processing_time_sec: float = Field(..., description="Total processing time in seconds")
    events: Optional[List[HistoryEventResponse]] = Field(
        None, 
        description="List of history events (optional, for debugging)"
    )
    error: Optional[str] = Field(None, description="Error message if failed")
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "success": True,
                    "page_id": "439913250",
                    "page_url": "https://kb.vseinstrumenti.ru/pages/viewpage.action?pageId=439913250",
                    "events_count": 6,
                    "processing_time_sec": 4.8,
                    "error": None
                }
            ]
        }
    }


class HealthResponse(BaseModel):
    """Health check response."""
    
    status: str = Field(..., description="Service status")
    version: str = Field(..., description="API version")
    redis_connected: bool = Field(..., description="Redis connection status")
    gitlab_connected: bool = Field(..., description="GitLab connection status")


class CacheWarmRequest(BaseModel):
    """Request body for cache warming."""
    
    gitlab_token: str = Field(
        ...,
        description="GitLab personal access token (REQUIRED)",
        min_length=20
    )
    project_path: str = Field(
        ...,
        description="GitLab project path (e.g., 'logistic/retail/rms/rms-api')",
        example="logistic/retail/rms/rms-api"
    )
    mr_limit: Optional[int] = Field(
        default=None,
        description="Maximum number of MRs to process (None for unlimited)",
        ge=1
    )
    since_date: Optional[date] = Field(
        default=None,
        description="Only load MRs merged after this date (YYYY-MM-DD). Use for incremental updates."
    )
    target_branch: Optional[str] = Field(
        default=None,
        description="Target branch to fetch MRs from. Defaults to 'master' if not provided."
    )
    compute_history: bool = Field(
        default=True,
        description="Pre-compute endpoint history for faster documentation generation. Enabled by default."
    )
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "gitlab_token": "glpat-xxxxxxxxxxxxxxxxxxxx",
                    "project_path": "logistic/retail/rms/rms-api",
                    "mr_limit": 1000,
                    "compute_history": True
                }
            ]
        }
    }


class CacheWarmResponse(BaseModel):
    """Response after cache warming."""
    
    success: bool = Field(..., description="Whether warming was successful")
    specs_loaded: int = Field(0, description="Number of new specs loaded and cached")
    specs_already_cached: int = Field(0, description="Number of specs already in cache")
    specs_failed: int = Field(0, description="Number of specs that failed to load")
    total_mrs: int = Field(0, description="Total MRs processed")
    history_events: int = Field(0, description="Number of history events created")
    details: Optional[str] = Field(None, description="Additional details")
    processing_time_sec: float = Field(..., description="Total processing time in seconds")
    error: Optional[str] = Field(None, description="Error message if failed")


class ProjectCacheStats(BaseModel):
    """Statistics for a single project in cache."""
    
    project_path: str = Field(..., description="GitLab project path")
    spec_count: int = Field(0, description="Number of cached OpenAPI specs")
    history_count: int = Field(0, description="Number of endpoints with pre-computed history")
    last_updated: Optional[str] = Field(None, description="Last cache update timestamp")


class CacheStatusResponse(BaseModel):
    """Cache status response with per-project breakdown."""
    
    connected: bool = Field(..., description="Cache connection status")
    total_specs: int = Field(0, description="Total cached OpenAPI specs across all projects")
    total_history: int = Field(0, description="Total endpoints with pre-computed history")
    cache_dir: Optional[str] = Field(None, description="Cache directory path")
    projects: List[ProjectCacheStats] = Field(default_factory=list, description="Per-project statistics")


class CacheClearRequest(BaseModel):
    """Request body for clearing cache."""
    
    project_path: Optional[str] = Field(
        default=None,
        description="GitLab project path to clear. If not provided, clears ALL projects.",
        example="logistic/retail/rms/rms-api"
    )
    confirm: bool = Field(
        default=False,
        description="Set to true to confirm deletion. Required when clearing all projects."
    )
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "project_path": "logistic/retail/rms/rms-api",
                    "confirm": True
                }
            ]
        }
    }


class CacheClearResponse(BaseModel):
    """Response after clearing cache."""
    
    success: bool = Field(..., description="Whether clearing was successful")
    deleted_count: int = Field(0, description="Number of keys deleted")
    project_path: Optional[str] = Field(None, description="Project that was cleared")
    error: Optional[str] = Field(None, description="Error message if failed")


class SwaggerConfig(BaseModel):
    """Swagger/OpenAPI configuration."""
    
    path: str = Field(
        "api/swagger",
        description="Path to swagger folder in repo",
        example="api/swagger"
    )
    entry_files: List[str] = Field(
        default_factory=lambda: ["openapi.yaml", "openapi.json", "api.yaml", "api.json"],
        description="List of entry files to look for",
        example=["openapi.yaml", "openapi.json"]
    )


class ConfluenceConfig(BaseModel):
    """Confluence settings for project."""
    
    space: str = Field(..., description="Confluence Space Key", example="API")
    parent_page_id: str = Field(..., description="Parent Page ID", example="169804502")
    token: Optional[str] = Field(None, description="Confluence API Token", example="${CONFLUENCE_TOKEN}")


class ProjectConfig(BaseModel):
    """Project configuration for API."""
    
    name: str = Field(..., description="Friendly name of the project", example="RMS API")
    path: str = Field(..., description="GitLab project path", example="logistic/retail/rms/rms-api")
    gitlab_token: Optional[str] = Field(
        "${GITLAB_TOKEN}",
        description="GitLab token (supports ${ENV_VAR})",
        example="${GITLAB_TOKEN}"
    )
    target_branch: str = Field("master", description="Target branch", example="master")
    confluence: Optional[ConfluenceConfig] = Field(None, description="Confluence settings")
    swagger: Optional[SwaggerConfig] = Field(None, description="Swagger settings")


class ProjectListResponse(BaseModel):
    """List of configured projects."""
    projects: List[ProjectConfig]
