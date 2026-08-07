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
    ("bearer-token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._=-]{16,}")),
    ("credential-kv", re.compile(
        r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token|token)\b"
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


def redact_value(value):
    """Recursively redact every string inside a JSON-shaped structure.
    Returns (clean_value, finding_labels)."""
    findings: list[str] = []

    def walk(item):
        if isinstance(item, str):
            clean, found = redact_text(item)
            findings.extend(found)
            return clean
        if isinstance(item, dict):
            return {key: walk(val) for key, val in item.items()}
        if isinstance(item, (list, tuple)):
            return [walk(val) for val in item]
        return item

    return walk(value), findings
