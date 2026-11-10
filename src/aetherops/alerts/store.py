"""Alert intake for the Sense plane: normalize -> deduplicate -> correlate
(docs/20-operator-console.md §3).

An outage never arrives as one alert. A bad deploy pages for latency, then
for OOMKilled pods, then for every caller that started timing out — and the
same monitor re-fires every evaluation interval. Asking a human (or an
agent fleet) to investigate each of those is the alert-fatigue failure mode.
This module collapses the storm in three deterministic steps:

  1. Normalize. Accepts the platform's native shape and the Prometheus
     Alertmanager webhook (v4) as-is, so an existing Alertmanager can point
     a receiver at POST /v1/alerts with no adapter.
  2. Deduplicate by fingerprint. An alert's identity is sha256 over
     (source, service, name). A re-fire of the same identity bumps a counter
     and `last_seen` instead of creating a new alert.
  3. Correlate by topology and time. A new alert joins an open group when
     it fired within the correlation window AND sits on the group's causal
     path — the same service as, a caller of, or a dependency of the group's
     probable origin in the service graph. Otherwise it opens a new group.

The probable origin of a group is its most upstream alerting service: the
one none of whose own dependencies are alerting. Callers page because the
thing they call is broken; the origin is where to start investigating.

Everything here is rules over declared topology — no model, no clustering
heuristics to tune. That is deliberate: the grouping an operator sees must be
explainable in one sentence per alert (each alert records WHY it joined).

In-memory and bounded by design. The store is the reference mirror of the
Kafka-fed Sense plane (docs/01 §1); durability belongs to the incidents the
groups become, not to the raw signal.
"""
from __future__ import annotations

import hashlib
import threading
import time
from calendar import timegm
from dataclasses import dataclass, field

from aetherops.core.types import new_id

CORRELATION_WINDOW_S = 900          # look-back for joining an open group
MAX_ALERTS_PER_REQUEST = 200
MAX_GROUPS_RETAINED = 200
MAX_FIELD_CHARS = 200
MAX_LABELS = 32

SEVERITIES = ("info", "warning", "high", "critical")
_SEVERITY_ALIASES = {
    "page": "critical", "crit": "critical", "error": "high", "major": "high",
    "warn": "warning", "minor": "warning", "none": "info", "low": "info",
    "notice": "info",
}


class AlertValidationError(ValueError):
    """A payload the intake refuses — surfaced to the caller as HTTP 400."""


def _text(value, name: str, required: bool = True) -> str:
    if value is None or value == "":
        if required:
            raise AlertValidationError(f"alert is missing {name!r}")
        return ""
    if not isinstance(value, (str, int, float)):
        raise AlertValidationError(f"alert field {name!r} must be a string")
    return str(value).strip()[:MAX_FIELD_CHARS]


def _severity(value) -> str:
    severity = str(value or "warning").strip().lower()
    severity = _SEVERITY_ALIASES.get(severity, severity)
    return severity if severity in SEVERITIES else "warning"


