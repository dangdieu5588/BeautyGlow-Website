"""Offline checks only: no Docker/API access, notifications, reload or service start.

Run in Ubuntu with python3-yaml and native promtool/amtool binaries:
  python3 -I -B scripts/verify-alerting.py --promtool /path/promtool --amtool /path/amtool
Only temporary validation files are written; production configuration is not edited.
"""
import argparse
import copy
import json
from pathlib import Path
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[1]


def read_yaml(path):
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--promtool", required=True)
    parser.add_argument("--amtool", required=True)
    args = parser.parse_args()
    promtool = str(Path(args.promtool).resolve(strict=True))
    amtool = str(Path(args.amtool).resolve(strict=True))
    compose = read_yaml("compose.yaml")
    prom = read_yaml("config/prometheus/prometheus.yml")
    am = read_yaml("config/alertmanager/alertmanager.yml")
    service = compose["services"]["alertmanager"]
    assert service["networks"] == ["monitoring"] and not service.get("ports")
    assert service["user"] == "65534:65534" and service["read_only"]
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert "--cluster.listen-address=" in service["command"]
    assert not service.get("privileged") and not service.get("environment")
    assert "docker.sock" not in json.dumps(service)
    smtp_mount = next(v for v in service["volumes"]
                      if isinstance(v, dict) and v.get("target") == "/etc/alertmanager/alertmanager.yml")
    assert smtp_mount["source"] == "/var/lib/beautyglow-secrets/alertmanager-gmail.yml"
    assert smtp_mount["read_only"] and smtp_mount["bind"]["create_host_path"] is False
    # The repository config is the safe no-delivery fallback, not the Gmail
    # runtime file. This offline script must never read production credentials.
    assert am["receivers"] == [{"name": "notifications-not-configured"}]
    assert am["route"]["receiver"] == "notifications-not-configured"
    assert prom["alerting"]["alertmanagers"] == [
        {"api_version": "v2", "static_configs": [{"targets": ["alertmanager:9093"]}]}
    ]
    rule_path = ROOT / "config/prometheus/rules/beautyglow-alerts.yml"
    assert prom["rule_files"] == ["/etc/prometheus/rules/beautyglow-alerts.yml"]
    assert not (ROOT / "config/prometheus/rules/beautyglow-email-test.yml").exists()
    mount = next(v for v in compose["services"]["prometheus"]["volumes"]
                 if isinstance(v, dict) and v.get("target") == "/etc/prometheus/rules")
    assert mount["source"] == "./config/prometheus/rules" and mount["read_only"]
    assert mount["bind"]["create_host_path"] is False
    print("PASS isolation, protected SMTP mount, rules mount, test cleanup and safe fallback")

    # Synthetic inputs cover firing delays, recovery, exporter/backend distinction,
    # missing metrics and isolation from other projects. No production samples used.
    specs = [
        ("mysqld-exporter", "db:9104", "mysql_up", "BeautyGlowMySQLDown", "mysql"),
        ("nginx-exporter", "web:9113", "nginx_up", "BeautyGlowNginxDown", "nginx"),
        ("blackbox-wordpress", "http://nginx:80/", "probe_success", "BeautyGlowHTTPProbeFailed", "wordpress"),
    ]

    def inputs(up_values, backend_values, project="beautyglow-website"):
        rows = []
        for job, instance, metric, _, _ in specs:
            labels = f'project="{project}",job="{job}",instance="{instance}"'
            rows.append({"series": "up{" + labels + "}", "values": up_values})
            if backend_values is not None:
                rows.append({"series": metric + "{" + labels + "}", "values": backend_values})
        return rows

    def expected(kind, state="firing"):
        samples = []
        for job, instance, _, name, service_name in specs:
            labels = {"__name__": "ALERTS", "alertname": name,
                      "alertstate": state, "project": "beautyglow-website",
                      "job": job, "instance": instance,
                      "severity": "critical", "service": service_name}
            if kind == "targets":
                labels.pop("service")
                labels.update(alertname="BeautyGlowTargetDown", severity="warning")
            metric = labels.pop("__name__")
            selector = metric + "{" + ",".join(
                key + "=" + json.dumps(value) for key, value in sorted(labels.items())
            ) + "}"
            samples.append({"labels": selector, "value": 1})
        return samples

    cases = []

    def case(name, rows, checks):
        cases.append({"name": name, "interval": "15s", "input_series": rows,
                      "promql_expr_test": [
                          {"expr": 'ALERTS{alertstate="' + state + '"}',
                           "eval_time": when, "exp_samples": samples}
                          for when, state, samples in checks]})

    case("healthy", inputs("1+0x24", "1+0x24"), [("3m", "firing", [])])
    case("short failure recovers before for", inputs("1+0x24", "0+0x2 1+0x21"),
         [("30s", "pending", expected("services", "pending")),
          ("45s", "firing", []), ("3m", "firing", [])])
    case("backend failures respect different for durations", inputs("1+0x24", "0+0x24"),
         [("45s", "firing", []),
          ("1m", "firing", expected("services")[:2]),
          ("2m", "firing", expected("services"))])
    case("backend recovery clears firing alerts", inputs("1+0x24", "0+0x8 1+0x15"),
         [("2m", "firing", expected("services")), ("2m15s", "firing", [])])
    case("exporter down emits target alerts only", inputs("0+0x24", "0+0x24"),
         [("1m45s", "firing", []), ("2m", "firing", expected("targets"))])
    case("exporter recovery clears target alerts", inputs("0+0x8 1+0x15", "1+0x24"),
         [("2m", "firing", expected("targets")), ("2m15s", "firing", [])])
    case("other project excluded", inputs("0+0x24", "0+0x24", "lab"),
         [("3m", "firing", [])])
    case("missing backend metric is not invented as down", inputs("1+0x24", None),
         [("3m", "firing", [])])

    with tempfile.TemporaryDirectory(prefix="beautyglow-alert-check-") as tmp:
        temp = Path(tmp)
        config = copy.deepcopy(prom)
        # Production paths are container paths. Resolve only the temporary copy
        # for native host validation, keeping all other settings unchanged.
        config["rule_files"] = [str(rule_path)]
        (temp / "prometheus.yml").write_text(yaml.safe_dump(config), encoding="utf-8")
        (temp / "tests.yml").write_text(yaml.safe_dump({
            "rule_files": [str(rule_path)], "evaluation_interval": "15s", "tests": cases
        }), encoding="utf-8")
        for command in [
            [promtool, "check", "config", str(temp / "prometheus.yml")],
            [promtool, "check", "rules", str(rule_path)],
            [amtool, "check-config", str(ROOT / "config/alertmanager/alertmanager.yml")],
            [promtool, "test", "rules", str(temp / "tests.yml")],
        ]:
            subprocess.run(command, check=True, timeout=60)
    print(f"PASS {len(cases)} synthetic scenarios / {sum(len(c['promql_expr_test']) for c in cases)} assertions")


if __name__ == "__main__":
    main()
