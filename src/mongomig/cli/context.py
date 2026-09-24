"""State shared by all commands."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mongomig.config.models import LoadedConfig
    from mongomig.migrations.graph import RevisionGraph
    from mongomig.output.console import Output


@dataclass(frozen=True)
class GlobalOptions:
    config: Path | None = None
    env: str | None = None
    json: bool = False
    verbose: bool = False


def load_config(opts: GlobalOptions, out: Output) -> LoadedConfig:
    from mongomig.config.loader import load_config as _load

    config = _load(opts.config, environment=opts.env)
    for warning in config.warnings:
        out.warn(warning)
    out.info(
        f"config: {config.config_path}"
        + (f" (environment: {config.environment})" if config.environment else "")
    )
    return config


def load_graph(config: LoadedConfig) -> RevisionGraph:
    from mongomig.migrations.graph import RevisionGraph
    from mongomig.migrations.script import load_scripts

    return RevisionGraph(load_scripts(config.versions_dir))


def relpath(path: Path, config: LoadedConfig) -> str:
    try:
        return str(path.relative_to(config.root_dir))
    except ValueError:
        return str(path)
