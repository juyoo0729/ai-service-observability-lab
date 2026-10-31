# AI Service Observability Lab

**AI 서비스가 실패했을 때, 어느 단계에서 왜 실패했고 복구됐는지 숫자와 기록으로 확인하는 실습입니다.**

1번 [검색 비교 프로젝트](https://github.com/juyoo0729/retrieval-benchmark-lab)가 검색 품질을 다룬다면,
이 저장소는 요청 추적, 실패 분류, 재시도, 지연, 오류 예산과 복구 확인을 다룹니다.

> 외부 LLM/API는 모의 구현입니다. HTTP 요청, 타임아웃, 재시도, SQLite 저장과 시간 측정은 실제 실행합니다.
> 비용은 가정한 단가로 계산하며 실제 청구액이 아닙니다. 실제 운영 장애 대응 실적을 뜻하지 않습니다.

## 실행 · Python 3.11 이상, 추가 패키지 없음

저장소 루트에서 실행합니다.

```bash
python -m unittest -v
python run_drill.py --output runs/local-01
python run_drill.py --concurrency 4 --output runs/local-load-01
```

정상 12건 → 장애 12건 → 복구 12건을 실제 로컬 HTTP로 보냅니다.
실행마다 빈 결과 폴더를 사용합니다. 기존 기록을 덮어쓰지 않습니다.
결과 폴더의 `dashboard.html`을 브라우저에서 열면 요약표와 실험 기록을 볼 수 있습니다.

개별 요청을 보내려면:

```bash
python observability_lab.py --port 8000 --db runs/local/events.sqlite
```

- `GET /health`: 프로세스 상태
- `POST /ask`: 질문과 실습 시나리오
- `GET /metrics`: 요청 집계와 최근 10건의 알림 상태

POST 예시:

```json
{"query":"예제 지식 질문","scenario":"retry_recovered"}
```

서버는 127.0.0.1에만 바인딩합니다. 학습용 서버라 인증·TLS·운영 배포 구성이 없고,
장애 주입용 scenario를 외부에 공개하는 용도로 설계하지 않았습니다.

## 구분하는 결과

| 시나리오 | 최종 상태 | HTTP | 관측할 내용 |
|---|---|---:|---|
| success | success | 200 | 검색·생성 단계 지연 |
| hold | hold | 200 | 근거 부족에 따른 정상 보류, 모델 호출 없음 |
| timeout | timeout | 504 | 실제 대기 제한, 최대 2회 시도 |
| upstream_error | upstream_error | 502 | 모의 공급자 오류 |
| rate_limited | rate_limited | 503 | 공급자 요청 제한, 서비스의 일시적 불가 |
| empty_response | empty_response | 502 | 빈 응답을 성공으로 오인하지 않음 |
| retry_recovered | success | 200 | 첫 시도 실패 후 복구, 요청 1건·시도 2회 |
| 잘못된 입력 | invalid | 400 | 관측하되 신뢰성 지표 분모에서 제외 |

## 무엇을 남기는가

- 요청 ID, 시작·종료 시각, 결과와 사유
- validation / retrieval / generation / retry_backoff 단계별 시간
- 시도 횟수와 실패한 시도의 결과
- p50/p95, 답변 비율, 보류 비율, 시스템 오류율
- 가정한 총 비용과 답변 1건당 비용
- 최근 10개 유효 요청 중 오류 20% 이상일 때 firing, 미만일 때 ok
- 장애 발생 시점과 복구가 관측된 시점을 담은 기록
- SQLite 영속 저장: 서버가 재시작돼도 누적 기록 보존

질문·답변·인증 헤더·사용자 IP는 관측 이벤트에 저장하지 않습니다.
전체 지표 정의와 분모는 [METRICS.md](METRICS.md)를 참고하세요.

## 실험 결과

현재 환경은 로컬 HTTP 연결을 차단하여, 서비스 직접 호출(inprocess)로 측정한 기록을 [EXPERIMENT.md](EXPERIMENT.md)와 example-* 파일에 보관합니다. GitHub Actions의 Python 3.11·3.12 환경에서 테스트 12개와 HTTP 장애·복구 실험(동시성 1·4)이 모두 통과했습니다. [클라우드 검증 기록](https://github.com/juyoo0729/ai-service-observability-lab/actions/runs/41173072994)
CI도 테스트와 두 동시성 조건의 실험을 실행하고 결과를 artifact로 보관합니다.

- `summary.json`: 환경·소스 해시·단계별 집계·알림 변화
- `events.jsonl`: 요청별 단계 기록
- `client-results.json`: HTTP 상태와 클라이언트에서 잰 지연
- `report.md`: 읽기 쉬운 결과
- `dashboard.html`: 별도 서버 없이 여는 보고서

## 실습 범위와 한계

- 실제 LLM 품질, RAG 검색 품질, 실서비스 처리량을 평가하지 않습니다.
- 모의 장애를 제거한 뒤 정상 트래픽을 보내는 복구 실습입니다. 외부 공급자를 실제로 복구한 것이 아닙니다.
- 알림은 요청 완료 후 관측하는 상태 전이입니다. Slack/이메일 전송이나 외부 모니터링은 연결하지 않았습니다.
- OpenTelemetry/Prometheus SDK를 사용한 프로젝트는 아닙니다. 먼저 추적 이벤트와 지표의 의미를 직접 구현한 버전입니다.
- 지연·알림 기준은 학습용 설정입니다. 실제 서비스의 SLO로 그대로 사용하지 않습니다.
- 짧은 실험 한 번의 p95를 운영 용량이나 안정성 보증으로 해석하지 않습니다.
- 기본 무한 수집/전체 조회는 큰 운영 데이터용이 아닙니다. 실제 적용 시 보관 기간·집계 저장소·접근 제어를 추가해야 합니다.

## 다음 직접 해볼 작업

1. [RUNBOOK.md](RUNBOOK.md)를 따라 요청 ID 하나의 실패→재시도 기록을 읽기.
2. max_attempts를 바꾸고 오류율·지연·가정 비용의 변화를 비교하기.
3. 보류 비율이 높아도 시스템 오류율이 0일 수 있는 이유 설명하기.
4. 기존 검색 서비스에 같은 관측 이벤트를 연결하기.
5. 이후 OpenTelemetry exporter와 외부 알림을 추가하고 실제 장애 대응을 검증하기.

초기 코드와 문서는 AI 코딩 도우미와 함께 작성했습니다.
직접 수정·검토·실험한 내용은 이후 커밋과 기록에 구분해서 남깁니다.

타임아웃은 작업 스레드의 결과 대기를 중단합니다. Python 스레드 자체를 강제 종료하지는 않으며, 모의 호출은 최대 1초 뒤 끝납니다. 운영 적용에는 취소 가능한 클라이언트와 동시 작업 수 제한이 필요합니다.

네트워크 제한 환경에서 서비스 로직만 측정하려면 `python run_drill.py --transport inprocess --output runs/local-direct`를 사용합니다. HTTP 전송을 검증한 결과와 구분해야 합니다.
