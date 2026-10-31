"""Local-only AI service reliability lab; simulated provider, real HTTP and timings."""
import argparse
import queue
import json
import math
import sqlite3
import threading
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ERRORS = {"timeout", "upstream_error", "rate_limited", "empty_response"}
SCENARIOS = {"success", "hold", "timeout", "upstream_error", "rate_limited", "empty_response", "retry_recovered"}
HTTP_STATUS = {"success": 200, "hold": 200, "invalid": 400, "timeout": 504,
               "upstream_error": 502, "rate_limited": 503, "empty_response": 502}
SIMULATED_ATTEMPT_COST_USD = 0.0002


def utc():
    return datetime.now(timezone.utc).isoformat()


def percentile(values, q):
    if not values:
        return None
    xs = sorted(values)
    index = (len(xs) - 1) * q
    lo, hi = math.floor(index), math.ceil(index)
    return xs[lo] + (xs[hi] - xs[lo]) * (index - lo)


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS requests (id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT UNIQUE NOT NULL, event TEXT NOT NULL)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def append(self, event):
        # The event schema deliberately contains neither query nor generated answer.
        with self.connect() as db:
            db.execute("INSERT INTO requests(request_id,event) VALUES (?,?)",
                       (event["request_id"], json.dumps(event, ensure_ascii=False)))

    def events(self):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT event FROM requests ORDER BY id")]


def summarize(events, slo=0.95):
    eligible = [e for e in events if e["outcome"] != "invalid"]
    counts = Counter(e["outcome"] for e in events)
    n = len(eligible)
    failures = sum(counts[k] for k in ERRORS)
    successes = counts["success"]
    cost = sum(e["simulated_cost_usd"] for e in events)
    return {
        "total_requests": len(events), "eligible_requests": n,
        "outcomes": dict(counts), "system_errors": failures,
        "system_error_rate": failures / n if n else None,
        "valid_response_rate": (successes + counts["hold"]) / n if n else None,
        "answer_rate": successes / n if n else None,
        "hold_rate": counts["hold"] / n if n else None,
        "latency_p50_ms": percentile([e["duration_ms"] for e in eligible], .5),
        "latency_p95_ms": percentile([e["duration_ms"] for e in eligible], .95),
        "answer_latency_p95_ms": percentile([e["duration_ms"] for e in eligible if e["outcome"] == "success"], .95),
        "provider_attempts": sum(e["attempts"] for e in events),
        "simulated_cost_usd": round(cost, 8),
        "simulated_cost_per_answer_usd": cost / successes if successes else None,
        "slo_target": slo, "error_budget_remaining_requests": n * (1 - slo) - failures,
    }


def alert_state(events, window=10, minimum=10, threshold=.2):
    eligible = [e for e in events if e["outcome"] != "invalid"][-window:]
    if len(eligible) < minimum:
        return {"state": "insufficient_data", "observations": len(eligible), "error_rate": None}
    rate = sum(e["outcome"] in ERRORS for e in eligible) / len(eligible)
    return {"state": "firing" if rate >= threshold else "ok",
            "observations": len(eligible), "error_rate": rate}


class ProviderFailure(Exception):
    def __init__(self, outcome):
        self.outcome = outcome


def fake_provider(scenario, attempt):
    if scenario == "timeout":
        time.sleep(1)
    else:
        time.sleep(.004)
    if scenario in {"upstream_error", "rate_limited"}:
        raise ProviderFailure(scenario)
    if scenario == "retry_recovered" and attempt == 1:
        raise ProviderFailure("upstream_error")
    if scenario == "empty_response":
        return ""
    return "Example answer from the simulated provider."


