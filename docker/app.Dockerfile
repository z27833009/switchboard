FROM python:3.12.13-slim-bookworm

# uv comes from PyPI at the version pinned in pyproject.toml's toolchain, so the
# image uses the same lock file as a local checkout.
RUN pip install --no-cache-dir uv==0.11.21

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
COPY mcp_servers ./mcp_servers
COPY config ./config
RUN uv sync --locked --no-dev --no-editable

EXPOSE 8000 8001
