"""The API surface behind the operator console (docs/20): the incident
document, alert intake, reporting, the route table's access control, and the
transport hardening (headers, CSP, rate limiting, body handling)."""
import base64
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from aetherops.api import server as api_server

ADMIN = "aetherops-dev"
STORM = {"alerts": [
    {"source": "prometheus", "name": "HighP99Latency",
     "service": "checkout-service", "severity": "critical"},
    {"source": "prometheus", "name": "HighP99Latency",
     "service": "checkout-service", "severity": "critical"},
    {"source": "prometheus", "name": "PodOOMKilled",
     "service": "checkout-service", "severity": "high"},
    {"source": "datadog", "name": "High5xxRate",
     "service": "storefront-web", "severity": "high"},
    {"source": "prometheus", "name": "TLSHandshakeErrors",
     "service": "payments-gateway", "severity": "critical"},
    {"source": "prometheus", "name": "QueueBacklog",
     "service": "orders-service", "severity": "warning"},
]}


class ApiCase(unittest.TestCase):
    """Boots an isolated API (fresh AppState) on an ephemeral port."""

    @classmethod
    def setUpClass(cls):
        api_server.STATE = api_server.AppState()
        api_server.STATE.tokens.update({
            "viewer-tok": "viewer", "operator-tok": "operator",
            "approver-tok": "approver", "ingest-tok": "ingest"})
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0),
                                         api_server.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever,
                         daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def raw(self, path, method="GET", body=None, token=ADMIN, data=None,
            headers=None):
        """(status, headers, parsed-or-text) — never raises on 4xx/5xx."""
        payload = data if data is not None else (
            json.dumps(body).encode() if body is not None else None)
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", method=method,
            data=payload,
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {token}"}
                        if token else {}), **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=60) as resp:
                status, hdrs, text = resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            status, hdrs, text = exc.code, exc.headers, exc.read()
        text = text.decode()
        try:
            return status, hdrs, json.loads(text)
        except json.JSONDecodeError:
            return status, hdrs, text

    def call(self, path, method="GET", body=None, token=ADMIN):
        status, _, payload = self.raw(path, method, body, token)
        return status, payload

    def create(self, scenario=None):
        status, incident = self.call(
            "/v1/incidents", "POST",
            {"scenario": scenario} if scenario else {})
        self.assertEqual(status, 201)
        return incident

    def approve(self, incident, decision="approve"):
        status, resolved = self.call(
            f"/v1/incidents/{incident['incident_id']}/approvals", "POST",
            {"decision": decision, "fence": incident["fence"]})
        self.assertEqual(status, 200)
        return resolved


