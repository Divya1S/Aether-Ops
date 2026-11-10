"""Record the console's DEMO transcript from the real API.

    make record-demo        # writes web/src/demo-data.json

The public demo (GitHub Pages) has no backend, so the console replays a
transcript instead. This script produces that transcript by booting the
actual stdlib API in-process and driving it over HTTP — every document the
demo shows is a response the server really returned, never hand-written
JSON. Re-run it whenever the API's shapes change; `make web` then bakes the
result into the single-file build.

Each golden scenario is run to its natural end (approval gate or
escalation). Scenarios that pause are then resolved both ways: the recorded
incident is approved, and a second run of the same scenario is denied — two
genuine runs, so the denied document carries its own incident id.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from aetherops import __version__                   # noqa: E402
from aetherops.api import server as api_server      # noqa: E402

TOKEN = "aetherops-dev"
OUT = ROOT / "web" / "src" / "demo-data.json"
STORM = json.loads((ROOT / "web" / "src" / "storm.json").read_text())

RUNBOOK_QUERIES = [
    "OOMKilled pods after deploy",
    "TLS certificate expired handshake failures",
    "roll back a bad deployment",
    "p99 latency spike what changed",
]
CHANGES = {
    "risky": {"service": "orders-service", "sha": "b7e21c9",
              "title": "Raise DB connection pool max_size 25 -> 250",
              "diff": "- max_size: 25\n+ max_size: 250",
              "peak_window": True, "freeze": False},
    "benign": {"service": "orders-service", "sha": "a11c3f0",
               "title": "Update README copy", "diff": "- old\n+ new",
               "peak_window": True, "freeze": False},
}


def main() -> int:
    for var in ("AETHEROPS_DB", "AETHEROPS_BACKENDS"):
        os.environ.pop(var, None)       # in-memory, offline: reproducible
    api_server.STATE = api_server.AppState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), api_server.Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def call(path: str, method: str = "GET", body: dict | None = None):
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {TOKEN}"})
        try:
            with urllib.request.urlopen(request, timeout=120) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"{method} {path} -> {exc.code}: "
                             f"{exc.read().decode()[:300]}") from exc

    def strip(doc: dict) -> dict:
        doc = dict(doc)
        doc.pop("fence", None)          # a capability token, not content
        return doc

    data: dict = {
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "version": __version__,
        "whoami": call("/v1/whoami"),
        "scenarios": call("/v1/scenarios")["scenarios"],
        "catalog": call("/v1/catalog"),
        "connectors": call("/v1/connectors")["connectors"],
        "openapi": call("/v1/openapi.json"),
        "evals": call("/v1/evals"),
        "runbooks": {
            query: call("/v1/runbooks/search?q="
                        + urllib.request.quote(query))["results"]
            for query in RUNBOOK_QUERIES},
        "changes": {kind: call("/v1/changes/score", "POST", body)
                    for kind, body in CHANGES.items()},
    }

    call("/v1/alerts", "DELETE")
    data["alerts"] = {"empty": call("/v1/alerts")}
    data["topology"] = {"idle": call("/v1/topology")}
    call("/v1/alerts", "POST", STORM)
    data["alerts"]["storm"] = call("/v1/alerts")
    data["topology"]["storm"] = call("/v1/topology")

    runs: dict = {}
    for scenario in data["scenarios"]:
        created = call("/v1/incidents", "POST", {"scenario": scenario["id"]})
        incident_id = created["incident_id"]
        run = {"created": strip(created),
               "audit_created": call(f"/v1/incidents/{incident_id}/audit")}
        if created["status"] == "PAUSED":
            approved = call(f"/v1/incidents/{incident_id}/approvals", "POST",
                            {"decision": "approve",
                             "fence": created["fence"]})
            run["approved"] = strip(approved)
            run["audit_approved"] = call(
                f"/v1/incidents/{incident_id}/audit")
            run["postmortem"] = call(
                f"/v1/incidents/{incident_id}/postmortem")

            second = call("/v1/incidents", "POST",
                          {"scenario": scenario["id"]})
            denied = call(f"/v1/incidents/{second['incident_id']}/approvals",
                          "POST", {"decision": "deny",
                                   "fence": second["fence"]})
            run["denied"] = strip(denied)
            run["audit_denied"] = call(
                f"/v1/incidents/{second['incident_id']}/audit")
        runs[scenario["id"]] = run
        print(f"  {scenario['id']:<26} {created['outcome']:<18}"
              f"{' + approved, denied' if 'approved' in run else ''}")
    data["runs"] = runs

    server.shutdown()
    server.server_close()

    OUT.write_text(json.dumps(data, separators=(",", ":"), sort_keys=True)
                   + "\n", encoding="utf-8")
    print(f"recorded {len(runs)} scenarios -> {OUT.relative_to(ROOT)} "
          f"({OUT.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
