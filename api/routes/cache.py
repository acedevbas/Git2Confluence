"""
Cache management endpoints.
"""
import time
import os
import shutil
import logging
import warnings
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Semaphore

from fastapi import APIRouter, HTTPException, status

# Suppress SSL warnings
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore", message="Unverified HTTPS request")

from ..deps import ApiKeyDep
from ..schemas import (
    CacheWarmRequest,
    CacheWarmResponse,
    CacheStatusResponse,
    CacheClearRequest,
    CacheClearResponse
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/cache",
    tags=["💾 Кеш"]
)


@router.get(
    "/status",
    response_model=CacheStatusResponse,
    summary="Статус кеша",
    description="Возвращает статистику кеша с разбивкой по проектам."
)
async def get_cache_status() -> CacheStatusResponse:
    """Получить текущую статистику кеша по проектам."""
    try:
        from src.cache.disk_cache import DiskCacheManager
        from src.cache.endpoint_history_cache import EndpointHistoryCache
        from ..schemas import ProjectCacheStats
        
        cache = DiskCacheManager()
        
        stats = cache.get_stats()
        project_stats_raw = cache.get_project_stats()
        
        # Get history counts per project
        history_cache = EndpointHistoryCache(cache)
        
        projects = []
        total_specs = 0
        total_history = 0
        
        for p in project_stats_raw.get("projects", []):
            project_path = p.get("project_path", "")
            spec_count = p.get("spec_count", 0)
            
            # Count history entries for this project
            history_count = len(history_cache.list_endpoints(project_path))
            
            projects.append(ProjectCacheStats(
                project_path=project_path,
                spec_count=spec_count,
                history_count=history_count,
                last_updated=p.get("last_updated")
            ))
            
            total_specs += spec_count
            total_history += history_count
        
        cache.close()
        
        return CacheStatusResponse(
            connected=True,
            total_specs=total_specs,
            total_history=total_history,
            cache_dir=stats.get("cache_dir"),
            projects=projects
        )
    except Exception as e:
        logger.exception(f"Failed to get cache status: {e}")
        return CacheStatusResponse(
            connected=False,
            total_specs=0,
            total_history=0,
            cache_dir=None,
            projects=[]
        )


