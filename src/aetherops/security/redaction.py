"""PII/secret redaction applied at the connector gateway before any external
content reaches workflow state or a hosted model (docs/05-security.md §5).

Production adds a small local NER model for names/addresses; deterministic
patterns run first in both designs because they are auditable and cheap.
Redaction never uses a hosted LLM: content must be clean *before* it leaves
the cell, and a redactor that can hallucinate is not a redactor.
"""
from __future__ import annotations

import re

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?"
                               r"(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")),
    ("github-pat", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b")),
    ("gcp-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
                       r"\.[A-Za-z0-9_-]{8,}\b")),
    ("bearer-token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._=-]{16,}")),
    # Key=value / key:value credentials. No leading \b before the keyword —
    # underscore is a word char, so \b before "token" would MISS the common
    # `github_token`, `db_password`, `client_secret` forms (audit H2). The
    # optional `<segment>_` prefix catches those without matching innocuous
    # words like "monkey" (which has no `=value`).
    ("credential-kv", re.compile(
        r"(?i)(password|passwd|secret|api[_-]?key|access[_-]?key|"
        r"secret[_-]?key|[a-z0-9]+_(?:token|secret|key|password|passwd))"
        r"\s*[=:]\s*[^\s,;'\"]+")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
]


def redact_text(text: str) -> tuple[str, list[str]]:
    """Returns (clean_text, finding_labels). Findings are labels only — the
    matched secret itself is never propagated, including into audit logs."""
    findings: list[str] = []
    for label, pattern in _PATTERNS:
        if pattern.search(text):
            findings.append(label)
            text = pattern.sub(f"[REDACTED:{label}]", text)
    return text, findings


# A dict KEY that names a credential — so a structured secret like
# {"api_key": "dd-..."} is redacted even though the value alone matches no
# key=value pattern (audit M1). Anchored and conservative: bare "key" does not
# match; "api_key"/"github_token"/"db_password" do.
_SENSITIVE_KEY = re.compile(
    r"(?i)^(password|passwd|secret|token|api[_-]?key|apikey|access[_-]?key|"
    r"secret[_-]?key|[a-z0-9]+_(?:token|secret|key|password|passwd))$")


def redact_value(value):
    """Recursively redact every string inside a JSON-shaped structure, and any
    value whose dict key names a credential. Returns (clean_value, labels)."""
    findings: list[str] = []

    def walk(item, key_hint=None):
        if isinstance(item, str):
            if key_hint and _SENSITIVE_KEY.match(key_hint):
                findings.append("credential-kv")
                return "[REDACTED:credential-kv]"
            clean, found = redact_text(item)
            findings.extend(found)
            return clean
        if isinstance(item, dict):
            return {key: walk(val, key_hint=key) for key, val in item.items()}
        if isinstance(item, (list, tuple)):
            return [walk(val) for val in item]
        return item

    return walk(value), findings