class TestIncidentDocument(ApiCase):
    def test_document_carries_the_hypothesis_tree(self):
        incident = self.create()
        tree = incident["hypotheses"]
        self.assertEqual(tree["conclusion"], "validated")
        self.assertEqual(tree["validated"], "deploy-regression/memory")
        self.assertTrue(tree["pipeline"]["agrees"])
        self.assertEqual([h["status"] for h in tree["hypotheses"]],
                         ["VALIDATED", "INVALIDATED", "INVALIDATED"])

    def test_evidence_items_are_addressable_and_cited(self):
        incident = self.create()
        ids = [e["id"] for e in incident["evidence"]]
        self.assertEqual(ids, [f"E{n}" for n in range(1, len(ids) + 1)])
        for item in incident["evidence"]:
            self.assertTrue(item["ref"])
            self.assertTrue(item["excerpt"])
        # every [En] a hypothesis criterion cites resolves to a bundle item
        cited = {ref for h in incident["hypotheses"]["hypotheses"]
                 for c in h["criteria"] for ref in c["evidence"]}
        self.assertTrue(cited)
        self.assertLessEqual(cited, set(ids))

    def test_stages_follow_the_workflow_to_the_gate(self):
        incident = self.create()
        stages = {s["id"]: s for s in incident["stages"]}
        self.assertEqual(list(stages)[:4], ["triage", "gather_evidence",
                                            "security_screen", "diagnose"])
        self.assertEqual(stages["policy_check"]["state"], "done")
        self.assertEqual(stages["approval_gate"]["state"], "paused")
        self.assertEqual(stages["execute"]["state"], "skipped")
        self.assertIsInstance(stages["diagnose"]["duration_ms"], float)

        resolved = self.approve(incident)
        self.assertTrue(all(s["state"] == "done"
                            for s in resolved["stages"]))
        self.assertEqual(resolved["outcome"], "remediated")

    def test_escalation_shows_where_and_why_the_pipeline_stopped(self):
        incident = self.create("s5-reverted-pool")
        self.assertEqual(incident["outcome"], "escalated")
        stages = {s["id"]: s for s in incident["stages"]}
        self.assertEqual(stages["plan"]["state"], "failed")
        self.assertIn("escalate", stages["plan"]["error"])
        self.assertEqual(stages["approval_gate"]["state"], "skipped")
        self.assertEqual(incident["hypotheses"]["conclusion"],
                         "inconclusive")

    def test_reviewer_verdicts_survive_a_rejection(self):
        # The checks that stopped a wrong rollback must be readable, not
        # swallowed by the exception that reported them.
        incident = self.create("s6-deploy-after-breach")
        self.assertEqual(incident["outcome"], "escalated")
        failed = [c["name"] for c in incident["review_checks"]
                  if not c["passed"]]
        self.assertEqual(failed, ["temporal-precedence"])

    def test_timeline_is_the_ledger_humanized_in_order(self):
        incident = self.create()
        timeline = incident["timeline"]
        self.assertEqual(timeline[0]["title"], "Workflow started")
        self.assertEqual(timeline[0]["t_ms"], 0.0)
        self.assertEqual([e["seq"] for e in timeline],
                         list(range(len(timeline))))
        kinds = {e["kind"] for e in timeline}
        self.assertLessEqual({"tool", "model", "decision", "stage", "gate"},
                             kinds)
        self.assertEqual(timeline[-1]["title"], "Paused for approval")
        self.assertEqual(incident["audit"]["count"], len(timeline))
        self.assertTrue(incident["audit"]["chain_verified"])

    def test_signals_put_deploy_and_events_on_the_symptom_axis(self):
        incident = self.create()
        signals = incident["signals"]
        self.assertEqual([p["value"] for p in signals["series"]],
                         [181, 2140, 2412])
        self.assertEqual(signals["onset"], "14:05Z")
        self.assertEqual(signals["deploy"]["commit"], "c9a1f42")
        self.assertIn("OOMKilled", {e["reason"] for e in signals["events"]})
        self.assertNotIn("recovered", signals)

        resolved = self.approve(incident)
        self.assertTrue(resolved["signals"]["recovered"]["recovered"])
        self.assertLess(resolved["signals"]["recovered"]["value"], 300)

    def test_blast_radius_and_similar_incidents(self):
        incident = self.create()
        self.assertEqual(incident["blast_radius"], ["storefront-web"])
        self.assertTrue(incident["similar_incidents"])
        self.assertTrue(incident["similar_incidents"][0]["ref"]
                        .startswith("memory://episode/"))

    def test_planted_credential_never_reaches_the_document(self):
        # FakeDatadog plants api_key=dd-abc123def456 in monitor metadata;
        # structured evidence data must not smuggle it past redaction.
        incident = self.create()
        self.assertNotIn("dd-abc123def456", json.dumps(incident))

    def test_resolved_document_reports_execution_and_verification(self):
        resolved = self.approve(self.create())
        self.assertEqual([e["action"] for e in resolved["executed"]],
                         ["rollback_deployment", "create_revert_pr"])
        self.assertTrue(all(e["dry_run"] for e in resolved["executed"]))
        self.assertTrue(resolved["verification"]["recovered"])
        self.assertTrue(resolved["episode_id"].startswith("ep_"))
        self.assertTrue(resolved["postmortem_available"])


