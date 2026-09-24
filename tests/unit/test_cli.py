"""CLI behaviour for the offline commands (no MongoDB needed)."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import warnings
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mongomig._version import __version__
from mongomig.cli.app import app
from mongomig.migrations.script import load_script

runner = CliRunner()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    return tmp_path


def invoke(*args: str) -> tuple[int, str]:
    result = runner.invoke(app, list(args))
    return result.exit_code, result.output


def as_json(*args: str) -> dict:  # type: ignore[type-arg]
    result = runner.invoke(app, ["--json", *args])
    return json.loads(result.stdout)  # type: ignore[no-any-return]


def test_version() -> None:
    code, output = invoke("--version")
    assert code == 0
    assert __version__ in output


def test_init_creates_scaffold(project: Path) -> None:
    assert (project / "mongomig.yaml").is_file()
    assert (project / "migrations" / "env.py").is_file()
    assert (project / "migrations" / "versions").is_dir()
    snapshot = json.loads((project / "migrations" / "schema_snapshot.json").read_text())
    assert snapshot["collections"] == {}
    assert "${MONGODB_URI}" in (project / "mongomig.yaml").read_text()


def test_init_refuses_to_overwrite(project: Path) -> None:
    before = (project / "mongomig.yaml").read_text()
    code, output = invoke("init")
    assert code == 3
    assert "already initialised" in output
    assert (project / "mongomig.yaml").read_text() == before


def test_init_custom_dir(tmp_path: Path) -> None:
    code, _ = invoke("init", str(tmp_path / "svc"), "--migrations-dir", "db_migrations")
    assert code == 0
    assert (tmp_path / "svc" / "db_migrations" / "env.py").is_file()
    assert "directory: db_migrations" in (tmp_path / "svc" / "mongomig.yaml").read_text()


def test_init_rejects_bad_dir_name(tmp_path: Path) -> None:
    code, _ = invoke("init", str(tmp_path), "--migrations-dir", "../escape")
    assert code == 3


def test_revision_chain_and_history(project: Path) -> None:
    r1 = as_json("revision", "-m", "initial")
    r2 = as_json("revision", "-m", "add profile")
    r3 = as_json("revision", "-m", "add indexes")
    assert r1["down_revision"] is None
    assert r2["down_revision"] == r1["revision"]
    assert r3["down_revision"] == r2["revision"]

    script = load_script(project / r3["path"])
    assert script.snapshot_hash is not None
    assert script.snapshot_hash.startswith("sha256:")

    history = as_json("history")["revisions"]
    assert [h["revision"] for h in history] == [r3["revision"], r2["revision"], r1["revision"]]
    assert history[0]["is_head"]
    assert history[-1]["is_base"]

    code, output = invoke("history")
    assert code == 0
    assert f"{r2['revision']} -> {r3['revision']} (head), add indexes" in output


def test_multiple_heads_block_new_revision(project: Path) -> None:
    r1 = as_json("revision", "-m", "initial")["revision"]
    as_json("revision", "-m", "a")
    result = runner.invoke(app, ["--json", "revision", "-m", "b", "--head", r1])
    assert result.exit_code == 0
    assert "second head" in result.stderr

    assert len(as_json("heads")["heads"]) == 2
    code, output = invoke("revision", "-m", "c")
    assert code == 4
    assert "Multiple heads" in output
    assert "--head" in output

    err = as_json("revision", "-m", "c")["error"]
    assert err["type"] == "MultipleHeadsError"
    assert err["exit_code"] == 4


def test_revision_with_prefix_and_custom_id(project: Path) -> None:
    as_json("revision", "-m", "initial", "--rev-id", "first_rev")
    child = as_json("revision", "-m", "next", "--head", "firs")
    assert child["down_revision"] == "first_rev"
    code, output = invoke("revision", "-m", "dup", "--rev-id", "first_rev")
    assert code == 4
    assert "already exists" in output


def test_heads_empty(project: Path) -> None:
    code, output = invoke("heads")
    assert code == 0
    assert "No revisions yet" in output


def test_missing_config_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    code, output = invoke("history")
    assert code == 3
    assert "mongomig init" in output


def test_broken_revision_file_reports_path(project: Path) -> None:
    (project / "migrations" / "versions" / "broken.py").write_text("revision = \n")
    err = as_json("history")["error"]
    assert err["type"] == "ScriptError"
    assert err["details"]["path"].endswith("broken.py")


def test_current_needs_uri(project: Path) -> None:
    code, output = invoke("current")
    assert code == 3
    assert "MONGODB_URI" in output


def test_cli_startup_time() -> None:
    """Design target: < 500 ms for `--help`. Hard-fail only on gross regressions."""
    cmd = [sys.executable, "-m", "mongomig", "--help"]
    subprocess.run(cmd, check=True, capture_output=True)  # warm bytecode cache
    start = time.perf_counter()
    subprocess.run(cmd, check=True, capture_output=True)
    elapsed = time.perf_counter() - start
    if elapsed > 0.5:
        warnings.warn(f"mongomig --help took {elapsed:.2f}s (target < 0.5s)", stacklevel=1)
    assert elapsed < 2.0
