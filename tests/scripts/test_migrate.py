"""Tests for migration script recovery paths."""

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "helm" / "files" / "migrate.nu"


def fake_command() -> str:
    """Return a fake external command implementation."""
    return """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

name = Path(sys.argv[0]).name
log = Path(os.environ['FAKE_LOG'])
with log.open('a') as output:
    output.write(name + ' ' + ' '.join(sys.argv[1:]) + '\\n')
if name == 'pg_dump' and os.environ.get('FAKE_PG_DUMP_FAIL') == '1':
    raise SystemExit(1)
if name == 'pg_dump':
    file_index = sys.argv.index('--file') + 1
    Path(sys.argv[file_index]).write_bytes(b'dump')
if name == 'psql' and 'SELECT EXISTS' in ' '.join(sys.argv):
    print('t')
if name == 'kubectl' and 'create' in sys.argv:
    print('apiVersion: v1')
"""


def run_migration(
    tmp_path: Path, values: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run migrate.nu with fake external commands."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for command in ["kubectl", "psql", "pg_dump", "pg_restore"]:
        path = bin_dir / command
        path.write_text(fake_command())
        path.chmod(0o755)
    environment = os.environ.copy()
    environment.update(values)
    environment["PATH"] = f"{bin_dir}:{environment['PATH']}"
    environment["FAKE_LOG"] = str(tmp_path / "commands.log")
    return subprocess.run(  # noqa: S603
        ["nu", str(SCRIPT)],  # noqa: S607
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


class TestMigrateScript:
    """MigrateScript behavior tests."""

    def test_records_noop_mode(self, tmp_path: Path) -> None:
        """Record completion without running a migration."""
        result = run_migration(
            tmp_path,
            {
                "SOURCE_MODE": "shared",
                "TARGET_MODE": "shared",
                "NAMESPACE": "data-proxy",
            },
        )
        assert result.returncode == 0
        assert "status=completed" in (tmp_path / "commands.log").read_text()

    def test_rejects_empty_schema_list(self, tmp_path: Path) -> None:
        """Reject a migration without configured schemas."""
        result = run_migration(
            tmp_path,
            {
                "SOURCE_MODE": "shared",
                "TARGET_MODE": "per-schema",
                "SCHEMAS": "",
            },
        )
        assert result.returncode != 0
        assert "must list at least one schema" in result.stderr

    def test_records_failure_and_unblocks_syncs(self, tmp_path: Path) -> None:
        """Record migration failure and resume the sync schedule."""
        result = run_migration(
            tmp_path,
            {
                "SOURCE_MODE": "shared",
                "TARGET_MODE": "per-schema",
                "SCHEMAS": "pic",
                "SOURCE_DSN": "source",
                "TARGET_DSN": "target-{schema}",
                "AUTH_USER_ROLE": "user",
                "DBOS_SYSTEM_DATABASE_URL": "system",
                "FAKE_PG_DUMP_FAIL": "1",
            },
        )
        log = (tmp_path / "commands.log").read_text()
        assert result.returncode != 0
        assert "status=failed" in log
        assert "workflow_schedules" in log
        assert "status = 'ACTIVE'" in log

    def test_completes_reverse_migration(self, tmp_path: Path) -> None:
        """Complete a per-schema to shared migration."""
        result = run_migration(
            tmp_path,
            {
                "SOURCE_MODE": "per-schema",
                "TARGET_MODE": "shared",
                "SCHEMAS": "pic",
                "SOURCE_DSN": "source",
                "TARGET_DSN": "target-{schema}",
                "AUTH_USER_ROLE": "user",
                "DBOS_SYSTEM_DATABASE_URL": "system",
                "SQL_TEMPLATE_DIR": str(
                    SCRIPT.parents[2] / "helm" / "files" / "templates" / "postgres"
                ),
                "KUBE_CONTEXT": "",
            },
        )
        log = (tmp_path / "commands.log").read_text()
        assert result.returncode == 0
        assert "status=completed" in log
        assert "pg_restore" in log
        assert "rollout restart" in log
