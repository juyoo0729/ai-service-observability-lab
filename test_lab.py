import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from observability_lab import Service, Store, alert_state, create_server, percentile, summarize


class LabTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "events.sqlite")
        self.service = Service(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def call(self, scenario, query="test"):
        return self.service.handle({"query": query, "scenario": scenario})

    def test_success_trace(self):
        status, response = self.call("success")
        self.assertEqual(status, 200)
        event = self.store.events()[0]
        self.assertEqual(response["request_id"], event["request_id"])
        self.assertEqual([s["name"] for s in event["stages"]], ["validation", "retrieval", "generation"])
        self.assertEqual(event["attempts"], 1)

    def test_hold_is_not_system_error_or_answer(self):
        self.call("hold")
        s = summarize(self.store.events())
        self.assertEqual(s["valid_response_rate"], 1)
        self.assertEqual(s["answer_rate"], 0)
        self.assertEqual(s["system_error_rate"], 0)
        self.assertEqual(s["simulated_cost_usd"], 0)
        self.assertIsNone(s["simulated_cost_per_answer_usd"])

    def test_real_timeout_and_bounded_retries(self):
        status, _ = self.call("timeout")
        self.assertEqual(status, 504)
        e = self.store.events()[0]
        self.assertEqual(e["attempts"], 2)
        self.assertTrue(all(s["outcome"] == "timeout" for s in e["stages"] if s["name"] == "generation"))

    def test_recovered_attempts_not_double_counted(self):
        status, _ = self.call("retry_recovered")
        s = summarize(self.store.events())
        self.assertEqual(status, 200)
        self.assertEqual(s["total_requests"], 1)
        self.assertEqual(s["system_errors"], 0)
        self.assertEqual(s["provider_attempts"], 2)
        self.assertAlmostEqual(s["simulated_cost_usd"], .0004)

    def test_distinct_failure_classes(self):
        for scenario, code in [("rate_limited",503),("upstream_error",502),("empty_response",502)]:
            status, result = self.call(scenario)
            self.assertEqual(status, code)
            self.assertEqual(result["outcome"], scenario)
        self.assertEqual(summarize(self.store.events())["system_errors"], 3)

    def test_invalid_excluded_from_sli(self):
        for payload in [None, {}, {"query":" "}, {"query":"x","scenario":[]}, {"query":123}]:
            code, _ = self.service.handle(payload)
            self.assertEqual(code, 400)
        self.call("success")
        s = summarize(self.store.events())
        self.assertEqual(s["total_requests"], 6)
        self.assertEqual(s["eligible_requests"], 1)
        self.assertEqual(s["valid_response_rate"], 1)

    def test_no_sensitive_payload_in_telemetry(self):
        self.call("success", "PRIVATE_SENTINEL_123")
        raw = json.dumps(self.store.events())
        self.assertNotIn("PRIVATE_SENTINEL", raw)
        self.assertNotIn('"query"', raw)
        self.assertNotIn('"answer"', raw)

    def test_persistence_after_reopen(self):
        self.call("success")
        reopened = Store(self.store.path)
        self.assertEqual(len(reopened.events()), 1)

    def test_alert_threshold_and_recovery_window(self):
        good = {"outcome": "success"}
        bad = {"outcome": "timeout"}
        self.assertEqual(alert_state([good] * 9)["state"], "insufficient_data")
        self.assertEqual(alert_state([good] * 8 + [bad] * 2)["state"], "firing")
        self.assertEqual(alert_state([bad] * 10 + [good] * 10)["state"], "ok")

    def test_empty_summary_and_percentile(self):
        self.assertIsNone(summarize([])["valid_response_rate"])
        self.assertIsNone(summarize([])["latency_p95_ms"])
        self.assertEqual(percentile([10,20],.95),19.5)

    def test_failure_in_latency_denominator(self):
        self.call("success")
        self.call("timeout")
        s = summarize(self.store.events())
        self.assertGreater(s["latency_p95_ms"], s["answer_latency_p95_ms"])
        self.assertEqual(s["system_error_rate"], .5)

    def test_http_and_bad_json(self):
        server = create_server(self.store, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with urllib.request.urlopen(base + "/health") as r:
                self.assertEqual(r.status, 200)
            req = urllib.request.Request(base+"/ask", data=b"{", headers={"Content-Type":"application/json"})
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req)
            self.assertEqual(cm.exception.code, 400)
            cm.exception.close()
            with urllib.request.urlopen(base + "/metrics") as r:
                data = json.loads(r.read())
            self.assertEqual(data["summary"]["outcomes"]["invalid"], 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
