"""Connector gateway abstraction (docs/04-connectivity.md).

Every integration is a Connector exposing typed tools. `Connector.call` is the
single choke point through which any external system is touched, and it
enforces the gateway contract in order:

  1. tool must exist and be declared (no undeclared capability);
  2. rate limiting (sliding window per tool);
  3. read-through cache for cacheable (read) tools;
  4. invoke the adapter;
  5. redact secrets/PII from the result before it enters workflow state;
  6. audit the call (hash-chained ledger).

Production runs these as sandboxed MCP server processes behind a gateway
service; the contract is identical.
"""
from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass

from aetherops.agents.base import TransientError
from aetherops.core.types import Citation, RiskLevel
from aetherops.security.redaction import redact_text, redact_value


class RateLimitExceeded(TransientError):
    """Transient by design: the DAG executor retries with backoff."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    risk: RiskLevel = RiskLevel.READ
    cacheable: bool = True          # writes must never be cacheable
    rate_per_min: int = 60

    def __post_init__(self):
        if self.risk > RiskLevel.READ and self.cacheable:
            raise ValueError(f"write tool {self.name!r} cannot be cacheable")


@dataclass(frozen=True)
class ToolResult:
    data: dict
    citation: Citation
    cached: bool = False


class Connector:
    """Subclasses set `system`, declare TOOLS, and implement `_invoke`."""

    system: str = "base"
    TOOLS: dict[str, ToolSpec] = {}

    def __init__(self, audit=None, clock=time.monotonic):
        self._audit = audit
        self._clock = clock
        self._cache: dict[str, ToolResult] = {}
        self._call_times: dict[str, deque] = {}

    # Principals permitted to invoke write-risk tools: the Control plane's
    # executor (forward remediation) and the compensator (saga undo). Agents
    # retrieve; they never write — enforced here as mechanism, not convention
    # (OWASP LLM06; PROMPT-10 layer 2). Compensation MUST be able to undo
    # MEDIUM+ writes, or the saga is fiction (audit C2).
    WRITE_PRINCIPALS = frozenset({"executor", "compensator"})

    def call(self, tool: str, args: dict | None = None,
             principal: str = "workflow") -> ToolResult:
        args = args or {}
        spec = self.TOOLS.get(tool)
        if spec is None:
            raise ValueError(f"{self.system}: undeclared tool {tool!r}")

        if (spec.risk >= RiskLevel.MEDIUM
                and principal not in self.WRITE_PRINCIPALS):
            if self._audit is not None:
                self._audit.append(
                    actor=principal, action="tool.denied",
                    payload={"system": self.system, "tool": tool,
                             "risk": spec.risk.name,
                             "reason": "write-risk tools require the executor "
                                       "or compensator principal"})
            raise PermissionError(
                f"{self.system}.{tool}: {spec.risk.name}-risk tool denied "
                f"for principal {principal!r} — writes go through the "
                "Control plane's executor (or compensator for saga undo) only")

        self._check_rate(spec)

        cache_key = f"{tool}:{json.dumps(args, sort_keys=True, default=str)}"
        if spec.cacheable and cache_key in self._cache:
            hit = self._cache[cache_key]
            self._log(tool, principal, spec, args, cached=True)
            return ToolResult(hit.data, hit.citation, cached=True)

        result = self._invoke(tool, args)
        clean, findings = redact_value(result.data)
        excerpt, excerpt_findings = redact_text(result.citation.excerpt)
        citation = result.citation
        if excerpt_findings:
            citation = Citation(source=citation.source, ref=citation.ref,
                                excerpt=excerpt, retrieved_at=citation.retrieved_at)
        result = ToolResult(clean, citation)
        self._log(tool, principal, spec, args, cached=False,
                  redactions=findings + excerpt_findings)

        if spec.cacheable:
            self._cache[cache_key] = result
        return result

    def cite(self, ref: str, excerpt: str) -> Citation:
        return Citation(source=self.system, ref=ref, excerpt=excerpt)

    def _invoke(self, tool: str, args: dict) -> ToolResult:
        raise NotImplementedError

    def _check_rate(self, spec: ToolSpec) -> None:
        now = self._clock()
        window = self._call_times.setdefault(spec.name, deque())
        while window and now - window[0] > 60.0:
            window.popleft()
        if len(window) >= spec.rate_per_min:
            raise RateLimitExceeded(
                f"{self.system}.{spec.name}: {spec.rate_per_min}/min exceeded")
        window.append(now)

    def _log(self, tool: str, principal: str, spec: ToolSpec, args: dict,
             cached: bool, redactions: list | None = None) -> None:
        if self._audit is not None:
            # Redact the ARGS too (audit M1): a secret passed as a tool
            # argument must not land in the ledger in cleartext, or the
            # redaction guarantee is only half kept.
            clean_args, arg_findings = redact_value(args)
            self._audit.append(
                actor=principal,
                action="tool.call",
                payload={"system": self.system, "tool": tool,
                         "risk": spec.risk.name, "cached": cached,
                         "args": clean_args,
                         "redactions": (redactions or []) + arg_findings})


class ConnectorRegistry:
    def __init__(self):
        self._connectors: dict[str, Connector] = {}

    def register(self, connector: Connector) -> Connector:
        self._connectors[connector.system] = connector
        return connector

    def get(self, system: str) -> Connector:
        if system not in self._connectors:
            raise KeyError(f"no connector registered for {system!r}")
        return self._connectors[system]

    def call(self, system: str, tool: str, args: dict | None = None,
             principal: str = "workflow") -> ToolResult:
        return self.get(system).call(tool, args, principal)

    def systems(self) -> list[str]:
        return sorted(self._connectors)