class TestHistoryAndReporting(ApiCase):
    def test_list_is_newest_first_and_filterable(self):
        first = self.create()
        second = self.create("s2-search-uncorrelated")
        status, body = self.call("/v1/incidents")
        self.assertEqual(status, 200)
        ids = [row["incident_id"] for row in body["incidents"]]
        self.assertLess(ids.index(second["incident_id"]),
                        ids.index(first["incident_id"]))
        row = next(r for r in body["incidents"]
                   if r["incident_id"] == second["incident_id"])
        self.assertEqual(row["outcome"], "escalated")
        self.assertIsNone(row["failure_class"])
        self.assertEqual(row["service"], "search-api")
        self.assertNotIn("timeline", row)               # list stays light

        _, escalated = self.call("/v1/incidents?outcome=escalated")
        self.assertTrue(escalated["incidents"])
        self.assertTrue(all(r["outcome"] == "escalated"
                            for r in escalated["incidents"]))
        _, limited = self.call("/v1/incidents?limit=1")
        self.assertEqual(len(limited["incidents"]), 1)

    def test_a_rejected_diagnosis_is_not_reported_as_the_diagnosis(self):
        # s6: the model diagnoses a deploy regression, the Reviewer rejects
        # it (the deploy landed after onset). The proposal stays on record,
        # but nothing may count it as what was wrong.
        rejected = self.create("s6-deploy-after-breach")
        self.assertEqual(rejected["diagnosis"]["status"], "diagnosed")
        self.assertEqual(rejected["diagnosis"]["failure_class"],
                         "deploy-regression/memory")
        self.assertFalse(rejected["diagnosis"]["standing"])

        standing = self.create()
        self.assertTrue(standing["diagnosis"]["standing"])
        denied = self.approve(self.create(), "deny")
        self.assertTrue(denied["diagnosis"]["standing"])    # plan denied,
                                                            # not diagnosis
        _, body = self.call("/v1/incidents")
        row = next(r for r in body["incidents"]
                   if r["incident_id"] == rejected["incident_id"])
        self.assertIsNone(row["failure_class"])
        self.assertIsNone(row["confidence"])

        _, stats = self.call("/v1/stats")
        counted = sum(stats["incidents"]["by_failure_class"].values())
        with_class = sum(r["failure_class"] is not None
                         for r in body["incidents"])
        self.assertEqual(counted, with_class)
        self.assertLess(stats["diagnosis_rate"], 1.0)

    def test_bad_limit_is_400(self):
        status, body = self.call("/v1/incidents?limit=lots")
        self.assertEqual(status, 400)
        self.assertIn("limit", body["error"])

    def test_postmortem_exists_only_after_verified_remediation(self):
        incident = self.create()
        path = f"/v1/incidents/{incident['incident_id']}/postmortem"
        self.assertEqual(self.call(path)[0], 404)
        self.approve(incident)
        status, postmortem = self.call(path)
        self.assertEqual(status, 200)
        self.assertTrue(postmortem["markdown"].startswith("# Postmortem"))
        self.assertIn("## Timeline", postmortem["markdown"])
        self.assertTrue(postmortem["follow_ups"])

    def test_stats_summarize_outcomes_and_cost(self):
        self.approve(self.create())
        self.create("s7-heap-oom")
        status, stats = self.call("/v1/stats")
        self.assertEqual(status, 200)
        outcomes = stats["incidents"]["by_outcome"]
        self.assertGreaterEqual(outcomes["remediated"], 1)
        self.assertGreaterEqual(outcomes["escalated"], 1)
        self.assertEqual(sum(outcomes.values()), stats["incidents"]["total"])
        self.assertGreater(stats["tokens"], 0)
        self.assertGreater(stats["mean_time_to_diagnosis_ms"], 0)
        self.assertIn("deploy-regression/memory",
                      stats["incidents"]["by_failure_class"])
        self.assertIn("noise_reduction", stats["alerts"])

    def test_topology_reports_blast_radius_for_a_focus_service(self):
        status, topology = self.call("/v1/topology?service=payments-service")
        self.assertEqual(status, 200)
        self.assertIn({"from": "checkout-service", "to": "payments-service"},
                      topology["edges"])
        self.assertEqual(topology["focus"]["blast_radius"],
                         ["checkout-service", "mobile-bff",
                          "orders-service", "storefront-web"])
        depth = {n["id"]: n["depth"] for n in topology["nodes"]}
        self.assertEqual(depth["payments-service"], 0)      # leaf
        self.assertGreater(depth["storefront-web"],
                           depth["checkout-service"])        # callers above

    def test_catalog_publishes_the_governed_surface(self):
        status, catalog = self.call("/v1/catalog")
        self.assertEqual(status, 200)
        actions = {s["action"]: s for s in catalog["step_catalog"]}
        self.assertEqual(actions["rollback_deployment"]["risk"], "HIGH")
        self.assertEqual(actions["rollback_deployment"]["required_args"],
                         ["service", "revision"])
        self.assertTrue(all(t["risk"] == "READ"
                            for t in catalog["investigation_tools"]))
        self.assertEqual(catalog["role_grants"]["approve"],
                         ["admin", "approver"])
        self.assertTrue(any(s["gate"] for s in catalog["stages"]))
        self.assertTrue(any(p.startswith("root_cause@")
                            for p in catalog["prompts"]))

    def test_evals_report_per_scenario_rows(self):
        status, evals = self.call("/v1/evals")
        self.assertEqual(status, 200)
        self.assertEqual(len(evals["scenarios"]), 7)
        self.assertTrue(all(row["outcome_correct"]
                            for row in evals["scenarios"]))
        self.assertTrue(evals["judge_gate"]["passed"])
        self.assertIn("strategies", evals["retrieval"])


