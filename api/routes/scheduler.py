"""
API эндпоинты для управления планировщиком.

Позволяет проверять статус, запускать/останавливать планировщик и вручную запускать задачи.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

router = APIRouter(prefix="/scheduler", tags=["⏰ Планировщик"])


class JobInfo(BaseModel):
    """Информация о запланированной задаче."""
    id: str = Field(..., description="Уникальный идентификатор задачи")
    name: str = Field(..., description="Название задачи")
    next_run: Optional[str] = Field(None, description="Время следующего запуска (ISO 8601)")
    trigger: str = Field(..., description="Тип триггера (cron, interval)")


class SchedulerStatus(BaseModel):
    """Статус планировщика."""
    enabled: bool = Field(..., description="Включён ли планировщик")
    running: bool = Field(..., description="Запущен ли планировщик")
    last_run: Optional[str] = Field(None, description="Время последнего запуска")
    last_status: str = Field(..., description="Статус последнего запуска: idle, running, completed, failed")
    current_project: Optional[str] = Field(None, description="Текущий обрабатываемый проект")
    scheduled_time: str = Field(..., description="Запланированное время ежедневного запуска")
    jobs: List[JobInfo] = Field(default_factory=list, description="Список запланированных задач")


class TriggerResponse(BaseModel):
    """Ответ на ручной запуск."""
    triggered: bool = Field(..., description="Была ли задача запущена")
    message: str = Field(..., description="Сообщение о результате")
    timestamp: str = Field(..., description="Время запуска")


class ActionResponse(BaseModel):
    """Ответ на действие с планировщиком."""
    status: str = Field(..., description="Результат: started, stopped, already_running, already_stopped")
    message: str = Field(..., description="Описание результата")


@router.get(
    "/status", 
    response_model=SchedulerStatus,
    summary="Получить статус планировщика",
    description="Возвращает текущий статус планировщика, время последнего и следующего запуска."
)
async def get_scheduler_status():
    """
    Получить текущий статус планировщика.
    
    **Возвращает:**
    - Включён/запущен ли планировщик
    - Время последнего запуска и его статус
    - Текущий обрабатываемый проект (если идёт обработка)
    - Список запланированных задач с временем следующего запуска
    """
    from src.scheduler.scheduler import get_scheduler
    
    scheduler = get_scheduler()
    status = scheduler.get_status()
    
    return SchedulerStatus(
        enabled=status["enabled"],
        running=status["running"],
        last_run=status["last_run"],
        last_status=status["last_status"],
        current_project=status["current_project"],
        scheduled_time=status["scheduled_time"],
        jobs=[JobInfo(**job) for job in status["jobs"]]
    )


@router.post(
    "/trigger", 
    response_model=TriggerResponse,
    summary="Запустить задачу вручную",
    description="Немедленно запускает прогрев кеша и публикацию документации для всех проектов."
)
async def trigger_scheduler():
    """
    Ручной запуск ежедневной задачи.
    
    Запускает в фоновом режиме:
    1. **Прогрев кеша** — для каждого проекта из `projects.yaml`
    2. **Публикацию документации** — сразу после прогрева каждого проекта
    
    > ⚠️ Планировщик должен быть запущен для работы этого эндпоинта.
    """
    from src.scheduler.scheduler import get_scheduler
    
    scheduler = get_scheduler()
    
    if not scheduler.is_running:
        raise HTTPException(
            status_code=400,
            detail="Планировщик не запущен. Сначала запустите его через /scheduler/start"
        )
    
    result = await scheduler.trigger_now()
    
    return TriggerResponse(
        triggered=result["triggered"],
        message=result["message"],
        timestamp=result["timestamp"]
    )


@router.post(
    "/start",
    response_model=ActionResponse,
    summary="Запустить планировщик",
    description="Запускает планировщик, если он ещё не запущен."
)
async def start_scheduler():
    """
    Запустить планировщик.
    
    Если планировщик уже запущен, возвращает соответствующее сообщение.
    """
    from src.scheduler.scheduler import get_scheduler
    
    scheduler = get_scheduler()
    
    if scheduler.is_running:
        return ActionResponse(
            status="already_running", 
            message="Планировщик уже запущен"
        )
    
    await scheduler.start()
    
    return ActionResponse(
        status="started", 
        message="Планировщик успешно запущен"
    )


@router.post(
    "/stop",
    response_model=ActionResponse,
    summary="Остановить планировщик",
    description="Останавливает планировщик."
)
async def stop_scheduler():
    """
    Остановить планировщик.
    
    Если планировщик не запущен, возвращает соответствующее сообщение.
    """
    from src.scheduler.scheduler import get_scheduler
    
    scheduler = get_scheduler()
    
    if not scheduler.is_running:
        return ActionResponse(
            status="already_stopped", 
            message="Планировщик не запущен"
        )
    
    await scheduler.stop()
    
    return ActionResponse(
        status="stopped", 
        message="Планировщик остановлен"
    )
