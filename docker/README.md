Building and running the terminal-mcp-server in Docker

Build:

```sh
# from repository root
docker build -t terminal-mcp-server:latest .
```

Run (stdio mode - attaches to container's stdio):

```sh
# Run interactively on the host's terminal
docker run --rm -it terminal-mcp-server:latest
```

Run as background service exposing a simple health endpoint (optional):

```sh
docker run --rm -d --name terminal-mcp-server -p 8909:8909 terminal-mcp-server:latest
```

Notes:
- The image uses a non-root user `tmcp` inside container for safety.
- The server runs with the container's default shell (/bin/bash). If you need to pass a different SHELL or environment variables, add `-e SHELL=/bin/sh` or similar to `docker run`.
- On macOS, PTY behavior inside containers may differ; testing on a Linux host is recommended for full PTY fidelity.

Docker Desktop / Docker Compose
--------------------------------

You can use Docker Desktop to import the repository or simply open the `docker-compose.yml` included at the repo root.

To start with Docker Compose:

```sh
# from repository root
docker-compose up --build -d
```

To stop:

```sh
docker-compose down
```

In Docker Desktop you can:
- Use "Add Existing" or "Open" to point to the repo folder and start the compose stack.
- Inspect the `terminal-mcp-events` volume under Volumes to see event logs if enabled.
