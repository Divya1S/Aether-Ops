"""End-to-end LIVE demo: a real incident analysis driven entirely by live
external data and a live local model — no snapshots, no offline heuristic.

Three genuinely live sources, wired through the platform's *real* components:

  1. LIVE GitHub      the real GitHubConnector reads a recent commit + its real
                      unified diff from the free GitHub REST API.
  2. LIVE Prometheus  the real PrometheusConnector runs a p99-latency range
                      query against a real Prometheus and maps the samples.
  3. LIVE model       the real ModelGateway (Ollama backend) renders the real
                      `root_cause` prompt from the versioned registry over the
                      live evidence and generates a grounded hypothesis.

Everything is metered and cited exactly as in production; the closing
provenance report shows, per evidence item, that the data came from a live
system — and that the model call was served by Ollama, not the offline stub.

    # needs a running Ollama (`ollama pull qwen2.5:7b`) and network:
    AETHEROPS_OLLAMA_MODEL=qwen2.5:7b python3 -m aetherops.live_demo
    # override the sources:
    AETHEROPS_GITHUB_REPO=owner/repo AETHEROPS_PROMETHEUS_URL=https://... \
        python3 -m aetherops.live_demo

It is manual and opt-in (hits the network); nothing in the core imports it, so
CI, tests, and the golden-scenario evals stay offline and reproducible.
"""
from __future__ import annotations

import json
import os
import urllib.request

from aetherops.connectors.adapters import GitHubConnector, PrometheusConnector
from aetherops.gateway.backends import build_backend_chain
from aetherops.gateway.model_gateway import ModelGateway, TaskProfile
from aetherops.prompts.registry import get_prompt

DEFAULT_REPO = "Divya1S/Aether-Ops"
DEFAULT_PROM = "https://prometheus.demo.prometheus.io"
DEFAULT_QUERY = ("histogram_quantile(0.99, sum by (le) "
                 "(rate(prometheus_http_request_duration_seconds_bucket[5m])))")


def _ensure_ca_bundle() -> None:
    """python.org macOS builds ship without a CA store; use certifi's if the
    system trust store is missing, so the live HTTPS reads just work."""
    if os.environ.get("SSL_CERT_FILE"):
        return
    try:
        import certifi
        os.environ["SSL_CERT_FILE"] = certifi.where()
    except Exception:
        pass


def _latest_two_shas(repo: str) -> list[dict]:
    url = f"https://api.github.com/repos/{repo}/commits?per_page=2"
    request = urllib.request.Request(url, headers={"User-Agent": "aetherops",
                                                   "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=15) as resp:
        return json.loads(resp.read())


def run_live_demo(repo: str | None = None, prom_url: str | None = None,
                  prom_query: str | None = None) -> int:
    _ensure_ca_bundle()
    repo = repo or os.environ.get("AETHEROPS_GITHUB_REPO") or DEFAULT_REPO
    prom_url = prom_url or os.environ.get("AETHEROPS_PROMETHEUS_URL") or DEFAULT_PROM
    prom_query = prom_query or os.environ.get("AETHEROPS_PROMETHEUS_QUERY") or DEFAULT_QUERY
    model = os.environ.get("AETHEROPS_OLLAMA_MODEL", "llama3.2:3b")

    line = "─" * 74
    print(line)
    print("AetherOps — END-TO-END LIVE run (live GitHub · live Prometheus · live model)")
    print(line)

    # 1) LIVE GitHub -----------------------------------------------------------
    commits = _latest_two_shas(repo)
    sha = commits[0]["sha"]
    prev = commits[1]["sha"] if len(commits) > 1 else ""
    github = GitHubConnector(repo=repo)
    diff = github.call("get_commit_diff", {"sha": sha}, principal="live-demo")
    change = diff.data
    print(f"\n[1] LIVE GitHub  ({repo})")
    print(f"    change   : {sha[:10]}  \"{change['title'][:58]}\"  by {change['author']}")
    print(f"    diff     : {len(change['diff'])} chars of real unified diff")
    print(f"    cite     : {diff.citation.ref}")

    # 2) LIVE Prometheus -------------------------------------------------------
    prom = PrometheusConnector(base_url=prom_url, query=prom_query)
    metrics = prom.call("query_metrics", {"query": prom_query}, principal="live-demo")
    series = metrics.data["series"]
    p99s = [s["p99_ms"] for s in series]
    print(f"\n[2] LIVE Prometheus  ({prom_url})")
    print(f"    query    : {prom_query[:60]}")
    print(f"    p99 (ms) : {p99s}  ({len(series)} live samples)")
    print(f"    cite     : {metrics.citation.ref}")

    # 3) LIVE model: real root_cause prompt over the live evidence --------------
    digest = "\n".join([
        f"[E1] (metrics/prometheus) p99 latency, last 15m: "
        f"{', '.join(f'{v}ms' for v in p99s) or 'no samples'} "
        f"[{metrics.citation.ref}]",
        f"[E2] (deploy/github) most recent change {sha[:10]} "
        f"\"{change['title']}\" by {change['author']}"
        + (f"; previous {prev[:10]}" if prev else "")
        + f" [{diff.citation.ref}]",
        f"[E3] (commit-diff/github) diff of {sha[:10]}:\n{change['diff'][:900]}",
    ])
    prompt = get_prompt("root_cause").render(
        title=f"{repo}: p99 latency vs. most recent change", digest=digest)

    gateway = ModelGateway(backends=build_backend_chain("ollama,offline"))
    print(f"\n[3] LIVE model  (Ollama · {model})  — reasoning over the live evidence…")
    response = gateway.complete(prompt, TaskProfile(task="root_cause",
                                                    tier_hint="reasoning"))
    print(f"\n    hypothesis (generated live):\n")
    for para in response.text.strip().splitlines():
        print(f"      {para}")

    # 4) provenance ------------------------------------------------------------
    print(f"\n{line}\nProvenance — every input was live, the model was live")
    print(line)
    print(f"  evidence E1  <- LIVE Prometheus   {metrics.citation.ref}")
    print(f"  evidence E2  <- LIVE GitHub       {diff.citation.ref}")
    print(f"  evidence E3  <- LIVE GitHub       diff of {sha[:10]}")
    print(f"  model call   <- backend={response.backend}  served={response.served_model}")
    print(f"                  tokens={response.tokens}  latency={response.latency_ms}ms  "
          f"est_prod_cost=${response.est_cost_usd}")
    if response.backend != "ollama":
        print("\n  NOTE: Ollama was unreachable — the gateway fell back to the offline\n"
              "        backend (audited). Start Ollama for a fully-live model call.")
    else:
        print("\n  The hypothesis was produced by a real local model reading real live\n"
              "  data. On unscripted data the grounded answer is often 'unclassified'\n"
              "  (no change actually correlates with the metric) — that is the\n"
              "  grounding working, not a failure.")
    return 0


def main() -> int:
    try:
        return run_live_demo()
    except Exception as exc:  # a live demo depends on the network + Ollama
        print(f"\nlive demo could not complete: {exc}")
        print("checklist: network reachable? Ollama running "
              "(`ollama pull qwen2.5:7b`)? AETHEROPS_OLLAMA_MODEL set?")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
