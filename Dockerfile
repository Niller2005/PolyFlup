# Stage 1: Builder
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS builder

# Configure uv
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_PROJECT_ENVIRONMENT="/venv"

WORKDIR /app

# Copy dependency files
COPY uv.lock pyproject.toml ./

# Install dependencies into /venv
RUN uv sync --frozen --no-install-project --no-dev

# Stage 2: Runner
FROM python:3.11-slim-bookworm

# Set working directory
WORKDIR /app

# Copy virtual environment from builder
# We place it outside /app so that binding local directory to /app doesn't overwrite it
COPY --from=builder /venv /venv

# Add virtual environment to PATH
ENV PATH="/venv/bin:$PATH"

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Copy application code
COPY . .

# Run the application using the venv python directly (no uv run needed)
CMD ["python", "polyastra.py"]