class Service:
    def __init__(self, store, provider=fake_provider, timeout=.04, max_attempts=2):
        self.store, self.provider = store, provider
        self.timeout, self.max_attempts = timeout, max_attempts

    def invoke(self, scenario, attempt):
        result = queue.Queue(maxsize=1)
        def worker():
            try:
                result.put((True, self.provider(scenario, attempt)))
            except Exception as exc:
                result.put((False, exc))
        threading.Thread(target=worker, daemon=True).start()
        try:
            ok, value = result.get(timeout=self.timeout)
        except queue.Empty:
            raise TimeoutError from None
        if not ok:
            raise value
        return value

    def handle(self, payload):
        started = time.perf_counter()
        event = {"request_id": uuid.uuid4().hex, "started_at": utc(), "outcome": "invalid",
                 "reason": "invalid_payload", "attempts": 0, "simulated_cost_usd": 0.0, "stages": []}
        answer = None
        def stage(name, start, outcome, attempt=None):
            item = {"name": name, "duration_ms": (time.perf_counter() - start) * 1000, "outcome": outcome}
            if attempt is not None:
                item["attempt"] = attempt
            event["stages"].append(item)
        try:
            begin = time.perf_counter()
            if not isinstance(payload, dict) or not isinstance(payload.get("query"), str) or not payload["query"].strip() or len(payload["query"]) > 2000:
                stage("validation", begin, "invalid")
            elif not isinstance(payload.get("scenario", "success"), str) or payload.get("scenario", "success") not in SCENARIOS:
                event["reason"] = "unknown_scenario"
                stage("validation", begin, "invalid")
            else:
                scenario = payload.get("scenario", "success")
                event["scenario"] = scenario
                stage("validation", begin, "ok")
                begin = time.perf_counter()
                time.sleep(.002)
                stage("retrieval", begin, "no_evidence" if scenario == "hold" else "ok")
                if scenario == "hold":
                    event.update(outcome="hold", reason="insufficient_evidence")
                else:
                    for attempt in range(1, self.max_attempts + 1):
                        event["attempts"] += 1
                        event["simulated_cost_usd"] += SIMULATED_ATTEMPT_COST_USD
                        begin = time.perf_counter()
                        try:
                            answer = self.invoke(scenario, attempt)
                            if not isinstance(answer, str) or not answer.strip():
                                raise ProviderFailure("empty_response")
                            event.update(outcome="success", reason="answered")
                            stage("generation", begin, "ok", attempt)
                            break
                        except TimeoutError:
                            outcome = "timeout"
                        except ProviderFailure as exc:
                            outcome = exc.outcome
                        except Exception:
                            outcome = "upstream_error"
                        event.update(outcome=outcome, reason=outcome)
                        stage("generation", begin, outcome, attempt)
                        if attempt < self.max_attempts:
                            begin = time.perf_counter()
                            time.sleep(.005)
                            stage("retry_backoff", begin, "ok", attempt)
        finally:
            event["duration_ms"] = (time.perf_counter() - started) * 1000
            event["finished_at"] = utc()
            self.store.append(event)
        status = HTTP_STATUS[event["outcome"]]
        response = {"request_id": event["request_id"], "outcome": event["outcome"], "reason": event["reason"]}
        if event["outcome"] == "success":
            response["answer"] = answer
        return status, response


def create_server(store, host="127.0.0.1", port=8000):
    service = Service(store)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # No query, header or client address access logs.

        def send_json(self, status, data):
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                self.send_json(200, {"status": "ok", "provider": "simulated"})
            elif self.path == "/metrics":
                events = store.events()
                self.send_json(200, {"summary": summarize(events), "alert": alert_state(events)})
            else:
                self.send_json(404, {"error": "not_found"})

        def do_POST(self):
            if self.path != "/ask":
                self.send_json(404, {"error": "not_found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16384:
                    raise ValueError("size")
                payload = json.loads(self.rfile.read(length))
            except (ValueError, UnicodeDecodeError):
                payload = None
            status, response = service.handle(payload)
            self.send_json(status, response)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="runs/local/events.sqlite")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = create_server(Store(args.db), port=args.port)
    print(f"Local lab: http://127.0.0.1:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
