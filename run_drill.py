"""Run real local HTTP traffic through baseline, incident, recovery phases."""
import argparse
import concurrent.futures
import hashlib
import html
import json
import platform
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from observability_lab import Service, Store, alert_state, create_server, summarize, utc

PHASES = [
    ("baseline", ["success"] * 8 + ["hold"] * 2 + ["retry_recovered"] * 2),
    ("incident", ["timeout"] * 4 + ["upstream_error"] * 4 + ["rate_limited"] * 2 + ["empty_response"] * 2),
    ("recovery", ["success"] * 10 + ["hold"] * 2),
]


def post(base, scenario):
    request = urllib.request.Request(base + "/ask", data=json.dumps({"query": "Sample knowledge question", "scenario": scenario}).encode(),
                                     headers={"Content-Type": "application/json"})
    start = time.perf_counter()
    try:
        response = urllib.request.urlopen(request, timeout=5)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        payload = json.loads(response.read())
        return {"scenario": scenario, "http_status": response.status,
                "request_id": payload["request_id"], "outcome": payload["outcome"],
                "client_latency_ms": (time.perf_counter() - start) * 1000}


def direct(service, scenario):
    start = time.perf_counter()
    status, response = service.handle({"query": "Sample knowledge question", "scenario": scenario})
    return {"scenario": scenario, "mapped_http_status": status, "request_id": response["request_id"],
            "outcome": response["outcome"], "client_latency_ms": (time.perf_counter() - start) * 1000}


def write_report(result, directory):
    def fmt(x):
        return "N/A" if x is None else f"{x:.4f}"
    lines = ["# AI Service Observability · measured drill", "",
             f"Simulated provider; transport={result['transport']}. Timings measured; costs modeled, not API bills.", "",
             "| Phase | Requests | Answers | HOLD | Errors | Error rate | p95 ms | Alert |",
             "|---|---:|---:|---:|---:|---:|---:|---|"]
    for phase in result["phases"]:
        s = phase["summary"]
        lines.append(f"| {phase['name']} | {s['total_requests']} | {s['outcomes'].get('success',0)} | {s['outcomes'].get('hold',0)} | {s['system_errors']} | {fmt(s['system_error_rate'])} | {fmt(s['latency_p95_ms'])} | {phase['alert_after']['state']} |")
    lines += ["", "## Detection and recovery", ""]
    for transition in result["alert_transitions"]:
        lines.append(f"- {transition['observed_at']}: {transition['state']} after request {transition['after_request_id']}")
    lines += ["", "The observer samples metrics after completed requests. This is not an external alert delivery service.",
              "Recovery means injected failures were removed and the last 10 eligible requests became healthy.",
              "The whole-run SLO can remain violated after the rolling alert clears.", "",
              "## Reproduction", "", f"- Python: {result['python']}",
              f"- Concurrent clients: {result['concurrency']}",
              "- Sequence: 12 baseline + 12 incident + 12 recovery",
              "- Server latency excludes telemetry database writes and network transfer; client latency includes both.",
              "- Whole-run aggregates and traces: summary.json / events.jsonl / client-results.json"]
    text = "\n".join(lines) + "\n"
    (directory / "report.md").write_text(text, encoding="utf-8")
    cards = "".join(f"<tr><td>{p['name']}</td><td>{p['summary']['outcomes'].get('success',0)}</td><td>{p['summary']['outcomes'].get('hold',0)}</td><td>{p['summary']['system_errors']}</td><td>{fmt(p['summary']['latency_p95_ms'])}</td><td>{p['alert_after']['state']}</td></tr>" for p in result["phases"])
    page = '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AI Service Observability</title><style>body{font:17px/1.65 system-ui;background:#101a29;color:#e8eff8;max-width:1050px;margin:45px auto;padding:0 24px}h1{font-size:36px}table{border-collapse:collapse;width:100%;background:#18283b}td,th{padding:14px;border-bottom:1px solid #35506b;text-align:left}pre{white-space:pre-wrap;font:14px/1.7 monospace}small{color:#a6bdd4}.scroll{overflow-x:auto}</style><small>RELIABILITY LAB / ACTUAL LOCAL MEASUREMENTS</small><h1>AI 서비스 장애 관측 실습</h1><p>정상 → 장애 주입 → 복구. 공급자는 모의 구현입니다. 실제 측정이며 전송 방식은 아래 transport 기록을 확인하세요.</p><div class="scroll"><table><tr><th>단계</th><th>답변</th><th>보류</th><th>오류</th><th>p95 ms</th><th>알림</th></tr>' + cards + '</table></div><h2>실험 기록</h2><pre>' + html.escape(text) + '</pre></html>'
    (directory / "dashboard.html").write_text(page, encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("runs/local-drill"))
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--transport", choices=["http", "inprocess"], default="http")
    args = parser.parse_args(argv)
    if not 1 <= args.concurrency <= 16:
        parser.error("concurrency must be 1..16")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("Use an empty output directory to preserve prior measurements")
    args.output.mkdir(parents=True, exist_ok=True)
    store = Store(args.output / "events.sqlite")
    service = Service(store)
    server = None
    if args.transport == "http":
        server = create_server(store, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
    result = {"started_at": utc(), "python": platform.python_version(), "platform": platform.platform(),
              "transport": args.transport, "concurrency": args.concurrency, "provider": "simulated", "phases": [], "alert_transitions": []}
    result["source_sha256"] = {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in ["observability_lab.py", "run_drill.py"]}
    clients, previous_state = [], "insufficient_data"
    try:
        for name, scenarios in PHASES:
            before = len(store.events())
            observed_start = utc()
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                futures = [pool.submit(post, base, scenario) if args.transport == "http" else pool.submit(direct, service, scenario) for scenario in scenarios]
                for future in concurrent.futures.as_completed(futures):
                    clients.append(future.result())
                    events = store.events()
                    state = alert_state(events)
                    if state["state"] != previous_state:
                        result["alert_transitions"].append({**state, "observed_at": utc(), "after_request_id": events[-1]["request_id"]})
                        previous_state = state["state"]
            phase_events = store.events()[before:]
            result["phases"].append({"name": name, "started_at": observed_start, "finished_at": utc(),
                                     "summary": summarize(phase_events), "alert_after": alert_state(store.events())})
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
    events = store.events()
    result.update(finished_at=utc(), summary=summarize(events))
    (args.output / "events.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n", encoding="utf-8")
    (args.output / "client-results.json").write_text(json.dumps(clients, indent=2), encoding="utf-8")
    (args.output / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    write_report(result, args.output)
    assert len(events) == 36
    assert result["phases"][1]["alert_after"]["state"] == "firing"
    assert result["phases"][2]["alert_after"]["state"] == "ok"
    print(json.dumps({"requests":len(events), "errors":result["summary"]["system_errors"],
                      "alerts":[p["alert_after"]["state"] for p in result["phases"]]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
