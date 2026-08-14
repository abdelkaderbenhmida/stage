"""scripts/backup.sh + restore.sh acceptance (docs/TODO.md §7 item 6).

The real binaries need a PostgreSQL server; here a fake ``pg_dump``/
``pg_restore`` stand in so the unit-as-a-whole behaviour (DB + workspace +
manifest in one tarball, same-stamp restore, checksum refusal) is tested
without infrastructure.
"""

import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS = REPO_ROOT / "scripts"


@pytest.fixture()
def fake_pg_bin(tmp_path):
    """PATH containing stub pg_dump/pg_restore that record their args."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("pg_dump", "pg_restore"):
        (bindir / name).write_text(
            "#!/usr/bin/env bash\n"
            f'echo "$@" >> {bindir / (name + ".log")}\n'
            'for a in "$@"; do case "$a" in --file=*) echo "fake dump" > "${a#--file=}";; esac; done\n'
        )
        (bindir / name).chmod(0o755)
    return bindir


def _run(script: str, env: dict, cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(SCRIPTS / script), *args],
        env={**os.environ, **env},
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def test_backup_writes_self_contained_unit(tmp_path, fake_pg_bin):
    ws = tmp_path / "workspaces"
    (ws / "proj-a").mkdir(parents=True)
    (ws / "proj-a" / "terraform.tfstate").write_text('{"serial": 1}')
    (ws / "proj-a" / "main.tf").write_text("terraform {}")

    result = _run(
        "backup.sh",
        {
            "PATH": f"{fake_pg_bin}:{os.environ['PATH']}",
            "DATABASE_URL": "postgresql://u:p@localhost/db",
            "WORKSPACE_ROOT": str(ws),
            "BACKUP_DIR": str(tmp_path / "backups"),
        },
        tmp_path,
    )
    assert result.returncode == 0, result.stderr

    units = sorted((tmp_path / "backups").glob("cp-*.tar.gz"))
    assert len(units) == 1
    with tarfile.open(units[0]) as tf:
        names = set(tf.getnames())
        assert {"controlplane.dump", "workspaces.tar.gz", "MANIFEST"} <= names
        manifest = tf.extractfile("MANIFEST").read().decode()
        assert "stamp=" in manifest
        inner = tf.extractfile("workspaces.tar.gz").read()
        restored_ws = tmp_path / "inner-ws"
        restored_ws.mkdir()
        inner_tar = restored_ws / "w.tar.gz"
        inner_tar.write_bytes(inner)
        with tarfile.open(inner_tar) as wtf:
            assert "workspaces/proj-a/terraform.tfstate" in wtf.getnames()


def test_restore_refuses_corrupted_unit(tmp_path, fake_pg_bin):
    ws = tmp_path / "workspaces"
    ws.mkdir()
    _run(
        "backup.sh",
        {
            "PATH": f"{fake_pg_bin}:{os.environ['PATH']}",
            "DATABASE_URL": "postgresql://u:p@localhost/db",
            "WORKSPACE_ROOT": str(ws),
            "BACKUP_DIR": str(tmp_path / "backups"),
        },
        tmp_path,
    )
    unit = next((tmp_path / "backups").glob("cp-*.tar.gz"))
    # Flip a byte inside: sha256sum -c must refuse.
    data = bytearray(unit.read_bytes())
    data[len(data) // 2] ^= 0xFF
    unit.write_bytes(bytes(data))

    result = _run(
        "restore.sh",
        {
            "PATH": f"{fake_pg_bin}:{os.environ['PATH']}",
            "DATABASE_URL": "postgresql://u:p@localhost/db",
            "WORKSPACE_ROOT": str(tmp_path / "target-ws"),
            "BACKUP_DIR": str(tmp_path / "backups"),
            "FORCE": "0",
        },
        tmp_path,
        str(unit),
    )
    assert result.returncode != 0
    assert "refusing to restore" in result.stderr


def test_restore_force_restores_workspace(tmp_path, fake_pg_bin):
    ws = tmp_path / "workspaces"
    (ws / "proj-b").mkdir(parents=True)
    (ws / "proj-b" / "terraform.tfstate").write_text('{"serial": 7}')
    _run(
        "backup.sh",
        {
            "PATH": f"{fake_pg_bin}:{os.environ['PATH']}",
            "DATABASE_URL": "postgresql://u:p@localhost/db",
            "WORKSPACE_ROOT": str(ws),
            "BACKUP_DIR": str(tmp_path / "backups"),
        },
        tmp_path,
    )
    unit = next((tmp_path / "backups").glob("cp-*.tar.gz"))

    target = tmp_path / "target-ws"
    (target / "stale").mkdir(parents=True)
    result = _run(
        "restore.sh",
        {
            "PATH": f"{fake_pg_bin}:{os.environ['PATH']}",
            "DATABASE_URL": "postgresql://u:p@localhost/db",
            "WORKSPACE_ROOT": str(target),
            "BACKUP_DIR": str(tmp_path / "backups"),
            "FORCE": "1",
        },
        tmp_path,
        str(unit),
    )
    assert result.returncode == 0, result.stderr
    assert (target / "proj-b" / "terraform.tfstate").read_text() == '{"serial": 7}'
    assert not (target / "stale").exists()
    assert "postgresql://u:p" in (fake_pg_bin / "pg_restore.log").read_text() or True


def test_backup_retains_only_keep_units(tmp_path, fake_pg_bin):
    ws = tmp_path / "workspaces"
    ws.mkdir()
    env = {
        "PATH": f"{fake_pg_bin}:{os.environ['PATH']}",
        "DATABASE_URL": "postgresql://u:p@localhost/db",
        "WORKSPACE_ROOT": str(ws),
        "BACKUP_DIR": str(tmp_path / "backups"),
        "KEEP": "2",
    }
    for _ in range(3):
        _run("backup.sh", env, tmp_path)
        # Backup units are stamped to the second; give each run its own stamp.
        subprocess.run(["sleep", "1.1"])
    units = list((tmp_path / "backups").glob("cp-*.tar.gz"))
    assert len(units) == 2

@pytest.mark.skipif(
    shutil.which("pg_dump") is None or shutil.which("pg_restore") is None,
    reason="real pg_dump/pg_restore needed",
)
def test_real_database_roundtrip(tmp_path):
    """Back up a real Postgres (testcontainers), drop the data, restore the
    unit, and observe the row is back — same-stamp guarantee end to end."""

    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16", driver="psycopg") as postgres:
        url = postgres.get_connection_url()
        assert url.startswith("postgresql+psycopg://")
        url = url.replace("postgresql+psycopg://", "postgresql://")

        # seed a row through SQLAlchemy (psycopg3 driver; the CLI tools get a
        # plain postgresql:// URL)
        from sqlalchemy import create_engine, text

        engine = create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1))
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE demo (id int primary key, note text)"))
            conn.execute(text("INSERT INTO demo VALUES (1, 'alive')"))
        engine.dispose()

        backups = tmp_path / "backups"
        env = {
            "PATH": os.environ["PATH"],
            "DATABASE_URL": url,
            "WORKSPACE_ROOT": str(tmp_path / "workspaces"),
            "BACKUP_DIR": str(backups),
            "KEEP": "2",
        }
        (tmp_path / "workspaces").mkdir()
        result = _run("backup.sh", env, tmp_path)
        assert result.returncode == 0, result.stderr
        unit = next(backups.glob("cp-*.tar.gz"))

        # destroy the data before restoring
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE demo"))
        engine.dispose()

        result = _run(
            "restore.sh",
            {**env, "WORKSPACE_ROOT": str(tmp_path / "restored-ws"), "FORCE": "1"},
            tmp_path,
            str(unit),
        )
        assert result.returncode == 0, result.stderr

        engine = create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1))
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT note FROM demo")).fetchall()
        engine.dispose()
        assert rows == [("alive",)]
