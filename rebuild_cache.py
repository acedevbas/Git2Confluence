
import asyncio
import logging
import sys
import os

# Fix path
sys.path.append(os.getcwd())

from src.processing.batch_processor import BatchProcessor
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("rebuild_cache")

async def rebuild():
    load_dotenv()
    
    # Target project from debug findings
    project_path = "logistic/retail/rms/rms-api"
    
    logger.info(f"🚀 Starting full cache rebuild for: {project_path}")
    logger.info("This will fetch ALL merged MRs and re-compute history.")
    
    try:
        processor = BatchProcessor.from_config("projects.yaml")
        
        # Check if project exists in config
        if project_path not in processor.projects:
            logger.error(f"Project {project_path} not found in projects.yaml")
            available = list(processor.projects.keys())
            logger.info(f"Available projects: {available}")
            return
            
        # Force full rebuild (incremental=False)
        result = await processor.warm_cache_with_history(
            project_path, 
            incremental=False
        )
        
        logger.info("✅ Rebuild complete!")
        logger.info(f"Stats: {result.specs_cached} cached, {result.specs_skipped} skipped, {result.specs_failed} failed")
        
        if result.errors:
            logger.error(f"Errors encountered: {result.errors}")
            
    except Exception as e:
        logger.exception(f"Fatal error: {e}")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(rebuild())
