"""Sense-plane alert intake (docs/20 §3): normalization, fingerprint
deduplication, and topology + time correlation into investigable groups."""
import unittest

from aetherops.alerts import AlertStore, AlertValidationError, normalize
from aetherops.alerts.store import (MAX_ALERTS_PER_REQUEST,
                                    MAX_GROUPS_RETAINED, fingerprint)
from aetherops.graph.service_graph import default_graph

T0 = 1_800_000_000.0


def _alert(name, service, ts=T0, **extra):
    return {"source": "prometheus", "name": name, "service": service,
            "ts": ts, **extra}


def _store(window_s=900):
    return AlertStore(default_graph(), window_s=window_s)


def _ingest(store, *alerts):
    return store.ingest(normalize({"alerts": list(alerts)}, now=T0))


class TestNormalize(unittest.TestCase):
    def test_single_native_alert(self):
        [alert] = normalize({"name": "HighLatency",
                             "service": "checkout-service",
                             "severity": "critical"}, now=T0)
        self.assertEqual(alert["source"], "api")
        self.assertEqual(alert["status"], "firing")
        self.assertEqual(alert["ts"], T0)

    def test_alertmanager_webhook_is_accepted_as_is(self):
        payload = {
            "version": "4", "receiver": "aetherops", "status": "firing",
            "alerts": [{
                "status": "firing",
                "labels": {"alertname": "KubePodCrashLooping",
                           "service": "checkout-service",
                           "severity": "page", "pod": "checkout-7f9c-1"},
                "annotations": {"summary": "Pod is crash looping"},
                "startsAt": "2026-08-07T14:06:00Z"}]}
        [alert] = normalize(payload, now=T0)
        self.assertEqual(alert["source"], "alertmanager")
        self.assertEqual(alert["name"], "KubePodCrashLooping")
        self.assertEqual(alert["service"], "checkout-service")
        self.assertEqual(alert["severity"], "critical")      # page -> critical
        self.assertEqual(alert["summary"], "Pod is crash looping")
        self.assertEqual(alert["labels"]["pod"], "checkout-7f9c-1")
        self.assertEqual(alert["ts"], 1786111560.0)          # parsed startsAt

    def test_alertmanager_falls_back_to_the_job_label(self):
        [alert] = normalize({"alerts": [{"labels": {
            "alertname": "TargetDown", "job": "search-api"}}]}, now=T0)
        self.assertEqual(alert["service"], "search-api")

    def test_unknown_severity_degrades_to_warning(self):
        [alert] = normalize(_alert("X", "svc", severity="spicy"), now=T0)
        self.assertEqual(alert["severity"], "warning")

    def test_unparseable_timestamp_falls_back_to_now(self):
        [alert] = normalize({"name": "X", "service": "svc",
                             "ts": "last tuesday"}, now=T0)
        self.assertEqual(alert["ts"], T0)

    def test_rejects_what_it_cannot_identify(self):
        for bad in ({}, [], {"name": "no service"},
                    {"service": "no name"}, {"alerts": []},
                    {"alerts": "nope"}, {"alerts": [42]},
                    {"alerts": [{"labels": {"alertname": "NoService"}}]},
                    {"name": {"nested": 1}, "service": "svc"}):
            with self.subTest(payload=bad):
                with self.assertRaises(AlertValidationError):
                    normalize(bad, now=T0)

    def test_batch_size_is_bounded(self):
        with self.assertRaises(AlertValidationError):
            normalize({"alerts": [_alert("X", "svc")]
                       * (MAX_ALERTS_PER_REQUEST + 1)}, now=T0)

    def test_oversized_fields_are_truncated_not_stored(self):
        [alert] = normalize(_alert("N" * 5000, "svc"), now=T0)
        self.assertEqual(len(alert["name"]), 200)

    def test_fingerprint_is_identity_not_content(self):
        a = normalize(_alert("HighLatency", "svc", summary="p99 2100ms"),
                      now=T0)[0]
        b = normalize(_alert("highlatency", "SVC", summary="p99 2400ms"),
                      now=T0)[0]
        c = normalize(_alert("HighLatency", "other"), now=T0)[0]
        self.assertEqual(fingerprint(a), fingerprint(b))
        self.assertNotEqual(fingerprint(a), fingerprint(c))


