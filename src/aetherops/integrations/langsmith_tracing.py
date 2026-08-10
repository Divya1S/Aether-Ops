"""LangSmith tracing for the model gateway (optional extra).

The stdlib gateway already meters every call (latency, tokens, est cost) into
the hash-chained audit ledger — the zero-dependency default. This adds
LangSmith distributed tracing on top: it instruments the gateway's single
choke point (`ModelGateway.complete`) so each model call becomes a nested run
you can inspect in the LangSmith UI, with the routed tier/model, token counts,
and estimated cost attached.

    pip install "aetherops[tracing]"
    export LANGCHAIN_TRACING_V2=true LANGCHAIN_API_KEY=ls-...   # free tier
    # ... build an environment, then:
    from aetherops.integrations.langsmith_tracing import trace_gateway
    trace_gateway(env["gateway"])

It is OPT-IN and transparent: langsmith only ships traces when
LANGCHAIN_TRACING_V2 is set (otherwise `@traceable` is a passthrough), and the
core never imports this — so the offline, network-free default is unchanged.
"""
from __future__ import annotations


def trace_gateway(gateway, *, project_name: str = "aetherops"):
    """Instrument a ModelGateway so each `.complete()` is a LangSmith run.

    Idempotent and transparent — returns the same gateway with its `complete`
    method wrapped. The completion result is returned unchanged; the metered
    fields (tier, model, tokens, est cost) are attached to the run as metadata.
    """
    from langsmith import traceable

    original = gateway.complete
    if getattr(original, "__aetherops_traced__", False):
        return gateway                                   # already instrumented

    @traceable(run_type="llm", name="ModelGateway.complete",
               project_name=project_name)
    def traced(prompt, profile):
        response = original(prompt, profile)
        _annotate(response)
        return response

    traced.__aetherops_traced__ = True
    gateway.complete = traced
    return gateway


def _annotate(response) -> None:
    """Attach the gateway's metered fields to the active LangSmith run so the
    trace carries the routed tier/model, token split, and estimated cost."""
    try:
        from langsmith.run_helpers import get_current_run_tree
    except Exception:
        return
    run = get_current_run_tree()
    if run is None:
        return
    run.extra.setdefault("metadata", {}).update({
        "tier": response.tier,
        "model_id": response.model_id,
        "backend": response.backend,
        "served_model": response.served_model,
        "tokens_in": response.tokens_in,
        "tokens_out": response.tokens_out,
        "latency_ms": response.latency_ms,
        "est_cost_usd": response.est_cost_usd,
    })
