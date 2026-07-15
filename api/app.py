"""
FastAPI application for OpenAPI History Tracker.

Provides REST API endpoints for generating API documentation
from GitLab MR history and publishing to Confluence.
"""
import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import docs, cache, projects, scheduler, logs
from .schemas import HealthResponse
from src.scheduler.scheduler import get_scheduler, init_scheduler
from src.logging_config import setup_logging, get_logger

# Configure centralized logging with ring buffer
setup_logging(level="INFO", debug_mode=False)
logger = get_logger(__name__)

# Application version — baked into the image at build time via the APP_VERSION
# build-arg (see Dockerfile). Falls back to "dev" for local runs. This is what
# /docs, /, /health and /version report, so the running image tag is always
# visible without exec-ing into the container.
APP_VERSION = os.getenv("APP_VERSION", "dev")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown."""
    # Startup
    logger.info("🚀 OpenAPI History Tracker API starting...")
    
    # Initialize and start scheduler (runs at 1:00 AM Moscow time)
    sched = init_scheduler(cron_hour=1, cron_minute=0, enabled=True)
    await sched.start()
    
    yield
    
    # Shutdown
    await sched.stop()
    logger.info("👋 OpenAPI History Tracker API shutting down...")


# Create FastAPI app
app = FastAPI(
    title="OpenAPI History Tracker",
    description="""
## 📚 Трекер истории OpenAPI

REST API для автоматической генерации документации API из истории Merge Request'ов GitLab 
и публикации в Confluence.

---

### 🚀 Возможности

| Функция | Описание |
|---------|----------|
| **Анализ MR** | Сканирование merge request'ов GitLab для отслеживания изменений API |
| **Парсинг OpenAPI** | Извлечение OpenAPI спецификаций с полным разрешением `$ref` |
| **Детекция изменений** | Сравнение схем между версиями для выявления модификаций |
| **Публикация в Confluence** | Генерация и публикация страниц документации |
| **Управление кешем** | Прогрев и мониторинг кеша спецификаций |
| **Планировщик** | Автоматический запуск задач по расписанию (ежедневно в 01:00) |

---

### 🔐 Аутентификация

Все эндпоинты требуют заголовок `X-API-Key` с валидным ключом.

---

### 📖 Быстрый старт

```bash
# Генерация документации для эндпоинта
curl -X POST "http://localhost:8000/api/v1/documentation/generate" \\
  -H "X-API-Key: your-api-key" \\
  -H "Content-Type: application/json" \\
  -d '{
    "target_endpoint": "POST /orders/products/return",
    "confluence_parent_page_id": "169804502"
  }'
```
    """,
    version=APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
    openapi_tags=[
        {"name": "📄 Документация", "description": "Генерация и публикация API документации"},
        {"name": "💾 Кеш", "description": "Управление кешем OpenAPI спецификаций"},
        {"name": "📁 Проекты", "description": "Информация о проектах и их эндпоинтах"},
        {"name": "⏰ Планировщик", "description": "Управление автоматическими задачами"},
        {"name": "🏠 Система", "description": "Системные эндпоинты"},
    ]
)

# CORS middleware (configure as needed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(docs.router, prefix="/api/v1")
app.include_router(cache.router, prefix="/api/v1")
app.include_router(projects.router, prefix="/api/v1")
app.include_router(scheduler.router, prefix="/api/v1")
app.include_router(logs.router, prefix="/api/v1")


@app.get("/", tags=["🏠 Система"], summary="Информация о сервисе")
async def root():
    """
    Корневой эндпоинт с информацией о сервисе.
    
    Возвращает базовую информацию: название, версию и ссылки на документацию.
    """
    return {
        "service": "OpenAPI History Tracker",
        "version": APP_VERSION,
        "docs": "/docs",
        "health": "/health"
    }


@app.get("/version", tags=["🏠 Система"], summary="Версия образа")
async def get_version():
    """
    Текущая версия развёрнутого образа.

    Значение вшивается в образ при сборке (build-arg `APP_VERSION`), поэтому
    отражает реально запущенный тег — удобно для проверки деплоя:
    `curl https://<host>/version`.
    """
    return {"version": APP_VERSION}


@app.get("/health", response_model=HealthResponse, tags=["🏠 Система"], summary="Проверка здоровья")
async def health_check():
    """
    Проверка состояния сервиса.
    
    Проверяет подключение к:
    - **Кеш** — дисковый кеш спецификаций
    - **GitLab** — доступ к репозиториям
    
    **Статусы:**
    - `healthy` — все системы работают
    - `degraded` — есть проблемы с компонентами
    """
    cache_ok = False
    gitlab_ok = False
    
    try:
        from src.cache.disk_cache import DiskCacheManager
        cache = DiskCacheManager()
        cache.close()
        cache_ok = True
    except Exception as e:
        logger.warning(f"Cache health check failed: {e}")
    
    try:
        import gitlab
        from config import settings
        gl = gitlab.Gitlab(
            url=settings.gitlab_url,
            private_token=settings.gitlab_token,
            ssl_verify=settings.gitlab_ssl_verify
        )
        gl.auth()
        gitlab_ok = True
    except Exception as e:
        logger.warning(f"GitLab health check failed: {e}")
    
    return HealthResponse(
        status="healthy" if cache_ok and gitlab_ok else "degraded",
        version=APP_VERSION,
        redis_connected=cache_ok,
        gitlab_connected=gitlab_ok
    )
