FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

# Set working directory
WORKDIR /app

# Configure uv to use a specific location for the virtual environment
# This allows us to mount the local directory to /app without hiding the venv
ENV UV_PROJECT_ENVIRONMENT="/uv-venv"
ENV PATH="/uv-venv/bin:$PATH"

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Ensure python output is sent straight to terminal (e.g. container logs) without being buffered
ENV PYTHONUNBUFFERED=1

# Copy dependency files first to leverage cache
COPY uv.lock pyproject.toml ./

# Install dependencies using uv
RUN uv sync --frozen --no-install-project

# Copy the rest of the application
COPY . .

# Run the application
CMD ["uv", "run", "polyastra.py"]
