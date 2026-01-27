"""
CLI Entry Point for OpenAPI History Tracker.

Provides command-line interface for batch operations:
- Cache warming for all projects
- Documentation generation

Usage:
    python cli.py warm-cache --all
    python cli.py warm-cache --project group/rms-api
    python cli.py generate-docs --all

For cron jobs:
    0 3 * * * cd /app && python cli.py warm-cache --all >> /var/log/openapi.log 2>&1
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import click

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False) -> None:
    """Configure logging level based on verbosity."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.getLogger().setLevel(level)
    
    # Reduce noise from third-party libraries
    logging.getLogger('aiohttp').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)


@click.group()
@click.option('-v', '--verbose', is_flag=True, help='Enable verbose logging')
@click.option(
    '-c', '--config',
    default='projects.yaml',
    help='Path to projects configuration file',
)
@click.pass_context
def cli(ctx: click.Context, verbose: bool, config: str) -> None:
    """
    OpenAPI History Tracker CLI.
    
    Batch operations for cache warming and documentation generation.
    """
    setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj['config_path'] = config
    ctx.obj['verbose'] = verbose


@cli.command('warm-cache')
@click.option('--all', 'process_all', is_flag=True, help='Process all configured projects')
@click.option('--project', '-p', help='Specific project path to process')
@click.option('--full', is_flag=True, help='Full rebuild (not incremental)')
@click.option('--limit', default=None, type=int, help='Maximum MRs to process per project (default: unlimited)')
@click.option('--compute-history', is_flag=True, help='Pre-compute endpoint history (faster doc generation)')
@click.pass_context
def warm_cache(
    ctx: click.Context,
    process_all: bool,
    project: Optional[str],
    full: bool,
    limit: Optional[int],
    compute_history: bool,
) -> None:
    """
    Warm up the cache by downloading and caching OpenAPI specs.
    
    Examples:
    
        # Incremental update for all projects
        python cli.py warm-cache --all
        
        # Full rebuild with history pre-computation
        python cli.py warm-cache --project group/rms-api --full --compute-history
    """
    from batch_processor import BatchProcessor
    
    if not process_all and not project:
        click.echo("Error: Specify --all or --project", err=True)
        sys.exit(1)
    
    config_path = ctx.obj['config_path']
    
    click.echo("=" * 70)
    click.echo(f"OpenAPI History Tracker - Cache Warming")
    click.echo(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    click.echo(f"Config: {config_path}")
    click.echo(f"Mode: {'Full rebuild' if full else 'Incremental'}{' + History' if compute_history else ''}")
    click.echo("=" * 70)
    
    start_time = datetime.now()
    
    try:
        processor = BatchProcessor.from_config(config_path)
        
        if not processor.projects:
            click.echo("No projects configured. Check your projects.yaml", err=True)
            sys.exit(1)
        
        async def run() -> None:
            if project:
                # Single project
                if compute_history:
                    result = await processor.warm_cache_with_history(
                        project,
                        incremental=not full,
                        mr_limit=limit,
                    )
                else:
                    result = await processor.warm_cache_project(
                        project,
                        incremental=not full,
                        mr_limit=limit,
                    )
                _print_project_result(result)
            else:
                # All projects
                result = await processor.warm_cache_all(
                    incremental=not full,
                    mr_limit=limit,
                )
                _print_batch_result(result)
        
        asyncio.run(run())
        
    except Exception as e:
        logger.exception("Cache warming failed")
        click.echo(f"\nError: {e}", err=True)
        sys.exit(1)
    
    duration = (datetime.now() - start_time).total_seconds()
    
    click.echo("=" * 70)
    click.echo(f"Completed in {duration:.1f} seconds")
    click.echo("=" * 70)


@cli.command('generate-docs')
@click.option('--all', 'process_all', is_flag=True, help='Process all configured projects')
@click.option('--project', '-p', help='Specific project path')
@click.option('--endpoint', '-e', help='Specific endpoint (e.g., "POST /orders")')
@click.option('--force', is_flag=True, help='Force regenerate (ignore hash)')
@click.pass_context
def generate_docs(
    ctx: click.Context,
    process_all: bool,
    project: Optional[str],
    endpoint: Optional[str],
    force: bool,
) -> None:
    """
    Generate API documentation for endpoints.
    
    Examples:
    
        # Generate docs for all endpoints in all projects
        python cli.py generate-docs --all
        
        # Generate for specific endpoint
        python cli.py generate-docs --project group/rms-api -e "POST /orders"
    """
    # TODO: Implement documentation generation
    click.echo("Documentation generation not yet implemented")
    click.echo("Coming in Phase 2...")


@cli.command('status')
@click.option('--project', '-p', help='Specific project path')
@click.pass_context
def status(ctx: click.Context, project: Optional[str]) -> None:
    """Show cache status and statistics."""
    from src.cache.disk_cache import DiskCacheManager
    from src.processing.batch_processor import BatchProcessor
    
    config_path = ctx.obj['config_path']
    processor = BatchProcessor.from_config(config_path)
    cache = DiskCacheManager()
    
    click.echo("\n" + "=" * 70)
    click.echo("Cache Status")
    click.echo("=" * 70)
    
    stats = cache.get_stats()
    click.echo(f"\nGlobal Statistics:")
    click.echo(f"  Total specs cached: {stats.get('spec_count', 0)}")
    click.echo(f"  Total schemas cached: {stats.get('schema_count', 0)}")
    click.echo(f"  Cache size: {stats.get('size_mb', 0):.1f} MB")
    
    click.echo(f"\nConfigured Projects: {len(processor.projects)}")
    for path, config in processor.projects.items():
        last_date = processor._get_last_mr_date(path)
        click.echo(f"  - {config.name}")
        click.echo(f"    Path: {path}")
        click.echo(f"    Last MR date: {last_date or 'Never'}")


def _print_project_result(result) -> None:
    """Print result for a single project."""
    click.echo(f"\nProject: {result.project_name}")
    click.echo(f"  Path: {result.project_path}")
    click.echo(f"  MRs found: {result.mrs_found}")
    click.echo(f"  Specs cached: {result.specs_cached}")
    click.echo(f"  Specs skipped: {result.specs_skipped}")
    click.echo(f"  Specs failed: {result.specs_failed}")
    click.echo(f"  Download method: folder={result.method_folder}, archive={result.method_archive}")
    click.echo(f"  Duration: {result.duration_seconds:.1f}s")
    
    if result.errors:
        click.echo(f"\n  Errors ({len(result.errors)}):")
        for error in result.errors[:5]:
            click.echo(f"    - {error}")
        if len(result.errors) > 5:
            click.echo(f"    ... and {len(result.errors) - 5} more")


def _print_batch_result(result) -> None:
    """Print result for batch processing."""
    click.echo(f"\n{'=' * 50}")
    click.echo("Summary")
    click.echo(f"{'=' * 50}")
    click.echo(f"Projects processed: {len(result.project_results)}")
    click.echo(f"Total MRs: {result.total_mrs}")
    click.echo(f"Total specs cached: {result.total_cached}")
    click.echo(f"Total errors: {result.total_errors}")
    click.echo(f"Total duration: {result.total_duration_seconds:.1f}s")
    
    click.echo(f"\n{'=' * 50}")
    click.echo("Per-Project Results")
    click.echo(f"{'=' * 50}")
    
    for r in result.project_results:
        status_icon = "✓" if not r.errors else "⚠"
        click.echo(
            f"{status_icon} {r.project_name}: "
            f"{r.specs_cached} cached, {r.specs_skipped} skipped, "
            f"{r.specs_failed} failed ({r.duration_seconds:.1f}s)"
        )


if __name__ == '__main__':
    cli()
