FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Pre-install DuckDB extensions so containers don't hit the network on every start.
RUN .venv/bin/python -c "\
import duckdb; \
conn = duckdb.connect(); \
conn.execute('INSTALL httpfs'); \
conn.execute('INSTALL bigquery FROM community'); \
conn.execute('INSTALL postgres'); \
"

COPY src/ src/

ENV PATH=/app/.venv/bin:$PATH
ENV PYTHONPATH=/app/src
