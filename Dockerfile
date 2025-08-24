FROM python:3.11-slim

# Set a non-root user for safety
ARG UNAME=tmcp
ARG UID=1000

ENV DEBIAN_FRONTEND=noninteractive

# Install system deps needed for pty and typical shells
RUN apt-get update \
     && apt-get install -y --no-install-recommends \
         ca-certificates \
         locales \
         procps \
         tzdata \
         bash \
     && rm -rf /var/lib/apt/lists/*

# Create user
RUN groupadd -g ${UID} ${UNAME} || true \
    && useradd -m -u ${UID} -g ${UID} -s /bin/bash ${UNAME} || true

WORKDIR /app

# Copy only requirements first for better caching
COPY requirements.txt /app/requirements.txt

# Install Python deps
RUN pip install --no-cache-dir --upgrade pip \
    && if [ -s requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi

# Copy application
COPY . /app

# Ensure logs directory exists
RUN mkdir -p /app/.terminal-mcp && chown -R ${UNAME}:${UNAME} /app/.terminal-mcp /app

USER ${UNAME}

ENV PYTHONUNBUFFERED=1

# By default run the FastMCP server using stdio transport
CMD ["/usr/local/bin/python", "server.py"]
