# BeautyGlow exporters — batch 4.3 active

## Services and access

- nginx-exporter: nginx/nginx-prometheus-exporter:1.5.3, internal port 9113.
- mysqld-exporter: prom/mysqld-exporter:v0.20.0, internal port 9104.
- blackbox-exporter: prom/blackbox-exporter:v0.29.0, internal port 9115.

None publishes a host port. Prometheus remains at http://localhost:19090/.
The active prometheus.yml now matches prometheus.batch43.yml and has six jobs:
prometheus, node-exporter, cadvisor, nginx-exporter, mysqld-exporter and
blackbox-wordpress. The last job probes the website, rather than collecting the
blackbox process metrics; its own /metrics endpoint has also been checked.

## Nginx

The approved server on port 8081 provides /stub_status on the existing frontend
Docker network. Port 8081 is not published; public port 18080 does not expose
stub_status output. Configuration was tested and gracefully reloaded in an earlier
step. This activation did not restart or recreate Nginx.

The pinned 1.5.3 Nginx image reports a build version beginning 0.0.0-SNAPSHOT and
revision 9956da7 in its own startup log; the configured image tag is still 1.5.3.

## MySQL credential protection

Credentials live outside the repository on Ubuntu ext4:
/var/lib/beautyglow-secrets/mysqld-exporter.cnf.
Directory: root:root 0700. File: 65534:65534 0400. Docker daemon resolves the host
path and bind-mounts the individual file read-only at /run/secrets/mysqld-exporter.cnf;
the exporter need not traverse the host parent directory. Reading the file was
verified inside the running exporter: UID 65534 succeeds, UID 12345 is denied.
Never copy its contents into documentation, Git, command arguments or logs.
The older D: credentials placeholder is empty and is not mounted.

The existing account beautyglow_metrics@172.22.0.0/16 has only USAGE,
MAX_USER_CONNECTIONS 3, and caching_sha2_password. Only global_status and
global_variables collectors are enabled. No root credentials enter the exporter.
No account, grant or application data was changed during exporter activation.
Recheck the Host restriction if the database network is ever recreated on a new
subnet. Do not broaden privileges to fix a scrape without reviewing the error.

## Blackbox

Probes http://nginx:80/ with Host localhost:18080, HTTP 200 required,
redirects disabled, and BeautyGlow required in the response body. This covers the
internal reverse-proxy path; Windows-to-WSL access is checked separately.

## Validation

Run from Ubuntu at /mnt/d/BeautyGlow-Website:

    docker compose config --quiet
    docker compose exec -T prometheus promtool check config /etc/prometheus/prometheus.yml
    python3 -B scripts/verify-monitoring.py --batch43
    docker compose ps

Inspect http://localhost:19090/targets for six UP targets. Useful queries:

    nginx_up
    nginx_connections_active
    nginx_http_requests_total
    mysql_up
    mysql_global_status_threads_connected
    mysql_exporter_collector_success
    probe_success{job="blackbox-wordpress"}
    probe_http_status_code{job="blackbox-wordpress"}

mysqld_exporter v0.20.0 exposes mysql_exporter_collector_success, not the older
mysql_exporter_last_scrape_error metric. Require success=1 for both
collect.global_status and collect.global_variables, plus mysql_up=1.

The candidate was validated with promtool before being applied; Prometheus was
reloaded with SIGHUP. A copy of the previous active config is in ignored
/tmp/prometheus-before-batch43.yml relative to this repository (the tmp directory).
Do not run a whole-project up/down to update these exporters. Target services:

    docker compose up -d --no-deps nginx-exporter mysqld-exporter blackbox-exporter

## Limits

Nginx stub_status gives aggregate requests/connections, not HTTP-status or latency
histograms. MySQL collectors do not expose per-table or replication dashboards.
Blackbox UP means the probe endpoint was scraped; probe_success=1 must also be
checked. Existing cAdvisor raw-cgroup ID filters still need refreshing if website
containers are recreated. Linux root/Docker administrators can access mounted
secrets; file mode is not isolation from a host administrator.
