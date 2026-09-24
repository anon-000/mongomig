"""Exception hierarchy. Every error maps to a documented CLI exit code."""

from __future__ import annotations

from enum import IntEnum
from typing import Any


class ExitCode(IntEnum):
    SUCCESS = 0
    VALIDATION_FAILURE = 1
    EXECUTION_FAILURE = 2
    CONFIG_ERROR = 3
    CONFLICT = 4
    LOCK_FAILURE = 5
    CHECKSUM_MISMATCH = 6


class MongoMigError(Exception):
    """Base class for all MongoMig errors.

    ``suggestion`` is a human-readable recovery hint shown by the CLI.
    ``details`` holds structured context (revision, collection, path, ...) for ``--json`` output.
    """

    exit_code: ExitCode = ExitCode.EXECUTION_FAILURE

    def __init__(
        self,
        message: str,
        *,
        suggestion: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.suggestion = suggestion
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": type(self).__name__,
            "message": self.message,
            "suggestion": self.suggestion,
            "exit_code": int(self.exit_code),
            "details": self.details,
        }


# --- exit code 1: validation ---------------------------------------------------------------


class ValidationError(MongoMigError):
    exit_code = ExitCode.VALIDATION_FAILURE


class ScriptError(ValidationError):
    """A revision file is malformed."""


class RevisionNotFoundError(ValidationError):
    pass


class AmbiguousRevisionError(ValidationError):
    pass


# --- exit code 2: execution ----------------------------------------------------------------


class ExecutionError(MongoMigError):
    exit_code = ExitCode.EXECUTION_FAILURE


class DatabaseError(ExecutionError):
    """Connecting to or talking to MongoDB failed."""


# --- exit code 3: configuration ------------------------------------------------------------


class ConfigError(MongoMigError):
    exit_code = ExitCode.CONFIG_ERROR


# --- exit code 4: conflicts in the revision graph ------------------------------------------


class RevisionConflictError(MongoMigError):
    """Duplicate revision ids, cycles, missing parents."""

    exit_code = ExitCode.CONFLICT


class MultipleHeadsError(RevisionConflictError):
    pass


# --- exit code 5 / 6 -----------------------------------------------------------------------


class LockError(MongoMigError):
    exit_code = ExitCode.LOCK_FAILURE


class ChecksumMismatchError(MongoMigError):
    exit_code = ExitCode.CHECKSUM_MISMATCH
