# BeautyGlow Loki — trạng thái Giai đoạn 5

Đã triển khai Loki 3.7.0 bằng image digest trong Compose, UID/GID 10001:10001,
root filesystem read-only, cap_drop ALL, no-new-privileges. Giới hạn thực tế
Compose: RAM 512 MiB, CPU 0.5, PID 128. Không publish cổng host.
Volume riêng hiện dùng: `beautyglow-logging-loki-data` (external).

Đã truy vấn đủ 3.977 dòng theo service/stream qua Loki và Grafana.
Retention 168h không phải quota dung lượng; auth_enabled=false nên mạng Docker
monitoring phải được coi là vùng tin cậy. Bridge không chạy thường trực.

Xem [runbook](../../docs/LOGGING-OPERATIONS.md) và
[dashboard](../../docs/LOGGING-DASHBOARD.md) cho thao tác hiện hành.

## Hồ sơ chuẩn bị 5.2.1 (lịch sử, chưa áp dụng tại thời điểm viết)

# BeautyGlow Loki — prepared only, slice 5.2.1

`loki.yml` is a candidate configuration for `grafana/loki:3.7.0`. No image has been
pulled or started. Loki readiness, ingestion, LogQL and Grafana integration are
NOT verified. No existing Compose/Grafana/Prometheus configuration is changed.

The candidate is single binary (`target=all` when later approved), replication
factor 1, filesystem storage under `/loki`, TSDB schema v13 and a 24h index period.
The schema start is a fixed past date for this new empty Loki store, not a request
to migrate any Lab store. It needs a separate `beautyglow-website_loki_data` volume;
never attach an existing Lab, MySQL, WordPress or Grafana volume.

Compactor retention is enabled with `retention_period: 168h`, a filesystem delete
request store and a small deletion-worker count. Retention affects only this new
Loki store. Deletion is asynchronous and is not an exact seven-day disk quota.
Source Docker rotation can remove records before ingestion; Loki cannot recover
them. Logs older than 168h are rejected by this candidate config.

Official reference:
https://grafana.com/docs/loki/latest/operations/storage/retention/

Alloy will push to `http://loki:3100/loki/api/v1/push`; Grafana would later query
`http://loki:3100` through an independently approved datasource. No datasource
provisioning file is created in this slice. The existing Prometheus datasource,
18 display panels / 21 queries and Grafana data volume stay untouched.

Single-tenant `auth_enabled: false` supplies no authentication boundary. Keep Loki
off host ports on a trusted BeautyGlow Docker network. An untrusted container on
that network could read or submit logs; network membership must be reviewed.
No credentials, Docker socket, application database mounts or host-root mounts
are required by Loki or Alloy.

For later approval, budget approximately 1 GiB RAM / 1 CPU for Loki and 256 MiB /
0.5 CPU for Alloy; these are proposals, not configured/enforced limits. Disk usage
must be monitored independently of retention. Current ingestion/query settings
are candidate limits, not runtime performance evidence. Image digest, UID/GID,
mount permissions, healthchecks and resource behavior remain unverified.

Later validation must check YAML, then the exact Loki binary with
`-config.file=<candidate> -verify-config=true`. Alloy likewise needs its own
`validate` command. Neither command has been run via an image here because
obtaining/starting containers is outside this slice's permission.

Activation, authenticated Grafana provisioning, storage/retention validation,
LogQL, failure recovery and regression checks require separate approval.
Rollback would stop only newly approved logging components and retain all volumes
and application data. No down/prune/reset procedure is proposed.