class TestFeedback(ApiCase):
    def test_feedback_is_audited_and_reaches_memory(self):
        resolved = self.approve(self.create())
        incident_id = resolved["incident_id"]
        entry = api_server.STATE.incidents[incident_id]
        memory, episode_id = entry["env"]["memory"], resolved["episode_id"]
        self.assertTrue(memory.get(episode_id)["verified"])

        status, updated = self.call(
            f"/v1/incidents/{incident_id}/feedback", "POST",
            {"verdict": "incorrect", "note": "  real cause was a leak  "})
        self.assertEqual(status, 200)
        self.assertEqual(updated["feedback"]["verdict"], "incorrect")
        self.assertEqual(updated["feedback"]["note"],
                         "real cause was a leak")
        self.assertTrue(updated["feedback"]["memory_updated"])
        # A rejected diagnosis stops being precedent (audit H4)...
        episode = memory.get(episode_id)
        self.assertFalse(episode["verified"])
        self.assertEqual(episode["human_verdict"], "incorrect")

        # ...and the verdict is in the tamper-evident chain.
        _, audit = self.call(f"/v1/incidents/{incident_id}/audit")
        last = audit["records"][-1]
        self.assertEqual(last["action"], "human.feedback")
        self.assertEqual(last["actor"], "human:admin")
        self.assertTrue(audit["chain_verified"])
        self.assertEqual(updated["timeline"][-1]["kind"], "human")

        # Reversing the verdict restores what the verifier measured.
        self.call(f"/v1/incidents/{incident_id}/feedback", "POST",
                  {"verdict": "correct"})
        self.assertTrue(memory.get(episode_id)["verified"])

    def test_feedback_validation_and_access(self):
        incident = self.create()
        path = f"/v1/incidents/{incident['incident_id']}/feedback"
        self.assertEqual(self.call(path, "POST", {"verdict": "meh"})[0], 400)
        self.assertEqual(self.call(path, "POST", {"verdict": "correct",
                                                  "note": 7})[0], 400)
        self.assertEqual(self.call(path, "POST", {"verdict": "correct"},
                                   token="viewer-tok")[0], 403)
        self.assertEqual(self.call(path, "POST", {"verdict": "correct"},
                                   token="approver-tok")[0], 200)
        self.assertEqual(self.call("/v1/incidents/inc_nope/feedback", "POST",
                                   {"verdict": "correct"})[0], 404)

    def test_feedback_counts_in_stats(self):
        incident = self.create()
        self.call(f"/v1/incidents/{incident['incident_id']}/feedback",
                  "POST", {"verdict": "correct"})
        _, stats = self.call("/v1/stats")
        self.assertGreaterEqual(stats["feedback"]["correct"], 1)
        self.assertIsNotNone(stats["feedback"]["acceptance"])


class TestAlertFlow(ApiCase):
    def setUp(self):
        self.call("/v1/alerts", "DELETE")

    def test_storm_collapses_into_groups_and_one_becomes_an_incident(self):
        status, outcome = self.call("/v1/alerts", "POST", STORM)
        self.assertEqual(status, 202)
        self.assertEqual(outcome["received"], 6)
        self.assertEqual(outcome["deduplicated"], 1)
        self.assertEqual(outcome["new_groups"], 3)

        _, listing = self.call("/v1/alerts")
        groups = {g["origin"]: g for g in listing["groups"]}
        self.assertEqual(set(groups), {"checkout-service",
                                       "payments-gateway", "orders-service"})
        checkout = groups["checkout-service"]
        self.assertEqual(checkout["services"],
                         ["checkout-service", "storefront-web"])
        self.assertEqual(checkout["events"], 4)
        self.assertEqual(checkout["severity"], "critical")
        self.assertTrue(checkout["investigable"])
        self.assertFalse(groups["orders-service"]["investigable"])
        self.assertEqual(listing["stats"]["noise_reduction"], 0.5)

        status, incident = self.call(
            f"/v1/alerts/groups/{checkout['id']}/investigate", "POST", {})
        self.assertEqual(status, 201)
        self.assertEqual(incident["alert_group_id"], checkout["id"])
        self.assertEqual(incident["service"], "checkout-service")
        self.assertEqual(incident["status"], "PAUSED")

        _, listing = self.call("/v1/alerts")
        claimed = next(g for g in listing["groups"]
                       if g["id"] == checkout["id"])
        self.assertEqual(claimed["status"], "investigating")
        self.assertEqual(claimed["incident_id"], incident["incident_id"])

        # one group, one incident
        status, again = self.call(
            f"/v1/alerts/groups/{checkout['id']}/investigate", "POST", {})
        self.assertEqual(status, 409)
        self.assertEqual(again["incident_id"], incident["incident_id"])

    def test_a_different_group_diagnoses_a_different_failure_class(self):
        self.call("/v1/alerts", "POST", STORM)
        _, listing = self.call("/v1/alerts")
        gateway = next(g for g in listing["groups"]
                       if g["origin"] == "payments-gateway")
        _, incident = self.call(
            f"/v1/alerts/groups/{gateway['id']}/investigate", "POST", {})
        self.assertEqual(incident["diagnosis"]["failure_class"],
                         "cert-expiry/tls")

    def test_group_without_an_evidence_source_is_refused_honestly(self):
        self.call("/v1/alerts", "POST", STORM)
        _, listing = self.call("/v1/alerts")
        orders = next(g for g in listing["groups"]
                      if g["origin"] == "orders-service")
        before = len(api_server.STATE.incidents)
        status, body = self.call(
            f"/v1/alerts/groups/{orders['id']}/investigate", "POST", {})
        self.assertEqual(status, 422)
        self.assertIn("no evidence source", body["error"])
        self.assertEqual(len(api_server.STATE.incidents), before)

    def test_alertmanager_webhook_round_trip(self):
        status, outcome = self.call("/v1/alerts", "POST", {
            "version": "4", "receiver": "aetherops",
            "alerts": [{"status": "firing",
                        "labels": {"alertname": "KubePodCrashLooping",
                                   "service": "checkout-service",
                                   "severity": "page"},
                        "annotations": {"summary": "crash looping"}}]})
        self.assertEqual(status, 202)
        self.assertEqual(outcome["new_alerts"], 1)
        _, listing = self.call("/v1/alerts")
        self.assertEqual(listing["groups"][0]["sources"], ["alertmanager"])

    def test_invalid_alerts_are_400_and_store_nothing(self):
        for bad in ({}, {"name": "no service"}, {"alerts": []}):
            status, body = self.call("/v1/alerts", "POST", bad)
            self.assertEqual(status, 400, bad)
            self.assertIn("error", body)
        self.assertEqual(self.call("/v1/alerts")[1]["stats"]["received"], 0)

    def test_unknown_group_is_404(self):
        self.assertEqual(self.call("/v1/alerts/groups/grp_nope/investigate",
                                   "POST", {})[0], 404)

    def test_topology_overlays_alert_pressure(self):
        self.call("/v1/alerts", "POST", STORM)
        _, topology = self.call("/v1/topology")
        nodes = {n["id"]: n for n in topology["nodes"]}
        self.assertTrue(nodes["checkout-service"]["alerts"]["origin"])
        self.assertEqual(nodes["checkout-service"]["alerts"]["alerts"], 2)
        self.assertIsNone(nodes["search-api"]["alerts"])
        # a service known only from alerts is shown, with no claimed edges
        self.assertFalse(nodes["payments-gateway"]["known"])


