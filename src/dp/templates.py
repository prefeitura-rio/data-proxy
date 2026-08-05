from functools import lru_cache
from pathlib import Path
from string import Template

SQL_DIR = Path(__file__).parent / "sql"


@lru_cache
def read_template(name: str) -> str:
    """Read and cache a SQL template by name."""
    return (SQL_DIR / f"{name}.sql").read_text()


def load_template(name: str, mapping: dict[str, str]) -> str:
    """Substitute mapping into a cached SQL template and return the final SQL."""
    return Template(read_template(name)).substitute(mapping)
