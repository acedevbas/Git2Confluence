# Base image
FROM python:3.12-slim

# Build-time version, surfaced by the app at /docs, /, /health and /version.
# Pass with: docker build --build-arg APP_VERSION=1.03 ...
ARG APP_VERSION=dev

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    APP_VERSION=${APP_VERSION}

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create directories for volumes
RUN mkdir -p logs cache_data

# Create user for security (optional but recommended, though sticking to root for simple file permissions on volumes often easier in simple setups. 
# Let's keep it simple for now, as volume permissions can be tricky on Windows/Linux boundaries)

# Expose port
EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD curl -f http://localhost:8000/api/v1/health || exit 1

# Command to run the application
CMD ["python", "run_api.py", "--host", "0.0.0.0", "--port", "8000"]