class TestAccessControl(ApiCase):
    def test_whoami_reports_role_and_grants(self):
        expected = {
            ADMIN: ("admin", ["approve", "create", "feedback", "ingest",
                              "read"]),
            "viewer-tok": ("viewer", ["read"]),
            "operator-tok": ("operator", ["create", "feedback", "ingest",
                                          "read"]),
            "approver-tok": ("approver", ["approve", "feedback", "read"]),
            "ingest-tok": ("ingest", ["ingest"]),
        }
        for token, (role, grants) in expected.items():
            status, body = self.call("/v1/whoami", token=token)
            self.assertEqual((status, body["role"], body["grants"]),
                             (200, role, grants))
        self.assertEqual(self.call("/v1/whoami", token=None)[0], 401)

    def test_ingest_credential_is_write_only_for_alerts(self):
        # The token that lives in an Alertmanager config can post alerts and
        # do nothing else: not read them back, not open an incident.
        status, _ = self.call("/v1/alerts", "POST", STORM, token="ingest-tok")
        self.assertEqual(status, 202)
        for method, path in (("GET", "/v1/alerts"), ("GET", "/v1/incidents"),
                             ("POST", "/v1/incidents"),
                             ("DELETE", "/v1/alerts"), ("GET", "/v1/stats")):
            status, _ = self.call(path, method, {} if method == "POST"
                                  else None, token="ingest-tok")
            self.assertEqual(status, 403, f"{method} {path}")

    def test_viewer_reads_every_new_surface_and_mutates_none(self):
        for path in ("/v1/incidents", "/v1/alerts", "/v1/stats",
                     "/v1/topology", "/v1/catalog", "/v1/openapi.json"):
            self.assertEqual(self.call(path, token="viewer-tok")[0], 200,
                             path)
        for method, path in (("POST", "/v1/alerts"), ("DELETE", "/v1/alerts"),
                             ("POST", "/v1/alerts/groups/x/investigate"),
                             ("POST", "/v1/incidents/x/feedback")):
            self.assertEqual(
                self.call(path, method, {}, token="viewer-tok")[0], 403,
                f"{method} {path}")

    def test_every_route_declares_a_grant_and_non_public_ones_enforce_it(self):
        public = {("GET", "/health")}
        for route in api_server.ROUTES:
            key = (route.method, route.path)
            if key in public:
                self.assertIsNone(route.grant)
                continue
            self.assertIsNotNone(route.grant, key)
            path = re.sub(r"\{\w+\}", "x", route.path)
            status, hdrs, _ = self.raw(path, route.method,
                                       {} if route.method == "POST" else None,
                                       token=None)
            self.assertEqual(status, 401, key)
            self.assertEqual(hdrs["WWW-Authenticate"], "Bearer")

    def test_openapi_documents_every_route_with_its_grant(self):
        status, spec = self.call("/v1/openapi.json")
        self.assertEqual(status, 200)
        self.assertEqual(spec["openapi"], "3.1.0")
        for route in api_server.ROUTES:
            operation = spec["paths"][route.path][route.method.lower()]
            self.assertEqual(operation["summary"], route.summary)
            if route.grant is None:
                self.assertEqual(operation["security"], [])
            else:
                self.assertEqual(operation["x-required-grant"], route.grant)
        approve = spec["paths"]["/v1/incidents/{incident_id}/approvals"]
        self.assertEqual(approve["post"]["x-roles"], ["admin", "approver"])
        self.assertEqual(approve["post"]["parameters"][0]["name"],
                         "incident_id")


