"""
Projects configuration management endpoints.
"""
import logging
import os
import yaml
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Body, status

from ..schemas import ProjectConfig, ProjectListResponse, SwaggerConfig, ConfluenceConfig

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/projects",
    tags=["📁 Проекты"]
)

CONFIG_FILE = "projects.yaml"


def _load_config() -> dict:
    """Load raw config from YAML."""
    if not os.path.exists(CONFIG_FILE):
        return {"projects": [], "defaults": {}}
    
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.error(f"Failed to load {CONFIG_FILE}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to load configuration file: {e}"
        )


def _save_config(config: dict) -> None:
    """Save raw config to YAML."""
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            yaml.safe_dump(config, f, sort_keys=False, allow_unicode=True, indent=2)
    except Exception as e:
        logger.error(f"Failed to save {CONFIG_FILE}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save configuration file: {e}"
        )


@router.get(
    "",
    response_model=ProjectListResponse,
    summary="Список проектов"
)
async def list_projects():
    """Получить список всех проектов из конфигурации."""
    data = _load_config()
    defaults = data.get("defaults", {})
    projects_data = data.get("projects", [])
    
    # We need to manually construct ProjectConfig objects to handle defaults merging
    # BUT for editing purposes, users might want to see explicitly correctly config?
    # Schema says "Loaded from projects.yaml with environment variable substitution".
    # But for editing, we probably want raw values (e.g. ${GITLAB_TOKEN}) preserved.
    # The Schema `ProjectConfig` includes defaults logic in `from_dict`.
    # Let's use that but be careful about saving back.
    
    # Actually, for the API "Manage Projects", we usually want to see what is effectively valid.
    # But if we PUT back the substituted value, we lose the env var dynamic nature.
    # Ideally, we should return the raw config values.
    
    # Pydantic model doesn't have the logic to UN-substitute.
    # So we should populate Pydantic model from raw dicts, relying on its default values where fields are missing.
    
    results = []
    for p_data in projects_data:
        # Merge minimal defaults for structure, but keep raw strings
        swagger = p_data.get("swagger")
        if swagger:
            swagger_config = SwaggerConfig(
                path=swagger.get("path", "api/swagger"),
                entry_files=swagger.get("entry_files", [])
            )
        else:
            swagger_config = None # Use None to indicate using defaults or nothing explicit
            
        confluence = p_data.get("confluence")
        confluence_config = None
        if confluence:
            confluence_config = ConfluenceConfig(
                space=confluence.get("space", ""),
                parent_page_id=str(confluence.get("parent_page_id", "")),
                token=confluence.get("token")
            )
        
        project = ProjectConfig(
            name=p_data.get("name", p_data.get("path", "Unknown")),
            path=p_data.get("path"),
            gitlab_token=p_data.get("gitlab_token"), # Keep ${VAR} if present
            target_branch=p_data.get("target_branch", "master"),
            confluence=confluence_config,
            swagger=swagger_config
        )
        results.append(project)
        
    return ProjectListResponse(projects=results)


@router.post(
    "",
    response_model=ProjectConfig,
    summary="Добавить проект"
)
async def add_project(project: ProjectConfig):
    """Добавить новый проект в конфигурацию."""
    data = _load_config()
    
    # Check duplicate by name (name is the unique identifier)
    for p in data.get("projects", []):
        if p.get("name") == project.name:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Project with name '{project.name}' already exists"
            )
    
    # Convert to dict for YAML
    new_project = project.model_dump(exclude_none=True, mode='json')
    
    if "projects" not in data:
        data["projects"] = []
    
    data["projects"].append(new_project)
    _save_config(data)
    
    return project


@router.put(
    "/{project_name}",
    response_model=ProjectConfig,
    summary="Обновить проект"
)
async def update_project(
    project_name: str,
    project: ProjectConfig
):
    """Обновить конфигурацию проекта по имени."""
    data = _load_config()

    found_idx = -1
    for i, p in enumerate(data.get("projects", [])):
        if p.get("name") == project_name:
            found_idx = i
            break

    if found_idx == -1:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project '{project_name}' not found"
        )

    # If name changes, ensure new name doesn't conflict
    if project.name != project_name:
        for p in data.get("projects", []):
            if p.get("name") == project.name:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Cannot rename to '{project.name}': project with this name already exists"
                )

    new_data = project.model_dump(exclude_none=True, mode='json')
    data["projects"][found_idx] = new_data
    _save_config(data)

    return project

@router.delete(
    "/{project_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить проект"
)
async def delete_project(project_name: str):
    """Удалить проект из конфигурации по имени."""
    data = _load_config()

    initial_len = len(data.get("projects", []))
    data["projects"] = [
        p for p in data.get("projects", []) if p.get("name") != project_name
    ]

    if len(data["projects"]) == initial_len:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project '{project_name}' not found"
        )

    _save_config(data)
    return None
