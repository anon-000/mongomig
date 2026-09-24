from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mongomig.errors import ExitCode, RevisionConflictError, ScriptError
from mongomig.migrations.revision import (
    new_revision_id,
    revision_filename,
    slugify,
    validate_revision_id,
    write_revision,
)
from mongomig.migrations.script import file_checksum, load_script, load_scripts
from tests.helpers import make_revision, write_file


def test_new_revision_id_format_and_uniqueness() -> None:
    ids = {new_revision_id() for _ in range(1000)}
    assert len(ids) == 1000
    assert all(re.fullmatch(r"[0-9a-f]{12}", i) for i in ids)


@pytest.mark.parametrize(
    ("message", "slug"),
    [
        ("Add user profile", "add_user_profile"),
        ("  users.email -> unique!! ", "users_email_unique"),
        ("Café naïve", "cafe_naive"),
        ("!!!", "revision"),
        ("x" * 100, "x" * 40),
    ],
)
def test_slugify(message: str, slug: str) -> None:
    assert slugify(message) == slug


def test_revision_filename() -> None:
    created = datetime(2026, 9, 24, 14, 32, tzinfo=UTC)
    assert revision_filename("7be204a1c9e0", "Add age", created) == (
        "20260924_1432_7be204a1c9e0_add_age.py"
    )


def test_validate_revision_id() -> None:
    assert validate_revision_id("abc_123") == "abc_123"
    with pytest.raises(ScriptError):
        validate_revision_id("bad id!")


def test_rendered_revision_roundtrips(versions_dir: Path) -> None:
    rev_id, path = write_revision(
        versions_dir,
        message='Tricky """quotes""" and \\backslash',
        down_revision=("aaaa11112222", "bbbb33334444"),
        snapshot_hash="sha256:abc",
    )
    source = path.read_text()
    compile(source, str(path), "exec")  # valid Python
    assert 'down_revision = ("aaaa11112222", "bbbb33334444")' in source

    script = load_script(path)
    assert script.revision == rev_id
    assert script.down_revisions == ("aaaa11112222", "bbbb33334444")
    assert script.is_merge
    assert script.message == "Tricky '''quotes''' and \\backslash"
    assert script.snapshot_hash == "sha256:abc"
    assert script.reversible
    assert script.checksum.startswith("sha256:")


def test_write_revision_refuses_overwrite(versions_dir: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    write_revision(
        versions_dir, message="m", down_revision=None, snapshot_hash=None, rev_id="abc", now=now
    )
    with pytest.raises(ScriptError, match="overwrite"):
        write_revision(
            versions_dir, message="m", down_revision=None, snapshot_hash=None, rev_id="abc", now=now
        )


def test_checksum_ignores_line_endings() -> None:
    assert file_checksum(b"a\nb\n") == file_checksum(b"a\r\nb\r\n")
    assert file_checksum(b"a\n") != file_checksum(b"b\n")


VALID = '''"""msg"""
revision = "r1"
down_revision = None
def upgrade(ctx): pass
def downgrade(ctx): pass
'''


@pytest.mark.parametrize(
    ("source", "error"),
    [
        ("def upgrade(ctx): pass\ndef downgrade(ctx): pass\n", "revision"),
        ('revision = "r1"\ndef downgrade(ctx): pass\n', "upgrade"),
        ('revision = "r1"\ndef upgrade(ctx): pass\n', "downgrade"),
        ('revision = "r1"\nrevision_x = 1\ndef upgrade(ctx:\n', "Syntax error"),
        (VALID.replace("None", "compute()"), "literal"),
        (VALID.replace("None", "42"), "down_revision"),
        (VALID + "mongomig_format = 99\n", "format 99"),
        (VALID + "reversible = 'yes'\n", "reversible"),
    ],
)
def test_invalid_scripts(tmp_path: Path, source: str, error: str) -> None:
    path = write_file(tmp_path / "rev.py", source)
    with pytest.raises(ScriptError, match=error) as exc:
        load_script(path)
    assert exc.value.exit_code == ExitCode.VALIDATION_FAILURE


def test_irreversible_script_may_omit_downgrade(tmp_path: Path) -> None:
    path = write_file(
        tmp_path / "rev.py", 'revision = "r1"\nreversible = False\ndef upgrade(ctx): pass\n'
    )
    script = load_script(path)
    assert not script.reversible
    assert not script.has_downgrade


def test_load_script_does_not_execute_code(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    path = write_file(
        tmp_path / "rev.py",
        VALID + f"open({str(marker)!r}, 'w').write('x')\nimport some_app_that_is_not_installed\n",
    )
    load_script(path)
    assert not marker.exists()


def test_load_scripts_skips_private_files_and_detects_duplicates(versions_dir: Path) -> None:
    make_revision(versions_dir, "aaa1")
    write_file(versions_dir / "__init__.py", "")
    write_file(versions_dir / "_helpers.py", "x = 1")
    assert [s.revision for s in load_scripts(versions_dir)] == ["aaa1"]

    write_file(
        versions_dir / "copy.py",
        (versions_dir / next(p.name for p in versions_dir.glob("*aaa1*"))).read_text(),
    )
    with pytest.raises(RevisionConflictError, match="Duplicate"):
        load_scripts(versions_dir)


def test_load_scripts_missing_dir(tmp_path: Path) -> None:
    assert load_scripts(tmp_path / "nope") == []