class TestTransport(ApiCase):
    def test_json_responses_carry_hardening_headers(self):
        status, hdrs, _ = self.raw("/v1/whoami")
        self.assertEqual(status, 200)
        self.assertEqual(hdrs["X-Content-Type-Options"], "nosniff")
        self.assertEqual(hdrs["Cache-Control"], "no-store")
        self.assertRegex(hdrs["X-Request-Id"], r"^[0-9a-f]{12}$")
        other = self.raw("/v1/whoami")[1]["X-Request-Id"]
        self.assertNotEqual(hdrs["X-Request-Id"], other)

    def test_console_csp_admits_exactly_the_shipped_scripts(self):
        status, hdrs, html = self.raw("/", token=None)
        self.assertEqual(status, 200)
        csp = hdrs["Content-Security-Policy"]
        self.assertIn("default-src 'none'", csp)
        self.assertIn("connect-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertEqual(hdrs["X-Frame-Options"], "DENY")
        script_src = next(part for part in csp.split("; ")
                          if part.startswith("script-src"))
        self.assertNotIn("unsafe-inline", script_src)
        self.assertNotIn("unsafe-eval", csp)
        scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.S)
        self.assertTrue(scripts)
        for script in scripts:
            digest = base64.b64encode(
                hashlib.sha256(script.encode()).digest()).decode()
            self.assertIn(f"'sha256-{digest}'", script_src)

    def test_malformed_json_is_400_not_a_default_request(self):
        # A mangled body must never be read as {} — on POST /v1/incidents
        # that would silently start the canonical incident.
        before = len(api_server.STATE.incidents)
        status, _, body = self.raw("/v1/incidents", "POST",
                                   data=b'{"scenario": ')
        self.assertEqual(status, 400)
        self.assertIn("not valid JSON", body["error"])
        self.assertEqual(len(api_server.STATE.incidents), before)

    def test_oversized_body_is_413(self):
        blob = b'{"note": "' + b"x" * (api_server.MAX_BODY_BYTES + 1) + b'"}'
        status, _, body = self.raw("/v1/changes/score", "POST", data=blob)
        self.assertEqual(status, 413)
        self.assertIn("exceeds", body["error"])

    def test_alert_batches_get_a_larger_body_budget(self):
        # 150 alerts with long summaries is over the default 64 KB cap but
        # is an ordinary Alertmanager flush.
        alerts = [{"name": f"Alert{i}", "service": f"svc-{i}",
                   "summary": "s" * 190,
                   "labels": {f"k{j}": "v" * 60 for j in range(6)}}
                  for i in range(150)]
        blob = json.dumps({"alerts": alerts}).encode()
        self.assertGreater(len(blob), api_server.MAX_BODY_BYTES)
        self.assertLess(len(blob), api_server.MAX_ALERT_BODY_BYTES)
        status, _, body = self.raw("/v1/alerts", "POST", data=blob)
        self.assertEqual(status, 202)
        self.assertEqual(body["received"], 150)
        self.call("/v1/alerts", "DELETE")

    def test_wrong_method_is_405_with_allow(self):
        status, hdrs, _ = self.raw("/v1/whoami", "POST", {})
        self.assertEqual(status, 405)
        self.assertEqual(hdrs["Allow"], "GET")
        status, hdrs, _ = self.raw("/v1/alerts", "PUT", {})
        self.assertIn(status, (405, 501))       # PUT is not served at all

    def test_unknown_route_is_401_anonymous_and_404_authenticated(self):
        self.assertEqual(self.call("/v1/nope", token=None)[0], 401)
        self.assertEqual(self.call("/v1/nope")[0], 404)
        self.assertEqual(self.call("/v1/nope", "POST", {})[0], 404)

    def test_handler_bug_is_a_500_that_names_the_request_not_the_bug(self):
        import contextlib
        import io

        def boom(req):
            raise RuntimeError("secret internal detail /etc/passwd")

        original = api_server.ROUTES
        api_server.ROUTES = [
            api_server.Route("GET", "/v1/boom", "read", boom, "test only"),
            *original]
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr):
                status, hdrs, body = self.raw("/v1/boom")
        finally:
            api_server.ROUTES = original
        self.assertEqual(status, 500)
        self.assertEqual(body["error"], "internal error")
        self.assertEqual(body["request_id"], hdrs["X-Request-Id"])
        self.assertNotIn("secret", json.dumps(body))
        # ...while the operator's log has the traceback under the same id
        logged = json.loads(stderr.getvalue())
        self.assertEqual(logged["request_id"], body["request_id"])
        self.assertIn("secret internal detail", logged["traceback"])
        # and the server is still serving
        self.assertEqual(self.call("/v1/whoami")[0], 200)

    def test_client_hanging_up_mid_response_is_not_a_server_error(self):
        import socket

        errors = []
        original = self.server.handle_error
        self.server.handle_error = lambda *args: errors.append(args)
        try:
            for _ in range(3):
                # ask for the ~750 KB console, then leave without reading it
                sock = socket.create_connection(("127.0.0.1", self.port))
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                __import__("struct").pack("ii", 1, 0))
                sock.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
                sock.recv(64)
                sock.close()                     # RST: abrupt disconnect
            time.sleep(0.3)
            self.assertEqual(self.call("/v1/whoami")[0], 200)
            self.assertEqual(errors, [])
        finally:
            self.server.handle_error = original

    def test_async_incident_reports_progress_then_completes(self):
        status, started = self.call("/v1/incidents", "POST", {"async": True})
        self.assertEqual(status, 202)
        self.assertEqual(started["status"], "RUNNING")
        self.assertIn("stages", started)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            _, polled = self.call(f"/v1/incidents/{started['incident_id']}")
            if polled["status"] != "RUNNING":
                break
            time.sleep(0.05)
        self.assertEqual(polled["status"], "PAUSED")
        self.assertEqual(polled["hypotheses"]["conclusion"], "validated")


