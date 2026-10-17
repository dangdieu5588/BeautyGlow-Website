"""Run in Ubuntu: python3 -B scripts/verify-grafana.py --prepare.

Omit --prepare only after Grafana activation. Read-only checks; never starts a
service, writes WordPress, creates passwords or prints authentication material.
Requires the existing Ubuntu python3-yaml package. Runtime auth reads the Linux
secret file locally; use an authorized local operator with access to that file.
"""
import argparse
import base64
import json
from pathlib import Path
import subprocess
import urllib.parse
import urllib.request
import yaml

ROOT = Path(__file__).resolve().parents[1]
SECRET = Path('/var/lib/beautyglow-secrets/grafana-admin-password')
DS_UID = 'beautyglow-prometheus'
DASH_UID = 'beautyglow-overview'
parser = argparse.ArgumentParser()
parser.add_argument('--prepare', action='store_true', help='Validate files and PromQL without contacting Grafana or reading its password.')
args = parser.parse_args()

def get_json(url, data=None, headers=None):
    request = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(request, timeout=20) as response:
        assert response.status == 200
        return json.load(response)

ds = yaml.safe_load((ROOT / 'config/grafana/provisioning/datasources/prometheus.yml').read_text())
provider = yaml.safe_load((ROOT / 'config/grafana/provisioning/dashboards/beautyglow.yml').read_text())
dashboard = json.loads((ROOT / 'config/grafana/dashboards/beautyglow-overview.json').read_text())
compose = json.loads(subprocess.check_output(['docker', 'compose', '-f', str(ROOT / 'compose.yaml'), 'config', '--format', 'json']))
service = compose['services']['grafana']
assert service['image'] == 'grafana/grafana:13.2.4' and service['user'] == '472:0'
assert set(service['networks']) == {'monitoring'}
assert len(service['ports']) == 1 and service['ports'][0]['host_ip'] == '127.0.0.1'
assert str(service['ports'][0]['published']) == '13000' and service['ports'][0]['target'] == 3000
assert ds['apiVersion'] == 1 and len(ds['datasources']) == 1
assert ds['datasources'][0]['uid'] == DS_UID
assert ds['datasources'][0]['url'] == 'http://prometheus:9090'
assert ds['datasources'][0]['access'] == 'proxy' and ds['datasources'][0]['jsonData']['timeInterval'] == '15s'
assert ds['datasources'][0]['editable'] is False
p = provider['providers'][0]
assert provider['apiVersion'] == 1 and p['allowUiUpdates'] is False and p['disableDeletion'] is True
assert p['updateIntervalSeconds'] == 30 and p['options']['path'] == '/etc/grafana/dashboards'
assert dashboard['uid'] == DASH_UID and dashboard['refresh'] == '15s'
assert len({p['id'] for p in dashboard['panels']}) == len(dashboard['panels'])

prom = 'http://127.0.0.1:19090'
targets = get_json(prom + '/api/v1/targets')['data']['activeTargets']
assert len(targets) == 6 and all(t['health'] == 'up' for t in targets)
expressions = []
for panel in dashboard['panels']:
    if panel['type'] == 'row':
        continue
    assert panel['datasource']['uid'] == DS_UID
    assert panel['gridPos']['x'] + panel['gridPos']['w'] <= 24
    for target in panel['targets']:
        # cAdvisor sample timestamps lag scrapes: use >=2m for CPU rates only.
        cpu_rate = 'rate(container_cpu_usage_seconds_total{' in target['expr']
        rate_window = '2m' if cpu_rate else '1m'
        expression = target['expr'].replace('$__rate_interval', rate_window)
        assert '$' not in expression
        result = get_json(prom + '/api/v1/query?' + urllib.parse.urlencode({'query': expression}))
        assert result['status'] == 'success', 'Query failed: ' + panel['title']
        if 'container_' in expression:
            expected_services = {'wordpress', 'mysql', 'nginx', 'phpmyadmin'}
            observed_services = {x['metric'].get('service') for x in result['data']['result']}
            if observed_services != expected_services:
                counts = {}
                if cpu_rate:
                    sample_query = 'count_over_time(container_cpu_usage_seconds_total{project="beautyglow-website",job="cadvisor"}[' + rate_window + '])'
                    sample_result = get_json(prom + '/api/v1/query?' + urllib.parse.urlencode({'query': sample_query}))
                    for item in sample_result['data']['result']:
                        service_name = item['metric'].get('service')
                        counts.setdefault(service_name, []).append(float(item['value'][1]))
                observed_counts = {name: counts.get(name, [0]) for name in sorted(expected_services)}
                raise AssertionError(
                    'Container services mismatch in ' + panel['title']
                    + '; missing=' + repr(sorted(expected_services - observed_services))
                    + '; unexpected=' + repr(sorted(map(str, observed_services - expected_services)))
                    + '; CPU sample counts per series in ' + rate_window + '=' + repr(observed_counts)
                )
        assert result['data']['result'], 'No data: ' + panel['title']
        if target['expr'].startswith('up{'):
            assert len(result['data']['result']) == 6
        expressions.append(target['expr'])
    print('PASS panel / PromQL:', panel['title'])
print('PASS Compose, provisioning YAML, dashboard JSON, six targets and', len(expressions), 'PromQL expressions.')

if args.prepare:
    names = subprocess.check_output(['docker', 'ps', '-a', '--filter', 'label=com.docker.compose.project=beautyglow-website', '--format', '{{.Names}}'], text=True).splitlines()
    assert 'beautyglow-website-grafana-1' not in names, 'Grafana already exists; review preparation-only scope.'
    print('PASS preparation only: no Grafana service container exists; no password read.')
else:
    health = get_json('http://127.0.0.1:13000/api/health')
    assert health.get('database') == 'ok' and health.get('version') == '13.2.4'
    password = SECRET.read_text().strip()
    assert password, 'Grafana password is still empty; do not attempt login.'
    token = base64.b64encode(('beautyglow_grafana_admin:' + password).encode()).decode()
    headers = {'Authorization': 'Basic ' + token}
    base = 'http://127.0.0.1:13000'
    source = get_json(base + '/api/datasources/uid/' + DS_UID, headers=headers)
    assert source['url'] == 'http://prometheus:9090' and source['access'] == 'proxy'
    ds_health = get_json(base + '/api/datasources/uid/' + DS_UID + '/health', headers=headers)
    assert ds_health.get('status') == 'OK'
    saved = get_json(base + '/api/dashboards/uid/' + DASH_UID, headers=headers)
    assert saved['dashboard']['title'] == dashboard['title'] and saved['meta'].get('provisioned')
    print('PASS Grafana health, authenticated datasource health and provisioned dashboard API.')
    print('Browser rendering and login still require a visual check.')