def _timestamp(value, default: float) -> float:
    """Epoch seconds from a number or an RFC-3339 string; `default` when the
    field is absent or unparseable (an alert with a bad clock still counts)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str) and value:
        try:
            return float(timegm(time.strptime(value[:19],
                                              "%Y-%m-%dT%H:%M:%S")))
        except ValueError:
            return default
    return default


def _labels(value) -> dict:
    if not isinstance(value, dict):
        return {}
    return {str(k)[:64]: str(v)[:MAX_FIELD_CHARS]
            for k, v in list(value.items())[:MAX_LABELS]}


def _from_alertmanager(item: dict, now: float) -> dict:
    labels = _labels(item.get("labels"))
    annotations = item.get("annotations")
    annotations = annotations if isinstance(annotations, dict) else {}
    return {
        "source": "alertmanager",
        "name": _text(labels.get("alertname"), "labels.alertname"),
        "service": _text(labels.get("service") or labels.get("job"),
                         "labels.service"),
        "severity": _severity(labels.get("severity")),
        "status": "resolved" if item.get("status") == "resolved"
        else "firing",
        "summary": _text(annotations.get("summary")
                         or annotations.get("description"), "summary",
                         required=False),
        "labels": labels,
        "ts": _timestamp(item.get("startsAt"), now),
    }


def _from_native(item: dict, now: float) -> dict:
    return {
        "source": _text(item.get("source") or "api", "source"),
        "name": _text(item.get("name") or item.get("title"), "name"),
        "service": _text(item.get("service"), "service"),
        "severity": _severity(item.get("severity")),
        "status": "resolved" if item.get("status") == "resolved"
        else "firing",
        "summary": _text(item.get("summary"), "summary", required=False),
        "labels": _labels(item.get("labels")),
        "ts": _timestamp(item.get("ts"), now),
    }


def normalize(payload: dict, now: float | None = None) -> list[dict]:
    """One request body -> a list of normalized alerts. Accepts a single
    native alert, a native batch ({"alerts": [...]}), or an Alertmanager
    webhook. Raises AlertValidationError on anything else."""
    now = time.time() if now is None else now
    if not isinstance(payload, dict) or not payload:
        raise AlertValidationError("expected a JSON object describing one "
                                   "alert or {'alerts': [...]}")
    items = payload.get("alerts")
    if items is None:
        return [_from_native(payload, now)]
    if not isinstance(items, list) or not items:
        raise AlertValidationError("'alerts' must be a non-empty list")
    if len(items) > MAX_ALERTS_PER_REQUEST:
        raise AlertValidationError(
            f"at most {MAX_ALERTS_PER_REQUEST} alerts per request")
    out = []
    for item in items:
        if not isinstance(item, dict):
            raise AlertValidationError("each alert must be a JSON object")
        # Alertmanager nests identity under `labels.alertname`.
        if isinstance(item.get("labels"), dict) and \
                "alertname" in item["labels"]:
            out.append(_from_alertmanager(item, now))
        else:
            out.append(_from_native(item, now))
    return out


def fingerprint(alert: dict) -> str:
    identity = "\x1f".join((alert["source"], alert["service"],
                            alert["name"])).lower()
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


@dataclass
class Alert:
    id: str
    fingerprint: str
    source: str
    name: str
    service: str
    severity: str
    status: str
    summary: str
    labels: dict
    first_seen: float
    last_seen: float
    count: int = 1                  # raw events folded into this alert
    group_id: str = ""
    joined_because: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "fingerprint": self.fingerprint,
                "source": self.source, "name": self.name,
                "service": self.service, "severity": self.severity,
                "status": self.status, "summary": self.summary,
                "labels": self.labels, "first_seen": self.first_seen,
                "last_seen": self.last_seen, "count": self.count,
                "joined_because": self.joined_because}


@dataclass
class AlertGroup:
    id: str
    alerts: list = field(default_factory=list)      # list[Alert]
    origin: str = ""
    incident_id: str | None = None

    @property
    def services(self) -> list[str]:
        return sorted({a.service for a in self.alerts})

    @property
    def first_seen(self) -> float:
        return min(a.first_seen for a in self.alerts)

    @property
    def last_seen(self) -> float:
        return max(a.last_seen for a in self.alerts)

    @property
    def events(self) -> int:
        return sum(a.count for a in self.alerts)

    @property
    def severity(self) -> str:
        return max((a.severity for a in self.alerts),
                   key=SEVERITIES.index)

    @property
    def status(self) -> str:
        if self.incident_id:
            return "investigating"
        if all(a.status == "resolved" for a in self.alerts):
            return "resolved"
        return "open"


class AlertStore:
    def __init__(self, graph, clock=time.time,
                 window_s: float = CORRELATION_WINDOW_S):
        self._graph = graph
        self._clock = clock
        self._window = window_s
        self._lock = threading.Lock()
        self._groups: dict[str, AlertGroup] = {}        # insertion-ordered
        self._by_fingerprint: dict[str, Alert] = {}     # open alerts only
        self._received = 0
        self._deduplicated = 0

    # ----------------------------------------------------------- topology
    def _relation(self, service: str, origin: str) -> str | None:
        """Why `service` belongs on the causal path through `origin`."""
        if service == origin:
            return f"same service as origin {origin}"
        if not (self._graph.knows(service) and self._graph.knows(origin)):
            return None             # unknown topology: identity only
        if service in self._graph.dependents(origin):
            return f"calls {origin} (downstream symptom)"
        if service in self._graph.dependencies(origin):
            return f"dependency of {origin} (upstream cause candidate)"
        return None

    def _origin(self, group: AlertGroup) -> str:
        """Most upstream alerting service: none of its own dependencies are
        alerting. Ties break on earliest first alert, then name."""
        alerting = set(group.services)
        roots = [s for s in alerting
                 if not (self._graph.knows(s)
                         and self._graph.dependencies(s) & alerting)]
        candidates = roots or sorted(alerting)

        def first_alert(service: str) -> float:
            return min(a.first_seen for a in group.alerts
                       if a.service == service)

        return min(candidates, key=lambda s: (first_alert(s), s))

    # -------------------------------------------------------------- intake
    def ingest(self, alerts: list[dict]) -> dict:
        """Fold normalized alerts into the store. Returns what happened to
        this batch so a caller can see dedup and correlation working."""
        outcome = {"received": len(alerts), "deduplicated": 0, "resolved": 0,
                   "new_alerts": 0, "new_groups": 0, "groups": []}
        touched: list[str] = []
        with self._lock:
            for item in alerts:
                self._received += 1
                key = fingerprint(item)
                existing = self._by_fingerprint.get(key)

                if item["status"] == "resolved":
                    if existing is not None:
                        existing.status = "resolved"
                        existing.last_seen = max(existing.last_seen,
                                                 item["ts"])
                        del self._by_fingerprint[key]
                        touched.append(existing.group_id)
                    outcome["resolved"] += 1
                    continue

                if existing is not None:            # same identity re-fired
                    existing.count += 1
                    existing.last_seen = max(existing.last_seen, item["ts"])
                    if SEVERITIES.index(item["severity"]) > \
                            SEVERITIES.index(existing.severity):
                        existing.severity = item["severity"]
                    self._deduplicated += 1
                    outcome["deduplicated"] += 1
                    touched.append(existing.group_id)
                    continue

                alert = Alert(
                    id=new_id("alt"), fingerprint=key, source=item["source"],
                    name=item["name"], service=item["service"],
                    severity=item["severity"], status="firing",
                    summary=item["summary"], labels=item["labels"],
                    first_seen=item["ts"], last_seen=item["ts"])
                group, reason = self._match(alert)
                if group is None:
                    group = AlertGroup(id=new_id("grp"))
                    self._groups[group.id] = group
                    reason = "first alert in the group"
                    outcome["new_groups"] += 1
                alert.group_id, alert.joined_because = group.id, reason
                group.alerts.append(alert)
                group.origin = self._origin(group)
                self._by_fingerprint[key] = alert
                outcome["new_alerts"] += 1
                touched.append(group.id)
            self._evict()
            for group_id in dict.fromkeys(touched):
                if group_id in self._groups:
                    outcome["groups"].append(group_id)
        return outcome

    def _match(self, alert: Alert) -> tuple[AlertGroup | None, str]:
        """Most recently active open group this alert correlates with."""
        for group in sorted(self._groups.values(),
                            key=lambda g: g.last_seen, reverse=True):
            if group.status == "resolved":
                continue
            if abs(alert.first_seen - group.last_seen) > self._window:
                continue
            reason = self._relation(alert.service, group.origin)
            if reason is not None:
                return group, reason
        return None, ""

    def _evict(self) -> None:
        while len(self._groups) > MAX_GROUPS_RETAINED:
            victim = next((g for g in self._groups.values()
                           if g.status == "resolved"),
                          next(iter(self._groups.values())))
            for alert in victim.alerts:
                self._by_fingerprint.pop(alert.fingerprint, None)
            del self._groups[victim.id]

    # --------------------------------------------------------------- reads
    def _group_dict(self, group: AlertGroup) -> dict:
        lead = max(group.alerts,
                   key=lambda a: (a.service == group.origin,
                                  SEVERITIES.index(a.severity),
                                  -a.first_seen))
        return {
            "id": group.id, "title": lead.name, "origin": group.origin,
            "services": group.services, "severity": group.severity,
            "status": group.status, "incident_id": group.incident_id,
            "first_seen": group.first_seen, "last_seen": group.last_seen,
            "events": group.events, "unique_alerts": len(group.alerts),
            "sources": sorted({a.source for a in group.alerts}),
            "alerts": [a.to_dict() for a in sorted(
                group.alerts, key=lambda a: a.first_seen)],
        }

    def groups(self) -> list[dict]:
        with self._lock:
            return [self._group_dict(g) for g in sorted(
                self._groups.values(), key=lambda g: g.last_seen,
                reverse=True)]

    def group(self, group_id: str) -> dict | None:
        with self._lock:
            group = self._groups.get(group_id)
            return self._group_dict(group) if group else None

    def attach_incident(self, group_id: str, incident_id: str) -> bool:
        """Claim a group for an incident. False when it is already claimed —
        two operators must not open two incidents for one group."""
        with self._lock:
            group = self._groups.get(group_id)
            if group is None or group.incident_id:
                return False
            group.incident_id = incident_id
            return True

    def stats(self) -> dict:
        with self._lock:
            unique = sum(len(g.alerts) for g in self._groups.values())
            groups = len(self._groups)
            received = self._received
            return {
                "received": received,
                "deduplicated": self._deduplicated,
                "unique_alerts": unique,
                "groups": groups,
                "open_groups": sum(g.status == "open"
                                   for g in self._groups.values()),
                # Share of raw events an operator no longer has to look at.
                "noise_reduction": round(1 - groups / received, 3)
                if received else 0.0,
                "window_s": self._window,
            }

    def by_service(self) -> dict[str, dict]:
        """Open alert pressure per service — the topology view's overlay."""
        with self._lock:
            out: dict[str, dict] = {}
            for group in self._groups.values():
                if group.status == "resolved":
                    continue
                for alert in group.alerts:
                    if alert.status != "firing":
                        continue
                    slot = out.setdefault(alert.service, {
                        "alerts": 0, "severity": "info", "origin": False})
                    slot["alerts"] += 1
                    if SEVERITIES.index(alert.severity) > \
                            SEVERITIES.index(slot["severity"]):
                        slot["severity"] = alert.severity
                    if group.origin == alert.service:
                        slot["origin"] = True
            return out

    def clear(self) -> None:
        with self._lock:
            self._groups.clear()
            self._by_fingerprint.clear()
            self._received = 0
            self._deduplicated = 0
