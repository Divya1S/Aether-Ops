"""The agentic investigation tool menu (PROMPT-11 rule 1).

Read-only tools the model may choose between during evidence gathering.
Safety is layered and inherited: the menu contains only READ-risk actions,
the connector write-guard would deny anything stronger regardless, and the
loop that consumes this menu enforces step budgets and duplicate rejection.
Executors mirror the platform's canonical gathering behavior so the
deterministic fallback path produces today's exact evidence bundle.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from aetherops.agents.base import TransientError
from aetherops.core.types import Citation, Evidence, new_id

# The model's decision contract, enforced by core/schema.py. Membership in
# the menu is checked separately so an out-of-menu request gets a clean
# rejection observation instead of a schema error.
DECISION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["action", "args", "rationale"],
    "properties": {
        "action": {"type": "string"},
        "args": {"type": "object"},
        "rationale": {"type": "string"},
    }}


@dataclass
class ToolOutcome:
    observation: str
    gap: str | None = None
    counted: bool = True
    commits: list = field(default_factory=list)   # newly discovered SHAs


def _add(ctx, kind, summary, citation, classification="INTERNAL"):
    ctx.add_evidence(Evidence(id=new_id("ev"), kind=kind, summary=summary,
                              citation=citation,
                              classification=classification))


def _query_metrics(ctx, service, args):
    result = ctx.connectors.call(
        "datadog", "query_metrics",
        {"query": f"p99{{service:{service}}}", "window": "incident"},
        principal="knowledge")
    _add(ctx, "metrics", f"p99 series {result.data['series']}",
         result.citation)
    return ToolOutcome(f"metrics recorded: {result.citation.excerpt[:120]}")


def _list_recent_deploys(ctx, service, args):
    result = ctx.connectors.call("github", "list_recent_deploys",
                                 {"service": service}, principal="knowledge")
    deploys = result.data["deploys"]
    summary = (f"deploys: {deploys}" if deploys
               else "no deployments for this service in the lookback window")
    _add(ctx, "deploy", summary, result.citation)
    commits = deploys[0]["commits"] if deploys else []
    return ToolOutcome(f"deploy history recorded; commits to inspect: "
                       f"{commits or 'none'}", commits=commits)


def _get_commit_diff(ctx, service, args):
    sha = args.get("sha")
    if not sha:
        return ToolOutcome("get_commit_diff requires args.sha",
                           counted=False)
    result = ctx.connectors.call("github", "get_commit_diff", {"sha": sha},
                                 principal="knowledge")
    _add(ctx, "commit", f"{result.data['sha']}: {result.data['title']}",
         result.citation)
    return ToolOutcome(f"commit {sha} recorded: {result.data['title']}")


def _get_events(ctx, service, args):
    result = ctx.connectors.call("kubernetes", "get_events",
                                 {"service": service}, principal="knowledge")
    events = result.data["events"]
    summary = (f"events: {events[:2]}..." if events
               else "no abnormal pod events in window")
    _add(ctx, "k8s-event", summary, result.citation)
    return ToolOutcome(f"k8s events recorded: {result.citation.excerpt[:120]}")


def _get_thread(ctx, service, args):
    try:
        result = ctx.connectors.call("slack", "get_thread",
                                     {"service": service},
                                     principal="knowledge")
    except KeyError:
        return ToolOutcome("slack connector not registered", counted=False)
    messages = result.data["messages"]
    if messages:
        # People's words get the strictest default (PROMPT-10): withheld
        # from model prompts; humans keep audit visibility.
        _add(ctx, "discussion",
             f"{len(messages)} message(s) in {result.data['channel']}",
             result.citation, classification="CONFIDENTIAL")
        return ToolOutcome(f"{len(messages)} discussion message(s) recorded "
                           "(CONFIDENTIAL — withheld from prompts)")
    return ToolOutcome("no incident-channel discussion in window")


def _search_runbooks(ctx, service, args):
    if getattr(ctx, "rag", None) is None:
        return ToolOutcome("rag store unavailable", counted=False)
    query = (args.get("query")
             or f"{ctx.incident.title} {ctx.incident.description}")
    seen: list[str] = []
    for hit in ctx.rag.search(query, k=6):
        if hit.chunk.doc_id in seen:
            continue
        seen.append(hit.chunk.doc_id)
        _add(ctx, hit.chunk.doc_kind,
             f"{hit.chunk.doc_title}: {hit.chunk.text[:120]}",
             Citation(source="aetherops-rag", ref=hit.ref,
                      excerpt=hit.chunk.text[:200],
                      retrieved_at=time.time()))
        if len(seen) == 2:
            break
    return ToolOutcome(f"runbook guidance recorded: {seen or 'no matches'}")


TOOLS = {
    "query_metrics": {
        "description": "latency/error timeseries for the incident window",
        "execute": _query_metrics},
    "list_recent_deploys": {
        "description": "recent deployments and the commits they shipped",
        "execute": _list_recent_deploys},
    "get_commit_diff": {
        "description": "title/diff for one commit (args: sha)",
        "execute": _get_commit_diff},
    "get_events": {
        "description": "recent Kubernetes pod events for the service",
        "execute": _get_events},
    "get_thread": {
        "description": "incident-channel discussion (screened, quarantined "
                       "if hostile)",
        "execute": _get_thread},
    "search_runbooks": {
        "description": "search operational runbooks/postmortems "
                       "(args: query)",
        "execute": _search_runbooks},
}

BASELINE_SEQUENCE = ("query_metrics", "list_recent_deploys",
                     "get_commit_diff", "get_events", "get_thread",
                     "search_runbooks")


def render_menu() -> str:
    lines = [f"- {name}: {spec['description']}"
             for name, spec in TOOLS.items()]
    lines.append("- finish: stop investigating (rationale: why the "
                 "evidence is sufficient or why you are giving up)")
    return "\n".join(lines)


def extract_json(text: str) -> dict | None:
    """First {...} object in the reply, tolerant of code fences and prose
    — live small models decorate their JSON."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(text[start:index + 1])
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


def run_tool(ctx, service: str, action: str, args: dict) -> ToolOutcome:
    try:
        return TOOLS[action]["execute"](ctx, service, args)
    except TransientError as exc:
        return ToolOutcome(f"{action} failed: {exc}",
                           gap=f"{action}: {exc}")
