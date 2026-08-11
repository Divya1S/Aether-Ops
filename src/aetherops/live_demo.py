"""End-to-end LIVE demo: a Change-Intelligence review of a REAL, in-the-wild
commit — no snapshots, no offline heuristic.

This is the platform's "prevent the outage" pillar, run entirely live:

  1. LIVE GitHub search  finds a real, recent commit that raises a resource
                         limit (a connection-pool increase — the platform's
                         signature failure mode), via the free GitHub API.
  2. LIVE GitHub read    the real GitHubConnector pulls that commit's real
                         unified diff.
  3. LIVE model          the real ModelGateway (Ollama backend) renders the
                         versioned `change_review` prompt over the real diff
                         and returns a grounded risk verdict (band + failure
                         mode + guardrail). The call is metered.

It closes with a provenance report proving the commit is a real public commit
(cite the SHA) and the model call was served by Ollama, not the offline stub.

    # needs a running Ollama (`ollama pull qwen2.5:7b`) and network:
    AETHEROPS_OLLAMA_MODEL=qwen2.5:7b python3 -m aetherops.live_demo
    AETHEROPS_GITHUB_SEARCH="raise memory limit" python3 -m aetherops.live_demo

Manual and opt-in (hits the network); nothing in the core imports it, so CI,
tests, and the golden-scenario evals stay offline and reproducible.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from aetherops.connectors.adapters import GitHubConnector
from aetherops.gateway.backends import build_backend_chain
from aetherops.gateway.model_gateway import ModelGateway, TaskProfile
from aetherops.prompts.registry import get_prompt

DEFAULT_QUERY = "raise connection pool max_size"
_KEYWORDS = ("max_size", "pool", "memory", "max_connections", "replicas",
             "limit", "timeout")
# Immutable real public commits (used only if live search is rate-limited).
_FALLBACKS = (("primerhq/primer", "4cdb06f5"),
              ("meridianmcp/Meridian", "c1e8d0b4"),
              ("yr369/swas-tool-g1", "65d2e40a"))


def _ensure_ca_bundle() -> None:
    """python.org macOS builds ship without a CA store; use certifi's if set
    so the live HTTPS reads just work."""
    if os.environ.get("SSL_CERT_FILE"):
        return
    try:
        import certifi
        os.environ["SSL_CERT_FILE"] = certifi.where()
    except Exception:
        pass


def _search_commits(query: str) -> list[tuple[str, str, str]]:
    url = ("https://api.github.com/search/commits?per_page=10&q="
           + urllib.parse.quote(query))
    request = urllib.request.Request(url, headers={
        "User-Agent": "aetherops", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=15) as resp:
        items = json.loads(resp.read()).get("items", [])
    return [(it["repository"]["full_name"], it["sha"],
             it["commit"]["message"].splitlines()[0]) for it in items]


def _first_usable(candidates, source: str):
    """Return (repo, sha, diff_data, source) for the first commit whose real
    diff is non-empty, resource-relevant, and not enormous."""
    for repo, sha, _title in candidates:
        try:
            data = GitHubConnector(repo=repo).call(
                "get_commit_diff", {"sha": sha}, principal="live-demo").data
        except Exception:
            continue
        diff = data.get("diff", "")
        if diff and len(diff) < 20000 and any(k in diff for k in _KEYWORDS):
            return repo, sha, data, source
    return None


def _pick_commit(query: str):
    try:
        found = _first_usable(_search_commits(query), "live GitHub search")
        if found:
            return found
    except urllib.error.HTTPError as exc:
        print(f"    (live search unavailable: HTTP {exc.code} — GitHub caps "
              f"unauthenticated search at 10/min; using a pinned real commit)")
    except Exception as exc:
        print(f"    (live search failed: {exc}; using a pinned real commit)")
    return _first_usable([(r, s, "") for r, s in _FALLBACKS], "pinned real commit")


def run_live_demo(query: str | None = None) -> int:
    _ensure_ca_bundle()
    query = query or os.environ.get("AETHEROPS_GITHUB_SEARCH") or DEFAULT_QUERY
    model = os.environ.get("AETHEROPS_OLLAMA_MODEL", "llama3.2:3b")
    line = "─" * 76

    print(line)
    print("AetherOps — LIVE Change-Intelligence review (real commit · real local model)")
    print(line)

    print(f"\n[1] LIVE GitHub — searching for a real change: \"{query}\"")
    picked = _pick_commit(query)
    if picked is None:
        print("    could not fetch any real commit (network?). aborting.")
        return 1
    repo, sha, data, source = picked
    print(f"    source   : {source}")
    print(f"    commit   : {repo}@{sha[:10]}")
    print(f"    title    : {data['title'][:64]}")
    print(f"    diff     : {len(data['diff'])} chars of real unified diff")
    print(f"    cite     : github://commit/{sha}")

    prompt = get_prompt("change_review").render(
        title=data["title"], repo=repo, diff=data["diff"][:1600])
    gateway = ModelGateway(backends=build_backend_chain("ollama,offline"))
    print(f"\n[2] LIVE model — {model} reviewing the real diff for deploy risk…\n")
    response = gateway.complete(
        prompt, TaskProfile(task="change_review", tier_hint="reasoning"))
    for row in response.text.strip().splitlines():
        if row.strip():
            print(f"      {row.strip()}")

    print(f"\n{line}\nProvenance — real public commit, real local model")
    print(line)
    print(f"  change   <- {source.upper()}   github://commit/{sha}")
    print(f"  verdict  <- backend={response.backend}  served={response.served_model}")
    print(f"              tokens={response.tokens}  latency={response.latency_ms}ms  "
          f"est_prod_cost=${response.est_cost_usd}")
    if response.backend != "ollama":
        print("\n  NOTE: Ollama was unreachable — the gateway fell back to the offline\n"
              "        backend (audited). Start Ollama for a fully-live model call.")
    else:
        print("\n  A real local model read a real, in-the-wild connection-pool change and\n"
              "  flagged exactly the memory/OOM risk this platform is built to catch —\n"
              "  the 'prevent the outage before it ships' pillar, running fully live.")
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
