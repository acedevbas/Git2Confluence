"""
Regression Filter - фильтрация временных регрессий из истории изменений API.

Проблема: при параллельной разработке может произойти "ложное удаление" - 
MR, созданный до мержа другого MR, перезаписывает добавленные поля.
Это выглядит как удаление, но на самом деле - косяк процесса.

Решение: если поле удалено и восстановлено в течение grace_period (по умолчанию 7 дней),
считаем это временной регрессией и фильтруем из основного отчёта.

Паттерн: Grace Period / Soft Delete, аналогично Kubernetes PodDisruptionBudget.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional


class DeletionStatus(Enum):
    """Статус удаления поля/эндпоинта."""
    CONFIRMED = "confirmed"           # Прошёл grace period, не восстановлено
    TEMPORARY = "temporary_regression" # Восстановлено в grace period - не настоящее удаление
    PENDING = "pending"               # Ещё в grace period, ждём подтверждения


@dataclass
class FieldChange:
    """Событие изменения поля."""
    field_path: str
    change_type: str  # "added", "removed", "modified"
    mr_iid: int
    merged_at: datetime
    mr_title: str = ""
    task_id: str = ""


@dataclass
class TemporaryRegression:
    """
    Временная регрессия - удаление которое было быстро отменено.
    
    Это НЕ настоящее удаление, а артефакт параллельной разработки.
    """
    field_path: str
    deleted_in_mr: int
    deleted_at: datetime
    restored_in_mr: int
    restored_at: datetime
    
    @property
    def duration(self) -> timedelta:
        """Время пока поле отсутствовало в master."""
        return self.restored_at - self.deleted_at
    
    @property
    def duration_days(self) -> float:
        """Время отсутствия в днях."""
        return self.duration.total_seconds() / 86400


@dataclass
class PendingDeletion:
    """
    Удаление в процессе подтверждения.
    
    Ждём grace_period чтобы убедиться что это реальное удаление.
    """
    field_path: str
    deleted_in_mr: int
    deleted_at: datetime
    grace_period_end: datetime
    
    @property
    def is_expired(self) -> bool:
        """Прошёл ли grace period."""
        return datetime.now() > self.grace_period_end


@dataclass
class FilterResult:
    """Результат фильтрации событий."""
    # События которые остаются в отчёте
    confirmed_events: list[FieldChange] = field(default_factory=list)
    # Временные регрессии (удалены из отчёта)
    temporary_regressions: list[TemporaryRegression] = field(default_factory=list)
    # Удаления ещё в процессе подтверждения
    pending_deletions: list[PendingDeletion] = field(default_factory=list)
    
    @property
    def filtered_count(self) -> int:
        """Количество отфильтрованных событий."""
        # Каждая regression = 2 события (removed + added)
        return len(self.temporary_regressions) * 2


class RegressionFilter:
    """
    Фильтрует временные регрессии из истории изменений.
    
    Логика:
    1. Собираем все события изменений отсортированные по времени
    2. Для каждого REMOVED события ищем ADDED в grace_period
    3. Если нашли - помечаем оба как temporary_regression
    4. Если не нашли - подтверждаем удаление
    
    Пример:
        MR !688 (2025-10-30): Added electronicReceipt
        MR !696 (2025-11-06): Removed electronicReceipt  <- временная регрессия
        MR !725 (2025-11-11): Added electronicReceipt    <- восстановление
        
        Интервал: 5 дней < 7 дней (grace_period)
        Результат: события !696 и !725 фильтруются из отчёта
    
    Args:
        grace_period_days: Время ожидания восстановления (по умолчанию 7 дней)
        min_regression_duration_hours: Минимальная длительность для учёта (по умолчанию 1 час)
    """
    
    def __init__(
        self, 
        grace_period_days: int = 7,
        min_regression_duration_hours: float = 1.0
    ):
        self.grace_period = timedelta(days=grace_period_days)
        self.min_duration = timedelta(hours=min_regression_duration_hours)
    
    def filter_events(
        self, 
        events: list[FieldChange],
        reference_date: Optional[datetime] = None
    ) -> FilterResult:
        """
        Фильтрует события, выделяя временные регрессии.
        
        Args:
            events: Список событий отсортированных по времени
            reference_date: Дата для определения pending статуса (по умолчанию now())
            
        Returns:
            FilterResult с разделением на confirmed, temporary и pending
        """
        if reference_date is None:
            reference_date = datetime.now(timezone.utc)
        
        result = FilterResult()
        
        # Группируем события по полю
        by_field: dict[str, list[FieldChange]] = {}
        for event in events:
            by_field.setdefault(event.field_path, []).append(event)
        
        # Анализируем каждое поле
        for field_path, field_events in by_field.items():
            # Сортируем по времени
            sorted_events = sorted(field_events, key=lambda e: e.merged_at)
            
            # Ищем пары removed → added в grace period
            removed_events = [e for e in sorted_events if e.change_type == "removed"]
            
            for removed in removed_events:
                # Ищем восстановление в grace period
                restoration = self._find_restoration(
                    removed, 
                    sorted_events, 
                    self.grace_period
                )
                
                if restoration:
                    # Это временная регрессия
                    regression = TemporaryRegression(
                        field_path=field_path,
                        deleted_in_mr=removed.mr_iid,
                        deleted_at=removed.merged_at,
                        restored_in_mr=restoration.mr_iid,
                        restored_at=restoration.merged_at
                    )
                    
                    # Проверяем минимальную длительность
                    if regression.duration >= self.min_duration:
                        result.temporary_regressions.append(regression)
                else:
                    # Проверяем: ещё в grace period или уже подтверждено?
                    grace_end = removed.merged_at + self.grace_period
                    
                    if reference_date < grace_end:
                        # Ещё ждём
                        result.pending_deletions.append(PendingDeletion(
                            field_path=field_path,
                            deleted_in_mr=removed.mr_iid,
                            deleted_at=removed.merged_at,
                            grace_period_end=grace_end
                        ))
                    else:
                        # Подтверждённое удаление - добавляем в confirmed
                        result.confirmed_events.append(removed)
            
            # Добавляем added события которые не являются восстановлениями регрессий
            regression_restorations = {
                r.restored_in_mr for r in result.temporary_regressions 
                if r.field_path == field_path
            }
            regression_deletions = {
                r.deleted_in_mr for r in result.temporary_regressions
                if r.field_path == field_path
            }
            
            for event in sorted_events:
                if event.change_type == "added":
                    if event.mr_iid not in regression_restorations:
                        result.confirmed_events.append(event)
                elif event.change_type == "modified":
                    # Модификации не фильтруем
                    result.confirmed_events.append(event)
        
        return result
    
    def _find_restoration(
        self, 
        removed: FieldChange, 
        events: list[FieldChange],
        grace_period: timedelta
    ) -> Optional[FieldChange]:
        """
        Ищет событие восстановления поля после удаления.
        
        Returns:
            FieldChange если найдено восстановление в grace period, иначе None
        """
        grace_end = removed.merged_at + grace_period
        
        for event in events:
            # Только после удаления
            if event.merged_at <= removed.merged_at:
                continue
            # В пределах grace period
            if event.merged_at > grace_end:
                break
            # Это восстановление
            if event.change_type == "added":
                return event
        
        return None


def format_regression_report(result: FilterResult) -> str:
    """Форматирует отчёт о временных регрессиях."""
    lines = []
    
    if result.temporary_regressions:
        lines.append("## ⏳ Temporary Regressions (Filtered)")
        lines.append("")
        lines.append("These deletions were reverted within the grace period and are likely")
        lines.append("parallel development issues, not intentional changes:")
        lines.append("")
        lines.append("| Field | Deleted In | Restored In | Duration |")
        lines.append("|-------|------------|-------------|----------|")
        
        for r in result.temporary_regressions:
            duration = f"{r.duration_days:.1f} days"
            lines.append(
                f"| `{r.field_path.split('.')[-1]}` | "
                f"MR !{r.deleted_in_mr} | "
                f"MR !{r.restored_in_mr} | "
                f"{duration} |"
            )
        lines.append("")
    
    if result.pending_deletions:
        lines.append("## ⏸️ Pending Deletions")
        lines.append("")
        lines.append("These deletions are still within the grace period:")
        lines.append("")
        
        for p in result.pending_deletions:
            days_left = (p.grace_period_end - datetime.now()).days
            lines.append(
                f"- `{p.field_path}` deleted in MR !{p.deleted_in_mr} "
                f"({days_left} days until confirmed)"
            )
        lines.append("")
    
    return '\n'.join(lines)
