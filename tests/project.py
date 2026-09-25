"""A throwaway MongoMig project whose models file tests can rewrite between CLI calls."""

from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from mongomig.cli.app import app
from mongomig.metadata import registry

runner = CliRunner()


class Project:
    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root = root
        monkeypatch.chdir(root)
        monkeypatch.setattr(sys, "dont_write_bytecode", True)  # models change within a second
        self.pkg = f"proj_{secrets.token_hex(4)}"
        (root / self.pkg).mkdir()
        (root / self.pkg / "__init__.py").write_text("")
        assert self.run("init").exit_code == 0
        env = root / "migrations" / "env.py"
        env.write_text(
            env.read_text().replace(
                "# import app.models  # noqa: F401  (importing registers @collection models)",
                f"import {self.pkg}.models  # noqa: F401",
            )
        )
        self.models("")

    @property
    def versions(self) -> Path:
        return self.root / "migrations" / "versions"

    @property
    def snapshot(self) -> dict[str, Any]:
        return json.loads((self.root / "migrations" / "schema_snapshot.json").read_text())

    def models(self, body: str) -> None:
        header = (
            "import datetime\nfrom pydantic import BaseModel, Field\n"
            "from mongomig import collection, Index, MongoMetadata\n\n"
        )
        (self.root / self.pkg / "models.py").write_text(header + body)

    def run(self, *args: str) -> Result:
        # Each CLI call must see the models file as it is now, like a fresh process would.
        registry._default = None
        for name in [m for m in sys.modules if m == self.pkg or m.startswith(self.pkg + ".")]:
            del sys.modules[name]
        return runner.invoke(app, list(args))

    def json(self, *args: str) -> Any:
        result = self.run("--json", *args)
        return json.loads(result.stdout)
