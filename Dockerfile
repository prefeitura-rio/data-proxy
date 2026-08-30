FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /app

RUN useradd --create-home --uid 10001 appuser

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

RUN .venv/bin/python -c "\
import duckdb; \
conn = duckdb.connect(); \
conn.execute('INSTALL httpfs'); \
conn.execute('INSTALL bigquery FROM community'); \
conn.execute('INSTALL postgres'); \
"

COPY src/ src/
RUN chown -R appuser:appuser /app

USER appuser

ENV PATH=/app/.venv/bin:$PATH
ENV PYTHONPATH=/app/src
