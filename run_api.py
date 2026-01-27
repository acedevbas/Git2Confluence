#!/usr/bin/env python
"""
Entry point for running the OpenAPI History Tracker API.

Usage:
    python run_api.py
    
    # With custom port
    python run_api.py --port 8080
    
    # With reload for development
    python run_api.py --reload
"""
import argparse
import uvicorn


def main():
    parser = argparse.ArgumentParser(
        description="OpenAPI History Tracker API Server"
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind to (default: 8000)"
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of worker processes (default: 1)"
    )
    
    args = parser.parse_args()
    
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║         OpenAPI History Tracker API                          ║
╠══════════════════════════════════════════════════════════════╣
║  Host: {args.host:<54}║
║  Port: {args.port:<54}║
║  Docs: http://{args.host}:{args.port}/docs{' ' * (42 - len(str(args.port)))}║
╚══════════════════════════════════════════════════════════════╝
    """)
    
    uvicorn.run(
        "api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers if not args.reload else 1,
        log_level="info"
    )


if __name__ == "__main__":
    main()
