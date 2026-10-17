# BeautyGlow Grafana — batch 4.4 deployed and verified

Grafana is deployed with image grafana/grafana:13.2.4 and is Healthy.
The /api/health endpoint returns HTTP 200 with database status ok.
Actual runtime identity is UID 472, GID 0. The Compose service explicitly uses 472:0.
No external dashboard plugins are required. Only built-in stat, time series and row
panels are used. Image source: https://github.com/grafana/grafana/releases/tag/v13.2.4.

URL: http://localhost:13000/ (127.0.0.1 only).
Only the existing beautyglow-website_monitoring network is attached. The datasource
uses http://prometheus:9090 through the Grafana backend; localhost would be wrong
inside its container. Grafana never connects to the WordPress MySQL database.
Its own database/plugins use beautyglow-website_grafana_data, not application volumes.

## Verified results and runtime checks

After the container CPU rate-window correction, verify-grafana.py passed five
consecutive complete runs, ten seconds apart. The Prometheus datasource health
check and provisioned dashboard API passed. The dashboard has 18 panels and 21
queries with data. Successful browser login and all 18 panels were also confirmed
by the project owner in Chrome.

All six Prometheus targets were UP: prometheus, node-exporter, cadvisor,
nginx-exporter, mysqld-exporter and blackbox-wordpress. Website and phpMyAdmin
returned HTTP 200, WordPress/MySQL remained Healthy, and ten published products
BG001–BG010 were preserved. Container IDs, start times and restart counts matched
the baseline across the final CPU-window correction and verification.

Run these read-only checks from Ubuntu. The Grafana runtime verification reads
the protected secret and therefore needs an authorized local operator, such as
running Python as root; credentials are not printed:

    cd /mnt/d/BeautyGlow-Website
    docker compose config --quiet
    sudo python3 -B scripts/verify-grafana.py
    python3 -B scripts/verify-monitoring.py --batch43

The verification script uses the existing Ubuntu python3-yaml package; it installs
nothing. It checks provisioning YAML/JSON, every PromQL expression, Grafana health,
authenticated datasource health and the provisioned dashboard API. The --prepare
mode is only for a fresh deployment before its Grafana container exists; it is not
the runtime check for this already deployed instance. Recorded PASS results do not
replace checking current availability when the system is used again.

## Administrator account and protected secret

Admin username: beautyglow_grafana_admin (separate from WordPress accounts).
Secret path: /var/lib/beautyglow-secrets/grafana-admin-password on Ubuntu ext4.
The parent is root:root 0700. The initialized secret file is 472:0 0400 and is
bind-mounted individually read-only. Docker resolves the protected host parent;
Grafana does not need to traverse it on the host. Actual file-read tests confirmed
UID472 can open it while UID12345 with the same GID0 cannot.
Credentials must never be copied to this repository, terminal output or evidence.

GF_SECURITY_ADMIN_PASSWORD__FILE points to the mounted secret. Anonymous access
and user signup are disabled. The service entrypoint refuses to boot with an empty
file, preventing accidental startup using a blank/default password. The password
was generated securely and stored outside the repository; no password value is
part of Compose, provisioning or this document.

For local login, retrieve the secret privately and remove only trailing CR/LF
before pasting it into the password field. Do not print or transmit it in chat,
screenshots or logs. Authentication was verified with the secret without its final
newline. Admin bootstrap settings initialize a new database and do not reset an
existing user's password when the file changes. Root/Docker administrators remain
able to access host secrets; file mode does not isolate from them.

## Provisioning and dashboard

Datasource UID: beautyglow-prometheus. Dashboard UID: beautyglow-overview.
Dashboard title: BeautyGlow — Tổng quan hệ thống. Folder: BeautyGlow.
Provisioning and dashboard files are read-only mounts. Refresh:15s, datasource
interval:15s. Dashboard provider polls every30s (reliable on WSL/Windows mounts),
allowUiUpdates:false, disableDeletion:true. Version-controlled files are authoritative;
UI changes cannot be saved over provisioned definitions.

The plugins/ and alerting/ provisioning directories are present with .gitkeep
files so Git preserves them. Their absence previously caused two startup errors.
After adding them, a separately approved Grafana-only restart confirmed no
missing-directory errors and no error-level lines in the new startup log window.
Old log entries before that restart were kept separate from the new result; the
ten preexisting containers were not restarted or recreated.

18 panels cover Ubuntu CPU/RAM, target scrape status, four application container
CPU/RAM and cAdvisor last-seen age, Nginx requests/connections, MySQL connections,
queries and collector success, and HTTP probe success/status/duration.

Units: host CPU/RAM percent; container CPU percent of one logical CPU (may exceed
100%); memory bytes; request/query rates per second; probe/last-seen durations in
seconds. Rate expressions keep $__rate_interval. Container CPU has a per-query
Min step of 30s, giving a minimum rate window of 2m; the verification script uses
2m for container CPU and retains 1m for the other rate queries. The datasource and
Prometheus scrape interval remain 15s.

cAdvisor exports its own sample timestamps. Observed CPU samples arrive about
15–30s apart, and a newer timestamp can reach Prometheus on the next scrape.
A live 1m range can temporarily contain only one sample even when scrapes are UP;
rate then omits that service. Re-querying the same historical time after ingestion
can show data that was not available live. The 2m window adds margin without
changing collection, permissions or containers; it smooths short CPU spikes more.
The script still requires all four expected services. On failure it reports missing
services and CPU sample counts rather than filling missing data with zero. A 2m
window is not a guarantee through WSL sleep or prolonged missing scrapes.
All queries filter project=beautyglow-website. Existing metrics only identify the
four website containers, not every monitoring container.

UP means an endpoint was scraped; it is not Docker healthy. Missing container
series must not be assumed healthy. cAdvisor freshness is observational; Docker
health/restart counts still require the existing verification script/CLI. Nginx
requests include monitoring traffic; MySQL counters include exporter queries.
Blackbox probes the internal Nginx path, not Windows port forwarding. WSL host
metrics do not represent total Windows resources. No filesystem/network panels are
invented for collectors that are not enabled. No data should stay No data.

## Redeployment and operating scope

The service is already running; no startup or password initialization is needed
to view the dashboard. For an explicitly approved future redeployment, record
application IDs/restarts and product/media fingerprints, recheck the port, verify
the protected Linux secret and inspect any existing Grafana data volume before
starting or updating only Grafana:

    docker compose up -d --no-deps grafana

No whole-project up/down is needed, and no Prometheus reload is needed. Changes
requiring a Grafana restart/recreation must be approved separately. Runtime checks
read the local secret without printing credentials:

    sudo python3 -B scripts/verify-grafana.py

Verify /api/health, authenticated datasource health, provisioned dashboard, login
and actual panels in the browser; compare dashboard values with Prometheus and
check No data/error states. Recheck six targets, HTTP18080/18081, WP/MySQL health,
ten product codes and unchanged application container IDs/restarts.
Report screenshots are a separate step and must exclude credentials. This
documentation update does not authorize container changes, password changes or
Git commit/push operations.