class TestShippedArtifacts(ApiCase):
    """The console is a build artifact committed in two places, and the
    public demo replays a recorded transcript. Both can silently go stale;
    these tests make that a failure instead."""

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _read(self, *parts):
        with open(os.path.join(self.ROOT, *parts), "rb") as fh:
            return fh.read()

    def test_api_console_and_pages_site_are_the_same_build(self):
        served = self._read("src", "aetherops", "api", "static",
                            "index.html")
        pages = self._read("site", "index.html")
        self.assertEqual(hashlib.sha256(served).hexdigest(),
                         hashlib.sha256(pages).hexdigest(),
                         "run `make web` — the API console and the Pages "
                         "site were built from different sources")

    def test_demo_transcript_matches_the_api_as_it_is_today(self):
        path = os.path.join(self.ROOT, "web", "src", "demo-data.json")
        if not os.path.exists(path):
            self.skipTest("no recorded transcript in this checkout")
        with open(path, encoding="utf-8") as fh:
            recorded = json.load(fh)
        hint = "run `make record-demo && make web` — the transcript is stale"

        live = self.create()
        live.pop("fence")
        canonical = recorded["runs"]["s1-checkout-pool"]["created"]
        # scenario differs by construction (default create vs. named), so
        # compare shape: the keys the console reads must all be recorded.
        self.assertEqual(sorted(live), sorted(canonical), hint)
        self.assertEqual(sorted(live["hypotheses"]),
                         sorted(canonical["hypotheses"]), hint)
        self.assertEqual(sorted(live["diagnosis"]),
                         sorted(canonical["diagnosis"]), hint)
        self.assertEqual([s["id"] for s in live["stages"]],
                         [s["id"] for s in canonical["stages"]], hint)

        _, scenarios = self.call("/v1/scenarios")
        self.assertEqual(sorted(s["id"] for s in scenarios["scenarios"]),
                         sorted(recorded["runs"]), hint)
        _, spec = self.call("/v1/openapi.json")
        self.assertEqual(sorted(spec["paths"]),
                         sorted(recorded["openapi"]["paths"]), hint)
        _, catalog = self.call("/v1/catalog")
        self.assertEqual(catalog, recorded["catalog"], hint)


