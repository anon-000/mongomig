"""Load the project's ``migrations/env.py`` and read ``target_metadata`` from it."""

from __future__ import annotations

import importlib.util
import sys
import traceback
from pathlib import Path
from types import ModuleType
from typing import Any

from mongomig.config.models import LoadedConfig
from mongomig.errors import ConfigError

ENV_MODULE_NAME = "_mongomig_env"


def load_env_module(path: Path, project_root: Path) -> ModuleType:
    """Execute ``env.py`` as a fresh module with ``project_root`` importable.

    The project root goes on ``sys.path`` so ``import app.models`` works from env.py, the same
    way Alembic's env.py imports application code.
    """
    if not path.is_file():
        raise ConfigError(
            f"{path.name} not found at {path}",
            suggestion="Run `mongomig init` or restore migrations/env.py.",
        )
    root = str(project_root)
    if root not in sys.path:
        sys.path.insert(0, root)

    spec = importlib.util.spec_from_file_location(ENV_MODULE_NAME, path)
    if spec is None or spec.loader is None:
        raise ConfigError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[ENV_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(ENV_MODULE_NAME, None)
        last = traceback.extract_tb(exc.__traceback__)[-1]
        raise ConfigError(
            f"Error while executing {path.name}: {type(exc).__name__}: {exc}",
            suggestion="Fix the error in env.py (often an import of your application models).",
            details={"path": str(path), "line": f"{last.filename}:{last.lineno}"},
        ) from exc
    return module


def load_target_metadata(config: LoadedConfig) -> Any:
    module = load_env_module(config.env_py_path, config.root_dir)
    if not hasattr(module, "target_metadata"):
        raise ConfigError(
            f"{config.env_py_path.name} does not define `target_metadata`.",
            suggestion="Add `target_metadata = ...` to migrations/env.py (None is allowed).",
        )
    return module.target_metadata
