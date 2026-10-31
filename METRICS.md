# 지표 정의

| 지표 | 분자 / 분모 또는 계산 |
|---|---|
| eligible_requests | 입력이 유효한 요청 수. 재시도는 요청 수를 늘리지 않음 |
| system_error_rate | timeout + upstream_error + rate_limited + empty_response / eligible |
| valid_response_rate | success + hold / eligible |
| answer_rate | success / eligible |
| hold_rate | hold / eligible |
| latency_p50/p95_ms | 모든 eligible 요청의 서버 처리 시간. 오류 포함 |
| answer_latency_p95_ms | success 요청만의 처리 시간 |
| provider_attempts | 최초 호출과 재시도 횟수의 합 |
| simulated_cost_usd | 공급자 시도 횟수 × 가정 단가 $0.0002. 실패 시도도 포함 |
| simulated_cost_per_answer_usd | 오류 요청 비용을 포함한 전체 가정 비용 / success 수 |
| error_budget_remaining_requests | eligible × (1 - 0.95) - 시스템 오류 수 |

분모가 0이면 비율·백분위·답변당 비용은 null입니다.
오류 예산은 음수가 될 수 있습니다. 전체 실험 구간을 대상으로 하며 월간 운영 SLO가 아닙니다.
백분위는 정렬된 표본 사이의 선형 보간입니다.
서버 시간은 검증부터 결과 결정까지이며 최종 SQLite 쓰기/네트워크 전송은 제외합니다.
클라이언트 시간은 HTTP 왕복과 저장 시간을 포함합니다. 두 값을 같은 지표처럼 비교하지 않습니다.

## 알림
최근 완료된 유효 요청 10건 중 오류 비율이 0.2 이상이면 firing.
10건 미만이면 insufficient_data. 0.2 미만이면 ok.
완료 순서로 계산하므로 동시 실행에서는 질의 제출 순서와 다를 수 있습니다.
실험 관측기는 응답 완료 후 DB를 읽으므로 한 번의 관측 사이에 여러 요청이 끝날 수 있습니다.
변화가 관측된 시점과 마지막으로 보인 request_id를 기록하며 정확한 최초 장애 시각이라고 주장하지 않습니다.

회복 구간에서 최근 10건이 정상이어도 누적 오류 예산은 적자일 수 있습니다.
이는 지표 오류가 아니라 전체 구간과 이동 창의 차이입니다.
