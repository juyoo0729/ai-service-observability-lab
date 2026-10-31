# AI Service Observability · measured drill

Simulated provider; transport=inprocess. Timings measured; costs modeled, not API bills.

| Phase | Requests | Answers | HOLD | Errors | Error rate | p95 ms | Alert |
|---|---:|---:|---:|---:|---:|---:|---|
| baseline | 12 | 10 | 2 | 0 | 0.0000 | 17.5216 | ok |
| incident | 12 | 0 | 0 | 12 | 1.0000 | 110.6655 | firing |
| recovery | 12 | 10 | 2 | 0 | 0.0000 | 7.3639 | ok |

## Detection and recovery

- 2026-10-31T05:30:17.307700+00:00: ok after request ac1fe52fd5fc4e989b34d555b3a5395e
- 2026-10-31T05:30:18.068136+00:00: firing after request 6a50261b12d24a95b10c71d988fbada9
- 2026-10-31T05:30:20.494895+00:00: ok after request cdbf972b15aa4a5694c3620c1fa248d2

The observer samples metrics after completed requests. This is not an external alert delivery service.
Recovery means injected failures were removed and the last 10 eligible requests became healthy.
The whole-run SLO can remain violated after the rolling alert clears.

## Reproduction

- Python: 3.11.15
- Concurrent clients: 1
- Sequence: 12 baseline + 12 incident + 12 recovery
- Server latency excludes telemetry database writes and network transfer; client latency includes both.
- Whole-run aggregates and traces: summary.json / events.jsonl / client-results.json


---

# AI Service Observability · measured drill

Simulated provider; transport=inprocess. Timings measured; costs modeled, not API bills.

| Phase | Requests | Answers | HOLD | Errors | Error rate | p95 ms | Alert |
|---|---:|---:|---:|---:|---:|---:|---|
| baseline | 12 | 10 | 2 | 0 | 0.0000 | 17.1294 | ok |
| incident | 12 | 0 | 0 | 12 | 1.0000 | 111.8814 | firing |
| recovery | 12 | 10 | 2 | 0 | 0.0000 | 7.7024 | ok |

## Detection and recovery

- 2026-10-31T05:30:22.258261+00:00: ok after request 5488d2b1d90c484bb6ed17f349f0f3f5
- 2026-10-31T05:30:22.737507+00:00: firing after request d85b670f130542be859dcad2504c457a
- 2026-10-31T05:30:23.517390+00:00: ok after request 7bde7627b18b4708bc33bd667259d61f

The observer samples metrics after completed requests. This is not an external alert delivery service.
Recovery means injected failures were removed and the last 10 eligible requests became healthy.
The whole-run SLO can remain violated after the rolling alert clears.

## Reproduction

- Python: 3.11.15
- Concurrent clients: 4
- Sequence: 12 baseline + 12 incident + 12 recovery
- Server latency excludes telemetry database writes and network transfer; client latency includes both.
- Whole-run aggregates and traces: summary.json / events.jsonl / client-results.json
