"""
API dependencies for authentication and common resources.
"""
import os
from typing import Annotated
from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# API Key header scheme
api_key_header = APIKeyHeader(
    name="X-API-Key",
    description="Service API key for authentication"
)

# Get expected API key from environment
SERVICE_API_KEY = os.getenv("SERVICE_API_KEY")

if not SERVICE_API_KEY:
    raise RuntimeError("SERVICE_API_KEY environment variable is not set")


async def verify_api_key(api_key: Annotated[str, Depends(api_key_header)]) -> str:
    """
    Verify the API key from the X-API-Key header.
    
    Args:
        api_key: The API key from the request header
        
    Returns:
        The validated API key
        
    Raises:
        HTTPException: If the API key is invalid
    """
    if api_key != SERVICE_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "ApiKey"}
        )
    return api_key


# Type alias for dependency injection
ApiKeyDep = Annotated[str, Depends(verify_api_key)]
