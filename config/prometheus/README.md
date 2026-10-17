# BeautyGlow monitoring — batch 4.2

Prometheus: http://localhost:19090/ (published on loopback only).
Targets: http://localhost:19090/targets.

Pinned images: prom/prometheus:v3.15.0, prom/node-exporter:v1.12.1,
ghcr.io/google/cadvisor:v0.60.6. Scrape/evaluation interval: 15 seconds.
Retention: 7 days or 2GB, whichever is reached first. The size setting bounds
retained blocks, not a strict total-volume quota; WAL/head can add disk usage.
Data volume: beautyglow-website_prometheus_data.

Node exporter reads Ubuntu WSL /proc and /sys read-only with host PID namespace.
Only CPU, memory, load, stat, time, uname, vmstat and pressure collectors are on.
Host network/filesystem collectors are deliberately not enabled; this does not
measure Windows resources or pretend the bridge network is the host network.

cAdvisor uses the host cgroup namespace and read-only /sys. It has no privileged
mode, added capabilities, Docker socket, Docker data-root or host-root mount.
Raw cgroup CPU/memory are sufficient for this batch. Docker factory connection
warnings are expected because the socket is intentionally absent. Docker image
metadata, per-container network and filesystem metrics are not provided.

Prometheus keeps only the four BeautyGlow application cgroup IDs and adds a
service label. Other cgroup samples (including Labs) are dropped before storage.
cAdvisor itself can inspect host cgroups: the filter is not a host access boundary.
If WordPress/MySQL/Nginx/phpMyAdmin are recreated, update the ID allowlist and
service mappings in prometheus.yml before expecting their metrics. No Docker API
credential or socket is used for automatic discovery.

Validation from Ubuntu, at /mnt/d/BeautyGlow-Website:

    docker compose config --quiet
    docker compose exec -T prometheus promtool check config /etc/prometheus/prometheus.yml
    python3 -B scripts/verify-monitoring.py --batch43
    docker compose ps

Only start/update the monitoring services (never the full project for this step):

    docker compose up -d --no-deps prometheus node-exporter cadvisor

Evidence views: Targets with three UP jobs; query `up`; query
`node_memory_MemTotal_bytes`; query `rate(node_cpu_seconds_total[2m])`;
query `sum by (service) (container_memory_working_set_bytes{job="cadvisor"})`;
query `sum by (service) (rate(container_cpu_usage_seconds_total{job="cadvisor"}[2m]))`.
Wait at least two scrape intervals before rate queries; after WSL resumes allow
the services and scrapes to settle. Do not change WSL lifecycle settings here.

Batch 4.3 is now active: see ../exporters/README.md for the three additional
jobs and Linux credential handling. Grafana is not deployed. Existing application
services were not restarted or recreated by exporter activation.
