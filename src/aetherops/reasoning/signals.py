"""Grounding predicates shared by the Reviewer's plan checks and the
hypothesis tree (docs/02-agents.md, docs/20-operator-console.md).

One definition, two consumers: the Reviewer evaluates these against its own
independent connector reads before a human is asked to approve; the
hypothesis tree evaluates them against the evidence bundle to explain *why*
a candidate cause was validated or ruled out. Keeping them here means the
explanation can never drift from the check that actually gates execution.
"""
from __future__ import annotations

import re

_HHMM = re.compile(r"(\d{1,2}):(\d{2})")
_REMOVED = re.compile(r"-\s*[^\n:]*:\s*(\d+)")
_ADDED = re.compile(r"\+\s*[^\n:]*:\s*(\d+)")
_RAISE_WORDS = re.compile(r"(?i)\b(rais\w*|increas\w*|bump\w*|expand\w*)\b")
# The memory-regression signature the deterministic diagnoser can ground: a
# connection-pool / pool-size limit change (gateway/offline.py uses the same
# pattern on the commit evidence line).
_POOL_SIGNATURE = re.compile(
    r"(?i)connection[ _-]?pool|pool[ _-]?size|max_size")


def minute_of_day(ts: str | None) -> int | None:
    """Minutes-of-day from the first HH:MM in a timestamp. The golden
    snapshots are same-day, so minute-of-day ordering is sufficient here;
    production parses full RFC-3339 timestamps."""
    match = _HHMM.search(ts or "")
    return int(match.group(1)) * 60 + int(match.group(2)) if match else None


def first_breach(series: list[dict]) -> str | None:
    """Timestamp of the first sample whose p99 exceeds 2x the baseline (first
    sample) — a data-driven onset, not a hard-coded SLO threshold."""
    if not series:
        return None
    baseline = series[0].get("p99_ms", 0)
    for point in series:
        if point.get("p99_ms", 0) > 2 * baseline:
            return point.get("ts")
    return None


def numeric_delta(diff: str) -> tuple[int, int] | None:
    """(before, after) for the largest numeric limit a diff removes and adds,
    or None when the diff carries no `key: N` pair on both sides."""
    removed = [int(n) for n in _REMOVED.findall(diff or "")]
    added = [int(n) for n in _ADDED.findall(diff or "")]
    if added and removed:
        return max(removed), max(added)
    return None


def diff_raises_resource(diff: str) -> bool:
    """True when the diff increases a numeric resource limit (a memory
    regression must RAISE something to exhaust memory; a diff that lowers it
    cannot be the cause). Falls back to intent keywords when no numeric
    delta is present."""
    delta = numeric_delta(diff)
    if delta is not None:
        return delta[1] > delta[0]
    return bool(_RAISE_WORDS.search(diff or ""))


def has_pool_signature(text: str) -> bool:
    """Whether a change's text matches the connection-pool signature."""
    return bool(_POOL_SIGNATURE.search(text or ""))
