
import asyncio
import argparse
import sys
import logging
import os
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("manage")

# Load environment
load_dotenv()

# Fix path to include project root
sys.path.append(os.getcwd())

from src.processing.batch_processor import BatchProcessor


async def warm_cache(args):
    """Execute cache warming."""
    try:
        processor = BatchProcessor.from_config("projects.yaml")
        
        # Check project filter
        if args.project:
            logger.info(f"Targeting single project: {args.project}")
            if args.project not in processor.projects:
                logger.error(f"Project '{args.project}' not found in projects.yaml")
                logger.info(f"Available: {list(processor.projects.keys())}")
                sys.exit(1)
            
            # Create restricted processor
            config = processor.projects[args.project]
            processor = BatchProcessor(projects=[config])
            
        logger.info(f"🚀 Starting cache warming (Incremental: {not args.full}, History: True)")
        
        result = await processor.warm_cache_all(
            incremental=not args.full,
            with_history=True
        )
        
        logger.info("✅ Cache warming complete")
        logger.info(f"Stats: {result.total_cached} cached, {result.total_mrs} MRs processed")
        
        if result.total_errors > 0:
            logger.error(f"Encountered {result.total_errors} errors")
            for p_res in result.project_results:
                for err in p_res.errors:
                    logger.error(f"[{p_res.project_name}] {err}")
            sys.exit(1)
            
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)


async def publish_docs(args):
    """Execute batch documentation publication."""
    try:
        from src.processing.docs_processor import DocumentationBatchProcessor
        
        processor = BatchProcessor.from_config("projects.yaml")
        # We reuse BatchProcessor to load config, but we need ProjectConfig list
        # Actually docs_processor creates its own instance or takes config list
        # Let's adjust docs_processor init to take list of ProjectConfig
        
        # BatchProcessor has self.projects used in warm_cache
        # Let's extract config loading logic or just use BatchProcessor to load config
        
        # Simpler:
        configs = list(processor.projects.values())
        docs_processor = DocumentationBatchProcessor(configs)
        
        project_filter = args.project
        endpoint_filter = args.endpoint
        
        if not project_filter and not args.all:
            logger.error("Must specify --project or --all")
            sys.exit(1)
            
        await docs_processor.publish_all(
            project_filter=project_filter,
            endpoint_filter=endpoint_filter,
            dry_run=args.dry_run
        )
            
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="OpenAPI History Tracker Management CLI")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # command: cache:warm
    warm_parser = subparsers.add_parser("cache:warm", help="Warm up cache for projects")
    warm_parser.add_argument("--full", action="store_true", help="Force full rebuild (ignore incremental)")
    warm_parser.add_argument("--project", type=str, help="Run for specific project path")
    
    # command: docs:publish
    docs_parser = subparsers.add_parser("docs:publish", help="Publish documentation to Confluence")
    docs_parser.add_argument("--project", type=str, help="Specific project path")
    docs_parser.add_argument("--endpoint", type=str, help="Specific endpoint (e.g., 'POST /path')")
    docs_parser.add_argument("--all", action="store_true", help="Process all projects")
    docs_parser.add_argument("--dry-run", action="store_true", help="Simulate without publishing")
    
    args = parser.parse_args()
    
    if args.command == "cache:warm":
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(warm_cache(args))
    elif args.command == "docs:publish":
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(publish_docs(args))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