@router.post(
    "/warm",
    response_model=CacheWarmResponse,
    summary="Прогрев кеша",
    description="""
Загружает и кеширует OpenAPI спецификации из Merge Request'ов GitLab.

**Особенности:**
- Загружает только папку swagger (~1MB) вместо полного архива (~100MB)
- Предварительно вычисляет историю изменений для быстрой генерации документации
- Использует fallback на архив для старых коммитов с другой структурой

После прогрева генерация документации через `/generate` будет в ~10 раз быстрее.
    """
)
async def warm_cache(
    request: CacheWarmRequest,
    api_key: ApiKeyDep
) -> CacheWarmResponse:
    """Оптимизированный прогрев кеша с точной генерацией истории."""
    try:
        from config import settings
        from src.processing.batch_processor import BatchProcessor, ProjectConfig
        
        # Resolve project name from config or request
        from api.routes.projects import _load_config as _load_projects_config
        project_name = request.project_name or ""
        if not project_name:
            for p in _load_projects_config().get("projects", []):
                if p.get("path") == request.project_path:
                    project_name = p.get("name", "")
                    break
        if not project_name:
            project_name = request.project_path.split('/')[-1]

        # Create single-project configuration
        proj_config = ProjectConfig(
            name=project_name,
            path=request.project_path,
            gitlab_url=settings.gitlab_url,
            gitlab_token=request.gitlab_token,
            gitlab_ssl_verify=settings.gitlab_ssl_verify,
            target_branch=request.target_branch or "master",
        )

        # Initialize processor
        processor = BatchProcessor(projects=[proj_config])

        result = await processor.warm_cache_with_history(
            project_id=proj_config.name,
            incremental=bool(request.since_date),
            mr_limit=request.mr_limit
        )
        
        return CacheWarmResponse(
            success=True,
            specs_loaded=result.specs_cached,
            specs_already_cached=result.specs_skipped,
            specs_failed=result.specs_failed,
            total_mrs=result.mrs_found,
            history_events=result.history_events,
            processing_time_sec=result.duration,
            details=f"Processed {result.mrs_found} MRs, {result.endpoints_processed} endpoints."
        )

    except Exception as e:
        logger.exception(f"Cache warming failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )



def _process_archive_sync(archive_bytes: bytes, spec_loader) -> Optional[dict]:
    """Process archive synchronously (for thread pool)."""
    import tempfile
    import zipfile
    
    with tempfile.TemporaryDirectory() as tmpdir:
        archive_path = os.path.join(tmpdir, "archive.zip")
        with open(archive_path, 'wb') as f:
            f.write(archive_bytes)
        
        try:
            with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                zip_ref.extractall(tmpdir)
        except zipfile.BadZipFile:
            return None
        
        return spec_loader.load_spec_from_snapshot(tmpdir)


def _extract_task_id_from_mr(mr: dict) -> str:
    """Extract JIRA task ID from MR title or branch."""
    import re
    
    title = mr.get('title', '')
    branch = mr.get('source_branch', '')
    
    if title.lower().startswith('revert'):
        return 'REVERT'
    if branch.lower().startswith('revert'):
        return 'REVERT'
    
    for text in [title, branch]:
        if not text:
            continue
        match = re.search(r'([A-Za-z]+-\d+)', text, re.IGNORECASE)
        if match:
            return match.group(1).upper()
    
    return 'NO-TASK'


# =============================================================================
# CACHE MANAGEMENT ENDPOINTS
# =============================================================================

@router.delete(
    "/clear",
    response_model=CacheClearResponse,
    summary="Очистка кеша",
    description="""
Очищает закешированные спецификации и схемы.

- Укажите `project_path` для очистки кеша конкретного проекта
- Не указывайте `project_path` и установите `confirm=true` для очистки ВСЕГО кеша
    """
)
async def clear_cache(
    request: CacheClearRequest,
    api_key: ApiKeyDep
) -> CacheClearResponse:
    """Очистка кеша для проекта или всех проектов."""
    try:
        from src.cache.disk_cache import DiskCacheManager
        from src.cache.endpoint_history_cache import EndpointHistoryCache
        
        cache = DiskCacheManager()
        
        if request.project_path:
            # Resolve project name (cache is keyed by name)
            from api.routes.projects import _load_config as _load_projects_config
            cache_key = request.project_path
            for p in _load_projects_config().get("projects", []):
                if p.get("path") == request.project_path or p.get("name") == request.project_path:
                    cache_key = p.get("name", request.project_path)
                    break

            # Clear specific project
            deleted = cache.flush_project(cache_key)

            # Also clear history
            history_cache = EndpointHistoryCache(cache)
            history_deleted = history_cache.clear_project(cache_key)

            cache.close()

            logger.info(f"[CACHE] Cleared {deleted} specs + {history_deleted} history for {cache_key}")
            
            return CacheClearResponse(
                success=True,
                deleted_count=deleted + history_deleted,
                project_path=request.project_path,
                error=None
            )
        else:
            # Clear all - requires confirmation
            if not request.confirm:
                cache.close()
                return CacheClearResponse(
                    success=False,
                    deleted_count=0,
                    project_path=None,
                    error="Set confirm=true to clear ALL cache"
                )
            
            # Get count before clearing
            stats = cache.get_stats()
            total_keys = stats.get("total_keys", 0)
            
            cache.flush_all()
            cache.close()
            
            logger.info(f"[CACHE] Cleared ALL cache ({total_keys} keys)")
            
            return CacheClearResponse(
                success=True,
                deleted_count=total_keys,
                project_path=None,
                error=None
            )
            
    except Exception as e:
        logger.exception(f"[CACHE] Clear failed: {e}")
        return CacheClearResponse(
            success=False,
            deleted_count=0,
            project_path=request.project_path,
            error=str(e)
        )