class TestDeduplication(unittest.TestCase):
    def test_refire_folds_into_the_existing_alert(self):
        store = _store()
        outcome = _ingest(store,
                          _alert("HighLatency", "checkout-service"),
                          _alert("HighLatency", "checkout-service", T0 + 60),
                          _alert("HighLatency", "checkout-service", T0 + 120))
        self.assertEqual(outcome["new_alerts"], 1)
        self.assertEqual(outcome["deduplicated"], 2)
        [group] = store.groups()
        self.assertEqual(group["unique_alerts"], 1)
        self.assertEqual(group["events"], 3)
        self.assertEqual(group["alerts"][0]["count"], 3)
        self.assertEqual(group["alerts"][0]["last_seen"], T0 + 120)

    def test_refire_can_only_raise_severity(self):
        store = _store()
        _ingest(store,
                _alert("HighLatency", "checkout-service", severity="high"),
                _alert("HighLatency", "checkout-service", T0 + 30,
                       severity="critical"),
                _alert("HighLatency", "checkout-service", T0 + 60,
                       severity="info"))
        self.assertEqual(store.groups()[0]["severity"], "critical")

    def test_same_name_from_another_source_is_a_different_alert(self):
        store = _store()
        _ingest(store,
                _alert("HighLatency", "checkout-service"),
                {**_alert("HighLatency", "checkout-service"),
                 "source": "datadog"})
        [group] = store.groups()
        self.assertEqual(group["unique_alerts"], 2)
        self.assertEqual(group["sources"], ["datadog", "prometheus"])


class TestCorrelation(unittest.TestCase):
    def test_a_caller_joins_the_group_of_the_service_it_calls(self):
        store = _store()
        _ingest(store,
                _alert("HighLatency", "checkout-service"),
                _alert("High5xxRate", "storefront-web", T0 + 90))
        [group] = store.groups()
        self.assertEqual(group["origin"], "checkout-service")
        self.assertEqual(group["services"],
                         ["checkout-service", "storefront-web"])
        joined = {a["service"]: a["joined_because"]
                  for a in group["alerts"]}
        self.assertEqual(joined["storefront-web"],
                         "calls checkout-service (downstream symptom)")

    def test_origin_is_the_most_upstream_alerting_service(self):
        # The symptom pages before the cause — arrival order must not decide
        # where the investigation starts.
        store = _store()
        _ingest(store,
                _alert("High5xxRate", "storefront-web"),
                _alert("HighLatency", "checkout-service", T0 + 30),
                _alert("ConnRefused", "payments-service", T0 + 60))
        [group] = store.groups()
        self.assertEqual(group["origin"], "payments-service")

    def test_an_unrelated_service_opens_its_own_group(self):
        # orders-service neither calls nor is called by checkout-service.
        store = _store()
        outcome = _ingest(store,
                          _alert("HighLatency", "checkout-service"),
                          _alert("QueueBacklog", "orders-service", T0 + 30))
        self.assertEqual(outcome["new_groups"], 2)
        self.assertEqual({g["origin"] for g in store.groups()},
                         {"checkout-service", "orders-service"})

    def test_a_service_outside_the_graph_correlates_only_with_itself(self):
        store = _store()
        _ingest(store,
                _alert("HighLatency", "checkout-service"),
                _alert("TLSHandshakeErrors", "payments-gateway", T0 + 10),
                _alert("CertExpired", "payments-gateway", T0 + 20))
        groups = {g["origin"]: g for g in store.groups()}
        self.assertEqual(set(groups),
                         {"checkout-service", "payments-gateway"})
        self.assertEqual(groups["payments-gateway"]["unique_alerts"], 2)

    def test_alerts_outside_the_window_do_not_correlate(self):
        store = _store(window_s=900)
        _ingest(store,
                _alert("HighLatency", "checkout-service"),
                _alert("High5xxRate", "storefront-web", T0 + 901))
        self.assertEqual(len(store.groups()), 2)

    def test_every_alert_records_why_it_joined(self):
        store = _store()
        _ingest(store,
                _alert("HighLatency", "checkout-service"),
                _alert("PodOOMKilled", "checkout-service", T0 + 5),
                _alert("Timeouts", "search-api", T0 + 10))
        reasons = {a["name"]: a["joined_because"]
                   for a in store.groups()[0]["alerts"]}
        self.assertEqual(reasons["HighLatency"], "first alert in the group")
        self.assertEqual(reasons["PodOOMKilled"],
                         "same service as origin checkout-service")
        self.assertEqual(
            reasons["Timeouts"],
            "dependency of checkout-service (upstream cause candidate)")


