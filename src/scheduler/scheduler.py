"""
APScheduler-based scheduler for daily cache warming and documentation publishing.

Integrates with FastAPI lifespan for automatic startup/shutdown.
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.job import Job

logger = logging.getLogger(__name__)


class SchedulerService:
    """
    Service for managing scheduled tasks.
    
    Tasks:
    - Daily cache warming at configured hour (default 1:00 AM)
    - Immediate doc publishing after each project's cache is warmed
    """
    
    def __init__(
        self,
        cron_hour: int = 1,
        cron_minute: int = 0,
        enabled: bool = True
    ):
        """
        Initialize scheduler.
        
        Args:
            cron_hour: Hour for daily job (0-23), default 1 (1:00 AM)
            cron_minute: Minute for daily job (0-59), default 0
            enabled: Whether scheduler is enabled
        """
        self.cron_hour = cron_hour
        self.cron_minute = cron_minute
        self.enabled = enabled
        self._scheduler: Optional[AsyncIOScheduler] = None
        self._running = False
        self._last_run: Optional[datetime] = None
        self._last_status: str = "idle"
        self._current_project: Optional[str] = None
        
    @property
    def is_running(self) -> bool:
        return self._running and self._scheduler is not None
    
    def get_status(self) -> Dict[str, Any]:
        """Get current scheduler status."""
        jobs = []
        if self._scheduler:
            for job in self._scheduler.get_jobs():
                next_run = job.next_run_time
                jobs.append({
                    "id": job.id,
                    "name": job.name or job.id,
                    "next_run": next_run.isoformat() if next_run else None,
                    "trigger": str(job.trigger)
                })
        
        return {
            "enabled": self.enabled,
            "running": self._running,
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "last_status": self._last_status,
            "current_project": self._current_project,
            "scheduled_time": f"{self.cron_hour:02d}:{self.cron_minute:02d}",
            "jobs": jobs
        }
    
    async def start(self):
        """Start the scheduler."""
        if not self.enabled:
            logger.info("⏸️ Scheduler is disabled, not starting")
            return
            
        if self._running:
            logger.warning("Scheduler already running")
            return
        
        self._scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
        
        # Add daily cache warming job
        self._scheduler.add_job(
            self._run_daily_task,
            CronTrigger(hour=self.cron_hour, minute=self.cron_minute),
            id="daily_cache_and_publish",
            name="Daily Cache Warm + Publish",
            replace_existing=True
        )
        
        self._scheduler.start()
        self._running = True
        
        next_run = self._scheduler.get_job("daily_cache_and_publish").next_run_time
        logger.info(f"📅 Scheduler started. Next run: {next_run}")
    
    async def stop(self):
        """Stop the scheduler gracefully."""
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
        self._running = False
        logger.info("⏹️ Scheduler stopped")
    
    async def trigger_now(self) -> Dict[str, Any]:
        """Manually trigger the daily task immediately."""
        logger.info("🔧 Manual trigger requested")
        
        # Run in background to not block the API
        asyncio.create_task(self._run_daily_task())
        
        return {
            "triggered": True,
            "message": "Daily task triggered in background",
            "timestamp": datetime.now().isoformat()
        }
    
    async def _run_daily_task(self):
        """
        Main daily task: warm cache for each project, then publish docs.
        
        For each project:
        1. Warm cache (incremental)
        2. Publish all documentation
        """
        import sys
        import os
        
        # Add project root to path if needed
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        
        from src.processing.batch_processor import BatchProcessor
        from src.processing.docs_processor import DocumentationBatchProcessor
        
        self._last_run = datetime.now()
        self._last_status = "running"
        
        logger.info("=" * 60)
        logger.info("🚀 DAILY SCHEDULED TASK STARTED")
        logger.info(f"⏰ Time: {self._last_run.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 60)
        
        try:
            # Load all projects from config
            processor = BatchProcessor.from_config("projects.yaml")
            project_configs = list(processor.projects.values())
            
            total_projects = len(project_configs)
            logger.info(f"📋 Found {total_projects} projects to process")
            
            for idx, config in enumerate(project_configs, 1):
                project_name = config.name
                self._current_project = project_name
                
                logger.info("-" * 40)
                logger.info(f"📦 [{idx}/{total_projects}] Processing: {project_name}")
                logger.info("-" * 40)
                
                try:
                    # Step 1: Warm cache for this project
                    logger.info(f"🔥 Warming cache for {project_name}...")
                    
                    single_processor = BatchProcessor(projects=[config])
                    cache_result = await single_processor.warm_cache_all(
                        incremental=True,
                        with_history=True
                    )
                    
                    logger.info(f"✅ Cache warmed: {cache_result.total_cached} endpoints, {cache_result.total_mrs} MRs")
                    
                    if cache_result.total_errors > 0:
                        logger.warning(f"⚠️ Cache warming had {cache_result.total_errors} errors")
                    
                    # Step 2: Publish documentation for this project
                    logger.info(f"📝 Publishing documentation for {project_name}...")
                    
                    docs_processor = DocumentationBatchProcessor([config])
                    await docs_processor.publish_all(
                        project_filter=config.path,
                        endpoint_filter=None,
                        dry_run=False
                    )
                    
                    logger.info(f"✅ Documentation published for {project_name}")
                    
                except Exception as e:
                    logger.exception(f"❌ Error processing {project_name}: {e}")
                    continue
            
            self._last_status = "completed"
            self._current_project = None
            
            logger.info("=" * 60)
            logger.info("✅ DAILY SCHEDULED TASK COMPLETED")
            logger.info(f"⏱️ Finished at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info("=" * 60)
            
        except Exception as e:
            self._last_status = f"failed: {str(e)}"
            self._current_project = None
            logger.exception(f"❌ Daily task failed: {e}")


# Global instance
_scheduler_service: Optional[SchedulerService] = None


def get_scheduler() -> SchedulerService:
    """Get or create the global scheduler instance."""
    global _scheduler_service
    if _scheduler_service is None:
        _scheduler_service = SchedulerService()
    return _scheduler_service


def init_scheduler(
    cron_hour: int = 1,
    cron_minute: int = 0,
    enabled: bool = True
) -> SchedulerService:
    """Initialize the global scheduler with custom settings."""
    global _scheduler_service
    _scheduler_service = SchedulerService(
        cron_hour=cron_hour,
        cron_minute=cron_minute,
        enabled=enabled
    )
    return _scheduler_service
