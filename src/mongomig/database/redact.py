"""Keep credentials out of anything MongoMig prints or logs."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode

_SCHEME_RE = re.compile(r"^(?P<scheme>mongodb(?:\+srv)?://)(?P<rest>.*)$", re.IGNORECASE)
_SENSITIVE_PARAM_RE = re.compile(r"(pass|secret|token|key|credential)", re.IGNORECASE)
# user:password@ in a URI; used to spot URIs embedded in free text (e.g. driver error messages).
_INLINE_URI_RE = re.compile(r"mongodb(?:\+srv)?://[^\s'\"]+", re.IGNORECASE)

REDACTED = "***"


def redact_uri(uri: str) -> str:
    """Return ``uri`` with password and sensitive query parameters masked.

    ``mongodb://user:pw@host/db?authSource=admin`` -> ``mongodb://user:***@host/db?authSource=admin``
    """
    match = _SCHEME_RE.match(uri.strip())
    if not match:
        return REDACTED
    scheme, rest = match.group("scheme"), match.group("rest")

    # Userinfo ends at the last '@' before the first '/' (passwords may contain '@' if unescaped).
    path_start = rest.find("/")
    authority = rest if path_start == -1 else rest[:path_start]
    tail = "" if path_start == -1 else rest[path_start:]

    if "@" in authority:
        userinfo, hosts = authority.rsplit("@", 1)
        user = userinfo.split(":", 1)[0]
        authority = f"{user}:{REDACTED}@{hosts}" if ":" in userinfo else f"{user}@{hosts}"

    if "?" in tail:
        path, query = tail.split("?", 1)
        params = [
            (k, REDACTED if _SENSITIVE_PARAM_RE.search(k) else v)
            for k, v in parse_qsl(query, keep_blank_values=True)
        ]
        tail = f"{path}?{urlencode(params, safe='*')}"

    return f"{scheme}{authority}{tail}"


def redact_text(text: str) -> str:
    """Mask any MongoDB URI that appears inside arbitrary text."""
    return _INLINE_URI_RE.sub(lambda m: redact_uri(m.group(0)), text)


def uri_has_inline_password(uri: str) -> bool:
    match = _SCHEME_RE.match(uri.strip())
    if not match:
        return False
    rest = match.group("rest")
    authority = rest.split("/", 1)[0]
    if "@" not in authority:
        return False
    userinfo = authority.rsplit("@", 1)[0]
    return ":" in userinfo and userinfo.split(":", 1)[1] != ""


def describe_hosts(uri: str) -> str:
    """Hosts portion of a URI, safe for messages like 'cannot connect to host:27017'."""
    match = _SCHEME_RE.match(uri.strip())
    if not match:
        return "<unknown host>"
    authority = match.group("rest").split("/", 1)[0].split("?", 1)[0]
    return authority.rsplit("@", 1)[-1] or "<unknown host>"
