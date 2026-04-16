FROM python:3.11-slim

# Create a non-root user so the agent sandbox cannot damage the host or
# read privileged process memory via /proc/1/environ.
RUN useradd -m -u 1000 -s /bin/bash sandbox

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install ALL dependencies (includes pytest and openenv-core)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code and hand ownership to sandbox user
COPY . /app
RUN chown -R sandbox:sandbox /app && \
    mkdir -p /tmp/codereview_workspaces && \
    chown sandbox:sandbox /tmp/codereview_workspaces

USER sandbox

# Expose the port
EXPOSE 7860

# Run the server
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "7860"]
