"""
API эндпоинты для просмотра логов.

Позволяет получать последние логи из кольцевого буфера без нагрузки на сервер.
"""
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

router = APIRouter(prefix="/logs", tags=["🏠 Система"])


class LogEntryResponse(BaseModel):
    """Запись лога."""
    timestamp: str = Field(..., description="Время записи (ISO 8601)")
    level: str = Field(..., description="Уровень: DEBUG, INFO, WARNING, ERROR")
    logger_name: str = Field(..., description="Имя логгера")
    message: str = Field(..., description="Сообщение")


class LogsResponse(BaseModel):
    """Ответ с логами."""
    total: int = Field(..., description="Всего записей в буфере")
    returned: int = Field(..., description="Возвращено записей")
    logs: List[LogEntryResponse] = Field(default_factory=list, description="Записи логов")


class LogStatsResponse(BaseModel):
    """Статистика логов."""
    total: int = Field(..., description="Всего записей в буфере")
    capacity: int = Field(..., description="Максимальная ёмкость буфера")
    by_level: Dict[str, int] = Field(default_factory=dict, description="Количество по уровням")


@router.get(
    "",
    response_model=LogsResponse,
    summary="Получить последние логи",
    description="""
Возвращает последние записи из кольцевого буфера логов.

**Особенности:**
- Буфер хранит максимум 1000 записей — не перегружает память
- Фильтрация по уровню: только INFO+, WARNING+, или ERROR+
- Фильтрация по имени логгера
    """
)
async def get_logs(
    count: int = Query(100, ge=1, le=1000, description="Количество записей"),
    level: Optional[str] = Query(None, description="Минимальный уровень: DEBUG, INFO, WARNING, ERROR"),
    logger: Optional[str] = Query(None, description="Фильтр по имени логгера (частичное совпадение)")
):
    """Получить последние логи из кольцевого буфера."""
    from src.logging_config import get_ring_handler
    
    handler = get_ring_handler()
    logs = handler.get_logs(count=count, level=level, logger_filter=logger)
    stats = handler.stats()
    
    return LogsResponse(
        total=stats["total"],
        returned=len(logs),
        logs=[LogEntryResponse(**log) for log in logs]
    )


@router.get(
    "/stats",
    response_model=LogStatsResponse,
    summary="Статистика логов",
    description="Возвращает статистику по буферу логов без самих записей."
)
async def get_log_stats():
    """Получить статистику логов."""
    from src.logging_config import get_ring_handler
    
    handler = get_ring_handler()
    stats = handler.stats()
    
    return LogStatsResponse(
        total=stats["total"],
        capacity=stats["capacity"],
        by_level=stats["by_level"]
    )


@router.delete(
    "",
    summary="Очистить буфер логов",
    description="Очищает кольцевой буфер логов."
)
async def clear_logs():
    """Очистить буфер логов."""
    from src.logging_config import get_ring_handler
    
    handler = get_ring_handler()
    handler.clear()
    
    return {"status": "cleared", "message": "Буфер логов очищен"}
