from __future__ import annotations

import pytest

from mongomig.database.redact import (
    describe_hosts,
    redact_text,
    redact_uri,
    uri_has_inline_password,
)


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("mongodb://localhost:27017", "mongodb://localhost:27017"),
        ("mongodb://user:pw@host:27017/db", "mongodb://user:***@host:27017/db"),
        ("mongodb://user@host/db", "mongodb://user@host/db"),
        (
            "mongodb+srv://u:p%40ss@cluster.example.net/app?retryWrites=true",
            "mongodb+srv://u:***@cluster.example.net/app?retryWrites=true",
        ),
        (
            "mongodb://h1,h2/db?replicaSet=rs0&tlsCertificateKeyFilePassword=x",
            "mongodb://h1,h2/db?replicaSet=rs0&tlsCertificateKeyFilePassword=***",
        ),
        ("not-a-uri", "***"),
    ],
)
def test_redact_uri(uri: str, expected: str) -> None:
    assert redact_uri(uri) == expected


def test_redact_uri_never_contains_password() -> None:
    assert "hunter2" not in redact_uri(
        "mongodb://admin:hunter2@db.internal:27017/?authSource=admin"
    )


def test_redact_text_masks_embedded_uris() -> None:
    text = "failed to connect to mongodb://a:secret@h:1/db because reasons"
    assert redact_text(text) == "failed to connect to mongodb://a:***@h:1/db because reasons"


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("mongodb://user:pw@host", True),
        ("mongodb://user@host", False),
        ("mongodb://user:@host", False),
        ("mongodb://host", False),
        ("${MONGODB_URI}", False),
    ],
)
def test_uri_has_inline_password(uri: str, expected: bool) -> None:
    assert uri_has_inline_password(uri) is expected


def test_describe_hosts() -> None:
    assert describe_hosts("mongodb://u:p@h1:1,h2:2/db?x=1") == "h1:1,h2:2"
    assert describe_hosts("garbage") == "<unknown host>"
