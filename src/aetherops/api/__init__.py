"""REST API over the existing workflows (stdlib http.server).

Deliberate deviation from docs/17's FastAPI suggestion, recorded in
PROMPT-09: stdlib keeps the everything-runs-anywhere guarantee and full CI
coverage; FastAPI remains the documented production choice (docs/12) and
the `aetherops[api]` optional extra.
"""
