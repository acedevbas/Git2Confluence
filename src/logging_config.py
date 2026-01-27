"""
Централизованная система логирования с кольцевым буфером.

Особенности:
- Ограниченный размер буфера (не перегружает память)
- API эндпоинт для просмотра последних логов
- Уровни: DEBUG < INFO < WARNING < ERROR
- DEBUG логи отключены по умолчанию в продакшене
"""
import logging
import os
from collections import deque
from datetime import datetime
from threading import Lock
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field, asdict


@dataclass
class LogEntry:
    """Запись лога."""
    timestamp: str
    level: str
    logger_name: str
    message: str
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RingBufferHandler(logging.Handler):
    """
    Обработчик логов с кольцевым буфером.
    
    Хранит только последние N записей, не переполняя память.
    """
    
    def __init__(self, capacity: int = 1000):
        """
        Args:
            capacity: Максимальное количество записей в буфере
        """
        super().__init__()
        self.capacity = capacity
        self.buffer: deque = deque(maxlen=capacity)
        self._lock = Lock()
    
    def emit(self, record: logging.LogRecord):
        """Добавить запись в буфер."""
        try:
            entry = LogEntry(
                timestamp=datetime.fromtimestamp(record.created).isoformat(),
                level=record.levelname,
                logger_name=record.name,
                message=self.format(record)
            )
            
            with self._lock:
                self.buffer.append(entry)
        except Exception:
            self.handleError(record)
    
    def get_logs(
        self, 
        count: int = 100, 
        level: Optional[str] = None,
        logger_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Получить последние логи из буфера.
        
        Args:
            count: Количество записей (максимум)
            level: Фильтр по уровню (INFO, WARNING, ERROR)
            logger_filter: Фильтр по имени логгера (частичное совпадение)
        
        Returns:
            Список записей логов
        """
        with self._lock:
            entries = list(self.buffer)
        
        # Фильтрация по уровню
        if level:
            level_priority = {
                'DEBUG': 0, 'INFO': 1, 'WARNING': 2, 'ERROR': 3, 'CRITICAL': 4
            }
            min_priority = level_priority.get(level.upper(), 0)
            entries = [
                e for e in entries 
                if level_priority.get(e.level, 0) >= min_priority
            ]
        
        # Фильтрация по имени логгера
        if logger_filter:
            entries = [
                e for e in entries 
                if logger_filter.lower() in e.logger_name.lower()
            ]
        
        # Последние N записей
        return [e.to_dict() for e in entries[-count:]]
    
    def clear(self):
        """Очистить буфер."""
        with self._lock:
            self.buffer.clear()
    
    def stats(self) -> Dict[str, Any]:
        """Статистика буфера."""
        with self._lock:
            entries = list(self.buffer)
        
        level_counts = {}
        for e in entries:
            level_counts[e.level] = level_counts.get(e.level, 0) + 1
        
        return {
            "total": len(entries),
            "capacity": self.capacity,
            "by_level": level_counts
        }


# Глобальный обработчик
_ring_handler: Optional[RingBufferHandler] = None


def get_ring_handler() -> RingBufferHandler:
    """Получить глобальный кольцевой обработчик."""
    global _ring_handler
    if _ring_handler is None:
        _ring_handler = RingBufferHandler(capacity=1000)
        _ring_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
    return _ring_handler


def setup_logging(
    level: str = "INFO",
    debug_mode: bool = False,
    error_log_file: str = "logs/errors.log"
) -> None:
    """
    Настроить централизованное логирование.
    
    Args:
        level: Уровень логирования (DEBUG, INFO, WARNING, ERROR)
        debug_mode: Включить DEBUG логи (по умолчанию off)
        error_log_file: Путь к файлу для сохранения ошибок
    """
    from logging.handlers import RotatingFileHandler
    
    # Определить уровень из окружения или параметра
    env_level = os.environ.get("LOG_LEVEL", level).upper()
    if debug_mode or os.environ.get("DEBUG", "").lower() in ("1", "true", "yes"):
        env_level = "DEBUG"
    
    log_level = getattr(logging, env_level, logging.INFO)
    
    # Базовая настройка
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        force=True
    )
    
    root_logger = logging.getLogger()
    
    # Добавить кольцевой обработчик
    ring_handler = get_ring_handler()
    ring_handler.setLevel(log_level)
    
    # Убедиться что не добавляем дважды
    for h in list(root_logger.handlers):
        if isinstance(h, (RingBufferHandler, RotatingFileHandler)):
            root_logger.removeHandler(h)
    
    root_logger.addHandler(ring_handler)
    
    # Добавить файловый обработчик для ERROR логов
    try:
        log_dir = os.path.dirname(error_log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
        
        file_handler = RotatingFileHandler(
            error_log_file,
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=3,
            encoding='utf-8'
        )
        file_handler.setLevel(logging.ERROR)
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s\n%(pathname)s:%(lineno)d'
        ))
        root_logger.addHandler(file_handler)
    except Exception as e:
        logging.warning(f"Failed to setup error log file: {e}")
    
    # Уменьшить verbosity сторонних библиотек
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("gitlab").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.INFO)


def get_logger(name: str) -> logging.Logger:
    """
    Получить логгер с настроенным именем.
    
    Args:
        name: Имя логгера (обычно __name__)
    
    Returns:
        Настроенный логгер
    """
    return logging.getLogger(name)
