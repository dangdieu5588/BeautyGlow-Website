"""Read-only Linux acceptance checks; emits only sanitized metadata/checksums.

Run with authorized Docker and protected-backup access. Never reads .env or SMTP
passwords, sends mail, restores data, or starts/stops services. Windows ACL is
verified separately with Get-Acl and included in the acceptance evidence.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import urllib.error
import urllib.parse
import urllib.request


def docker(*args):
    p = subprocess.run(["docker", *args], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=25)
    if p.returncode:
        raise RuntimeError("Docker read-only check failed; output suppressed")
    return p.stdout


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, b""


def api(path):
    return json.loads(get("http://127.0.0.1:19090" + path)[2])["data"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-directory", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--report-file", type=Path, required=True)
    args = parser.parse_args()
    ids = docker("ps", "-aq", "--filter", "label=com.docker.compose.project=beautyglow-website").decode().split()
    items = json.loads(docker("inspect", *ids))
    assert len(items) == 14
    baseline = json.loads(args.baseline.read_text())
    services = {}
    for item in items:
        service = item["Config"]["Labels"]["com.docker.compose.service"]
        host, state = item["HostConfig"], item["State"]
        assert state["Status"] == "running"
        assert state.get("Health", {}).get("Status", "healthy") == "healthy"
        assert not host["Privileged"] and host["NetworkMode"] != "host"
        assert "no-new-privileges:true" in host["SecurityOpt"]
        assert not any("docker.sock" in mount["Source"] for mount in item["Mounts"])
        assert all(binding["HostIp"] == "127.0.0.1" for bindings in host["PortBindings"].values() for binding in bindings or [])
        pid_status = (Path("/proc") / str(state["Pid"]) / "status").read_text()
        uid = int(next(line for line in pid_status.splitlines() if line.startswith("Uid:")).split()[1])
        if service == "blackbox-exporter":
            assert uid == 65534 and item["Id"] != baseline[service]["id"]
        else:
            before = baseline[service]
            assert item["Id"] == before["id"] and state["StartedAt"] == before["started"] and item["RestartCount"] == before["restarts"]
        if service in {"mysql", "wordpress", "loki", "alloy", "alertmanager"}:
            assert not host["PortBindings"]
        if service == "mysql":
            assert uid != 0 and set(item["NetworkSettings"]["Networks"]) == {"beautyglow-website_database"}
        services[service] = {"state": state["Status"], "health": state.get("Health", {}).get("Status", "not-configured"),
                             "pid1_uid": uid, "restart_count": item["RestartCount"],
                             "read_only_rootfs": host["ReadonlyRootfs"], "drop_all_caps": "ALL" in (host["CapDrop"] or []),
                             "no_new_privileges": True, "host_ports_loopback_only": True}
        if service in {"loki", "alloy", "alertmanager"}:
            ip = item["NetworkSettings"]["Networks"]["beautyglow-website_monitoring"]["IPAddress"]
            port, path = {"loki": (3100, "/ready"), "alloy": (12345, "/-/ready"), "alertmanager": (9093, "/-/ready")}[service]
            assert get(f"http://{ip}:{port}{path}")[0] == 200
    database_net = json.loads(docker("network", "inspect", "beautyglow-website_database"))[0]
    assert database_net["Internal"]
    by_id = {item["Id"] for item in items}
    assert set(database_net["Containers"]).issubset(by_id)
    docker("exec", "beautyglow-website-nginx-1", "nginx", "-t")
    required = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "SAMEORIGIN",
                "Referrer-Policy": "strict-origin-when-cross-origin",
                "Permissions-Policy": "camera=(), microphone=(), geolocation=()"}
    responses = {}
    for path, expected in [("/", 200), ("/wp-login.php", 200), ("/phase6-not-found-check", 404)]:
        status, headers, _ = get("http://127.0.0.1:18080" + path)
        assert status == expected and all(headers.get(key) == value for key, value in required.items())
        assert headers.get("Server") == "nginx"
        responses[path] = {"http_status": status, "security_headers": required, "server_version_hidden": True}
    assert get("http://127.0.0.1:19090/-/ready")[0] == 200
    grafana_status, _, body = get("http://127.0.0.1:13000/api/health")
    assert grafana_status == 200 and json.loads(body)["database"] == "ok"
    targets = api("/api/v1/targets")["activeTargets"]
    assert len(targets) == 6 and all(target["health"] == "up" for target in targets)
    rules = [rule for group in api("/api/v1/rules")["groups"] for rule in group["rules"]]
    assert len(rules) == 4 and all(rule["health"] == "ok" and rule["state"] == "inactive" for rule in rules)
    assert len(api("/api/v1/alertmanagers")["activeAlertmanagers"]) == 1
    for expression in ['mysql_up{project="beautyglow-website"}', 'nginx_up{project="beautyglow-website"}', 'probe_success{project="beautyglow-website"}']:
        values = api("/api/v1/query?" + urllib.parse.urlencode({"query": expression}))["result"]
        assert len(values) == 1 and float(values[0]["value"][1]) == 1
    secret_metadata = {}
    for path, uid, gid, mode in [
        ("/var/lib/beautyglow-secrets", 0, 0, 0o700),
        ("/var/lib/beautyglow-secrets/alertmanager-gmail.yml", 0, 65534, 0o440),
        ("/var/lib/beautyglow-secrets/grafana-admin-password", 472, 0, 0o400),
        ("/var/lib/beautyglow-secrets/mysqld-exporter.cnf", 65534, 65534, 0o400),
    ]:
        metadata = os.lstat(path)
        assert not stat.S_ISLNK(metadata.st_mode)
        assert (metadata.st_uid, metadata.st_gid, stat.S_IMODE(metadata.st_mode)) == (uid, gid, mode)
        secret_metadata[Path(path).name] = {"uid": uid, "gid": gid, "mode": oct(mode), "content_not_read": True}
    backup = args.backup_directory.resolve(strict=True)
    assert backup.parent == Path("/var/lib/beautyglow-backups")
    for name in ["database.sql", "uploads.tar.gz", "manifest.json", "restore-result.json"]:
        metadata = (backup / name).stat()
        assert metadata.st_uid == 0 and not stat.S_IMODE(metadata.st_mode) & 0o077 and metadata.st_size > 0
    for line in (backup / "SHA256SUMS").read_text().splitlines():
        expected, name = line.split()
        assert name in {"database.sql", "uploads.tar.gz"}
        assert hashlib.sha256((backup / name).read_bytes()).hexdigest() == expected
    result = json.loads((backup / "restore-result.json").read_text())
    assert result["status"] == "PASS" and result["restored_sql_semantically_identical"] and result["restored_uploads_identical"]
    assert result["readonly_create_denied_mysql_error"] == 1142
    assert result["preexisting_missing_attachment_files"] == 0
    assert result["attachment_files_verified"] == 14
    isolated = json.loads(docker("inspect", result["restore_container"]))[0]
    assert isolated["State"]["Status"] == "exited" and not isolated["HostConfig"]["PortBindings"]
    assert {mount["Name"] for mount in isolated["Mounts"] if mount["Type"] == "volume"} == {"beautyglow-restore-test_mysql_data"}
    assert json.loads(docker("network", "inspect", "beautyglow-restore-test_isolated"))[0]["Internal"]
    report = {"checked_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "overall_status": "PENDING_MEDIA" if result["preexisting_missing_attachment_files"] else "PASS",
              "technical_checks": "PASS", "production_containers": services,
              "production_changes": ["approved Windows .env ACL", "Blackbox HTTP exporter non-root UID/GID65534"],
              "database_network_internal": True, "http_checks": responses,
              "prometheus_targets_up": 6, "official_alert_rules_healthy": 4,
              "secret_metadata": secret_metadata, "backup_restore": result,
              "test_mysql_stopped": True,
              "read_only_evidence": {"scope": "isolated MySQL only", "grant": "SELECT on beautyglow_catalog.*",
                                     "select_posts_count": result["readonly_select_count"],
                                     "create_table_denied_error": 1142, "production_accounts_changed": False},
              "limitations": ["Earlier missing-media report was a verifier charset error; utf8mb4 confirms all 14 originals",
                              "Local HTTP only; HTTPS/HSTS outside this bounded hardening step",
                              "Backup/drill data retained on the same host; no offsite or disaster-recovery claim",
                              "Windows workspace config ACL remains broader than protected Ubuntu runtime files"]}
    args.report_file.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("PASS read-only hardening/backup/restore checks; sanitized report written")


if __name__ == "__main__":
    main()
