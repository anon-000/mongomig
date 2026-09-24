from __future__ import annotations

import re
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from mongomig.cli.context import GlobalOptions
from mongomig.config.models import (
    DEFAULT_CONFIG_FILENAME,
    ENV_FILENAME,
    SNAPSHOT_FILENAME,
    VERSIONS_DIRNAME,
)
from mongomig.errors import ConfigError
from mongomig.schema.snapshot import canonical_json, empty_snapshot

if TYPE_CHECKING:
    from rich.console import Console

    from mongomig.output.console import Output

_DIR_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")


def _template(name: str) -> str:
    return files("mongomig").joinpath(f"templates/{name}").read_text(encoding="utf-8")


def run(opts: GlobalOptions, out: Output, *, directory: Path, migrations_dir: str) -> None:
    if not _DIR_NAME_RE.match(migrations_dir):
        raise ConfigError(
            f"Invalid migrations directory name {migrations_dir!r}.",
            suggestion="Use a simple directory name such as 'migrations'.",
        )
    root = directory.expanduser().resolve()
    mig = root / migrations_dir
    planned: dict[Path, str | None] = {
        root / DEFAULT_CONFIG_FILENAME: _template("mongomig.yaml.tmpl").replace(
            "{{migrations_dir}}", migrations_dir
        ),
        mig / ENV_FILENAME: _template("env.py.tmpl"),
        mig / SNAPSHOT_FILENAME: canonical_json(empty_snapshot()),
        mig / VERSIONS_DIRNAME / ".gitkeep": "",
    }

    existing = [p for p in planned if p.exists()]
    if existing:
        raise ConfigError(
            "MongoMig is already initialised here: "
            + ", ".join(str(p.relative_to(root)) for p in existing),
            suggestion="Remove those files first if you really want to start over.",
        )

    for path, content in planned.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content or "", encoding="utf-8")

    created = [str(p.relative_to(root)) for p in planned if p.name != ".gitkeep"]
    created.insert(3, f"{migrations_dir}/{VERSIONS_DIRNAME}/")

    def render(con: Console) -> None:
        con.print(f"[green]Initialised MongoMig in[/green] {root}")
        for item in created:
            con.print(f"  [green]+[/green] {item}")
        con.print("\nNext steps:")
        con.print("  1. export MONGODB_URI=mongodb://localhost:27017")
        con.print('  2. mongomig revision -m "initial"')
        con.print("  3. mongomig current")

    out.result({"root": str(root), "created": created}, render)
