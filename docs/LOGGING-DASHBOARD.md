# BeautyGlow - Logs & Security

Dashboard UID: `beautyglow-logs-security`, folder BeautyGlow.
Local URL: <http://localhost:13000/d/beautyglow-logs-security>.
Use the existing Grafana account. No credential is stored in this document.

The new JSON file in `config/grafana/dashboards/` is managed by the existing
BeautyGlow file provider (30-second polling). No container restart, datasource
change or provider change was needed. The Prometheus dashboard is unchanged.

## Panels and filters

14 panels: scope notes; total/error-keyword/warning-keyword stats; total volume
and per-service time series; stdout/stderr distribution; service/stream totals;
nginx 4xx/5xx time series; nginx status totals; error and warning keyword series
by service; detailed log table; expandable log explorer. Both detail views are
capped at 500 rows. Metric counts use the full selected interval, not that limit.

Service and Stream are chained Loki label-query variables with multi-select and
All support. Every data query respects both filters, including nginx HTTP panels.
HTTP panels are empty/zero when filters exclude nginx stdout. Zero matches does
not prove absence of attacks or complete Docker source coverage.

The default absolute time range is the verified dataset, 2026-10-19
10:13:34Z–18:16:40Z, rather than an empty recent interval while Bridge is stopped.
The browser displays timestamps in its local timezone. Select another interval
to inspect later runs; refresh does not cause Bridge collection. Retention may
eventually remove this historical sample.

## Evidence-based parsing

JSONL has timestamp/project/service/stream/message. Loki indexed labels observed
were project/service/stream/service_name. The dashboard does not assume a level,
HTTP status or other structured application field exists. Alloy emits the message
as the Loki log line, so JSON parsing of that line would be incorrect.

HTTP status is extracted with an anchored regexp matching the observed combined
access-log prefix. In the dataset, 1,072 of 1,088 nginx stdout lines matched:
200=1,070; 302=1; 500=1; no observed 4xx. The other 16 stdout lines are excluded
from HTTP metrics but remain visible/countable in general log panels. Parser
errors are excluded with `__error__=""`.

Error detection is the case-insensitive word set error/fatal/critical/crit/alert/
emerg/panic. Warning detection is warn/warning. They are content heuristics with
false positives/negatives, not authoritative severity labels; stderr alone is
not classified as an error. The current sample yields 2 nginx error-keyword lines
and 4 MySQL warning-keyword lines. No intrusion-detection claim is made.

## Verified results

Verified via real Loki queries through the Grafana datasource proxy:

| Service | stdout | stderr | Total |
|---|---:|---:|---:|
| nginx | 1,088 | 217 | 1,305 |
| wordpress | 2,630 | 10 | 2,640 |
| mysql | 8 | 24 | 32 |
| Total | 3,726 | 251 | 3,977 |

All 15 dashboard queries succeeded (14 panels; pie has separate stdout/stderr queries), including instant metrics, time series and
both log views. All six service/stream combinations matched source JSONL counts.
Label variable endpoints returned the expected three services and two streams.
Grafana `/api/ds/query` returned actual Time/Line frames for both detail views;
raw messages and secrets were not printed. File provisioning reported provisioned
and the saved dashboard panels/variables matched the source JSON. After the display correction, the user confirmed successful browser visual
acceptance. API/frame checks also verify time/value series and separate pie
values (stdout 3,726, stderr 251). No official evidence screenshot was captured.

Existing Prometheus dashboard expressions remained identical, all 21 expressions
returned data, and all 6 targets were up. Its file SHA-256 remained
`041391062f76e52d656b6dde5a9d1d9bf9451e01a725f2c6d7838b15b6a65ee6`.

`scripts/verify-logging-dashboard.py` reproduces these read-only checks against
the approved fixed snapshot. Run in an existing Ubuntu session with an operator
permitted to read the private JSONL and existing local Grafana credential. It
never starts services, writes data/config, runs Bridge or prints log content.
Its only POST is a Grafana datasource query (read-only), not a write API.

References:
- <https://grafana.com/docs/loki/latest/query/log_queries/>
- <https://grafana.com/docs/grafana/latest/datasources/loki/template-variables/>
