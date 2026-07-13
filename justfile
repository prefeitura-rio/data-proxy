# List available commands
default:
    @just --list

# Start the local stack (pg_duckdb + PostgREST)
up:
    @echo "🐳 Starting pg_duckdb + PostgREST..."
    docker compose up -d
    @echo "PostgREST:  http://localhost:3111"
    @echo "Postgres:   localhost:5544"

# Stop the local stack
down:
    docker compose down

# Stop the local stack and wipe all data (fresh start)
reset:
    docker compose down -v

# Tail logs from both services
logs:
    docker compose logs -f

# Run the BigQuery -> GCS -> pg_duckdb sync script
sync:
    uv run python scripts/sync.py

# Seed the synthetic dataset into BigQuery (one-time / re-runnable)
seed-bq:
    uv run python scripts/seed_bigquery.py

# Run demo requests against PostgREST (filtering, pagination, RLS)
demo:
    ./scripts/demo.sh

# Lint Python code
lint:
    uv run ruff check scripts/

# Format Python code
fmt:
    uv run ruff format scripts/