class TestLifecycle(unittest.TestCase):
    def test_resolve_then_refire_is_a_new_alert(self):
        store = _store()
        _ingest(store, _alert("HighLatency", "checkout-service"))
        outcome = _ingest(store, _alert("HighLatency", "checkout-service",
                                        T0 + 60, status="resolved"))
        self.assertEqual(outcome["resolved"], 1)
        self.assertEqual(store.groups()[0]["status"], "resolved")
        self.assertEqual(store.stats()["open_groups"], 0)

        _ingest(store, _alert("HighLatency", "checkout-service", T0 + 120))
        self.assertEqual(len(store.groups()), 2)      # resolved one is closed
        self.assertEqual(store.stats()["open_groups"], 1)

    def test_a_group_is_claimed_by_exactly_one_incident(self):
        store = _store()
        _ingest(store, _alert("HighLatency", "checkout-service"))
        group_id = store.groups()[0]["id"]
        self.assertTrue(store.attach_incident(group_id, "inc_1"))
        self.assertFalse(store.attach_incident(group_id, "inc_2"))
        self.assertFalse(store.attach_incident("grp_nope", "inc_3"))
        self.assertEqual(store.group(group_id)["incident_id"], "inc_1")
        self.assertEqual(store.group(group_id)["status"], "investigating")

    def test_noise_reduction_is_groups_over_raw_events(self):
        store = _store()
        alerts = [_alert("HighLatency", "checkout-service", T0 + i)
                  for i in range(6)]
        alerts += [_alert("PodOOMKilled", "checkout-service", T0 + 6),
                   _alert("High5xxRate", "storefront-web", T0 + 7),
                   _alert("CertExpired", "payments-gateway", T0 + 8),
                   _alert("CertExpired", "payments-gateway", T0 + 9)]
        _ingest(store, *alerts)
        stats = store.stats()
        self.assertEqual(stats["received"], 10)
        self.assertEqual(stats["deduplicated"], 6)
        self.assertEqual(stats["unique_alerts"], 4)
        self.assertEqual(stats["groups"], 2)
        self.assertEqual(stats["noise_reduction"], 0.8)

    def test_empty_store_reports_zero_not_a_division_error(self):
        self.assertEqual(_store().stats()["noise_reduction"], 0.0)

    def test_pressure_by_service_marks_the_origin(self):
        store = _store()
        _ingest(store,
                _alert("HighLatency", "checkout-service",
                       severity="critical"),
                _alert("High5xxRate", "storefront-web", T0 + 5))
        pressure = store.by_service()
        self.assertTrue(pressure["checkout-service"]["origin"])
        self.assertEqual(pressure["checkout-service"]["severity"],
                         "critical")
        self.assertFalse(pressure["storefront-web"]["origin"])

    def test_retention_is_bounded(self):
        store = AlertStore(default_graph(), window_s=1)
        for index in range(MAX_GROUPS_RETAINED + 25):
            store.ingest(normalize(
                _alert("X", f"svc-{index}", T0 + index * 10), now=T0))
        self.assertEqual(len(store.groups()), MAX_GROUPS_RETAINED)

    def test_clear_resets_counters(self):
        store = _store()
        _ingest(store, _alert("HighLatency", "checkout-service"))
        store.clear()
        self.assertEqual(store.groups(), [])
        self.assertEqual(store.stats()["received"], 0)


if __name__ == "__main__":
    unittest.main()