class TestRateLimit(ApiCase):
    def test_mutations_are_limited_per_role_and_reads_are_not(self):
        api_server.STATE.limiter = api_server.RateLimiter(3)
        body = {"service": "orders-service", "title": "t", "diff": "d"}
        codes = [self.call("/v1/changes/score", "POST", body)[0]
                 for _ in range(5)]
        self.assertEqual(codes, [200, 200, 200, 429, 429])
        status, hdrs, payload = self.raw("/v1/changes/score", "POST", body)
        self.assertEqual(status, 429)
        self.assertGreaterEqual(int(hdrs["Retry-After"]), 1)
        self.assertIn("rate limit", payload["error"])
        # reads are never throttled, and another role has its own budget
        self.assertEqual(self.call("/v1/whoami")[0], 200)
        self.assertEqual(self.call("/v1/changes/score", "POST", body,
                                   token="operator-tok")[0], 200)

    def test_window_slides(self):
        now = [0.0]
        limiter = api_server.RateLimiter(2, clock=lambda: now[0])
        self.assertEqual(limiter.check("k"), 0.0)
        self.assertEqual(limiter.check("k"), 0.0)
        self.assertGreater(limiter.check("k"), 0.0)
        now[0] = 61.0
        self.assertEqual(limiter.check("k"), 0.0)

    def test_zero_disables_the_limiter(self):
        limiter = api_server.RateLimiter(0)
        self.assertTrue(all(limiter.check("k") == 0.0 for _ in range(50)))


class TestIncidentArchive(unittest.TestCase):
    """With AETHEROPS_DB set, incident history survives a restart — as a
    readable record, explicitly not as a resumable workflow."""

    def test_history_survives_restart_but_paused_state_does_not(self):
        with tempfile.TemporaryDirectory() as directory:
            os.environ["AETHEROPS_DB"] = os.path.join(directory, "aether.db")
            try:
                api_server.STATE = api_server.AppState()
                server = ThreadingHTTPServer(("127.0.0.1", 0),
                                             api_server.Handler)
                port = server.server_address[1]
                threading.Thread(target=server.serve_forever,
                                 daemon=True).start()

                def call(path, method="GET", body=None):
                    request = urllib.request.Request(
                        f"http://127.0.0.1:{port}{path}", method=method,
                        data=json.dumps(body).encode()
                        if body is not None else None,
                        headers={"Content-Type": "application/json",
                                 "Authorization": f"Bearer {ADMIN}"})
                    try:
                        with urllib.request.urlopen(request,
                                                    timeout=60) as resp:
                            return resp.status, json.loads(resp.read())
                    except urllib.error.HTTPError as exc:
                        return exc.code, json.loads(exc.read())

                _, paused = call("/v1/incidents", "POST", {})
                _, done = call("/v1/incidents", "POST", {})
                _, done = call(
                    f"/v1/incidents/{done['incident_id']}/approvals", "POST",
                    {"decision": "approve", "fence": done["fence"]})
                self.assertEqual(done["status"], "SUCCEEDED")

                api_server.STATE.close()
                api_server.STATE = api_server.AppState()   # "restart"
                self.assertEqual(len(api_server.STATE.incidents), 0)

                _, listing = call("/v1/incidents")
                rows = {r["incident_id"]: r for r in listing["incidents"]}
                self.assertEqual(set(rows), {paused["incident_id"],
                                             done["incident_id"]})
                self.assertTrue(all(r["archived"] for r in rows.values()))
                self.assertEqual(rows[done["incident_id"]]["outcome"],
                                 "remediated")

                status, restored = call(
                    f"/v1/incidents/{done['incident_id']}")
                self.assertEqual(status, 200)
                self.assertTrue(restored["archived"])
                self.assertEqual(restored["hypotheses"]["validated"],
                                 "deploy-regression/memory")
                self.assertNotIn("fence", restored)

                # the audit chain reloads and re-verifies independently
                _, audit = call(f"/v1/incidents/{done['incident_id']}/audit")
                self.assertTrue(audit["chain_verified"])

                # the persisted-audit lookup takes its filename from the
                # URL: an id that is not a plain token must not reach disk
                for hostile in ("..%2F..%2Faether", "..", "a%00b"):
                    status, _ = call(f"/v1/incidents/{hostile}/audit")
                    self.assertEqual(status, 404, hostile)

                # a gate cannot be decided against state that is gone
                status, body = call(
                    f"/v1/incidents/{paused['incident_id']}/approvals",
                    "POST", {"decision": "approve",
                             "fence": paused["fence"]})
                self.assertEqual(status, 409)
                self.assertIn("did not survive", body["error"])

                _, stats = call("/v1/stats")
                self.assertEqual(stats["incidents"]["total"], 2)

                server.shutdown()
                server.server_close()
                api_server.STATE.close()
            finally:
                os.environ.pop("AETHEROPS_DB", None)
                api_server.STATE = api_server.AppState()


if __name__ == "__main__":
    unittest.main()
