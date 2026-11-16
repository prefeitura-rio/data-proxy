"""Read the goose migrations of the repository, so that a test can run them in its own transaction."""

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Final

MIGRATIONS: Final = Path(__file__).parents[3] / "migrations"
ENVSUB: Final = re.compile(r"\$\{(\w+)\}")
ROLES: Final = {
    "AUTH_ANON_ROLE": "anon",
    "AUTH_USER_ROLE": "dp_api",
    "AUTH_SIGNER_ROLE": "migration_signer",
    "PGUSER": "test",
}


def migration_files(folder: str) -> list[Path]:
    """Return the migrations of one folder, in the order that goose applies them."""
    return sorted((MIGRATIONS / folder).glob("*.sql"))


def up_part(text: str) -> str:
    """Return the Up part of a goose migration."""
    return text.split("-- +goose Up", 1)[1].split("-- +goose Down", 1)[0]


def down_part(text: str) -> str:
    """Return the Down part of a goose migration, or nothing when it has none."""
    parts = text.split("-- +goose Down", 1)

    return parts[1] if len(parts) == 2 else ""


def fill(text: str, env: Mapping[str, str]) -> str:
    """Fill ${NAME} between `ENVSUB ON` and `ENVSUB OFF`, as goose does. A name that is not set is an error."""
    lines: list[str] = []
    active = False
    for line in text.split("\n"):
        if line.startswith("-- +goose ENVSUB"):
            active = line.split()[-1] == "ON"
            lines.append(line)
        elif active:
            lines.append(ENVSUB.sub(lambda match: env[match.group(1)], line))
        else:
            lines.append(line)

    return "\n".join(lines)


def migration_sql(
    folder: str,
    name: str,
    *,
    part: str = "up",
    schema: str | None = None,
    env: Mapping[str, str] = ROLES,
) -> str:
    """Return one part of one migration as SQL that a test can run.

    With `schema`, the application schema data_proxy becomes that schema, so that a test can try the objects of a
    migration in a schema of its own, which the test drops afterward.
    """
    text = (MIGRATIONS / folder / name).read_text()
    sql = fill(up_part(text) if part == "up" else down_part(text), env)

    return re.sub(r"\bdata_proxy\b", schema, sql) if schema is not None else sql


def console_section(number: int) -> str:
    """Return the part of the console schema migration that came from the old console migration with this number."""
    text = "\n".join(
        up_part(file.read_text())
        for file in sorted((MIGRATIONS / "console").glob("0000[6-9]_console_*.sql"))
    )
    sections = re.split(r"(?m)^-- From console migration (\d{5})_\w+\n", text)
    found = dict(zip(sections[1::2], sections[2::2], strict=True))

    return str(found[f"{number:05d}"])


def description_lines(text: str) -> list[str]:
    """Return the comment lines of the paragraph that describes a migration.

    The paragraph starts at the first comment that is not a goose or noqa directive,
    and it ends at the first blank line, directive or statement.
    """
    lines: list[str] = []
    started = False

    for line in text.split("\n"):
        directive = line.startswith(
            ("-- +goose", "-- noqa", "-- From console migration")
        )
        if line.startswith("--") and not directive:
            lines.append(line)
            started = True
        elif started:
            break

    return lines
