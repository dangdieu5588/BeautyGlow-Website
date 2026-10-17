"""Read-only checks: run python3 -B scripts/verify-monitoring.py in Ubuntu WSL."""
import argparse
import json
import subprocess
import time
import urllib.parse
import urllib.request

def api(path):
    with urllib.request.urlopen('http://127.0.0.1:19090' + path, timeout=20) as r:
        data = json.load(r)
    assert data['status'] == 'success', data
    return data['data']

def query(expression):
    result = api('/api/v1/query?' + urllib.parse.urlencode({'query': expression}))['result']
    print(expression, json.dumps(result))
    assert result, 'No samples for ' + expression
    return result

parser = argparse.ArgumentParser()
parser.add_argument('--batch43', action='store_true', help='Expect six targets and exporter metrics after batch 4.3 activation.')
args = parser.parse_args()
expected_jobs = {'prometheus', 'node-exporter', 'cadvisor'}
if args.batch43:
    expected_jobs |= {'nginx-exporter', 'mysqld-exporter', 'blackbox-wordpress'}
deadline = time.monotonic() + 60
while True:
    targets = api('/api/v1/targets')['activeTargets']
    if {t['labels']['job'] for t in targets} == expected_jobs and all(t['health'] == 'up' for t in targets):
        break
    if time.monotonic() >= deadline:
        break
    time.sleep(2)
assert {t['labels']['job'] for t in targets} == expected_jobs
for target in targets:
    print('TARGET', target['labels']['job'], target['health'], target['lastError'])
    assert target['health'] == 'up' and not target['lastError']
query('up')
if args.batch43:
    for expression in ('nginx_up{job="nginx-exporter"}', 'mysql_up{job="mysqld-exporter"}', 'probe_success{job="blackbox-wordpress"}'):
        assert all(float(item['value'][1]) == 1 for item in query(expression)), expression
    query('nginx_http_requests_total{job="nginx-exporter"}')
    query('mysql_global_status_threads_connected{job="mysqld-exporter"}')
    query('mysql_global_status_questions{job="mysqld-exporter"}')
    collector_results = query('mysql_exporter_collector_success{job="mysqld-exporter"}')
    assert {item['metric'].get('collector') for item in collector_results} == {'collect.global_status', 'collect.global_variables'}
    assert all(float(item['value'][1]) == 1 for item in collector_results)
    assert all(float(item['value'][1]) == 200 for item in query('probe_http_status_code{job="blackbox-wordpress"}'))
query('count(node_cpu_seconds_total{mode="idle"})')
memory = query('node_memory_MemTotal_bytes')[0]['value'][1]
host_memory = int(next(line.split()[1] for line in open('/proc/meminfo') if line.startswith('MemTotal:'))) * 1024
assert int(float(memory)) == host_memory, (memory, host_memory)
print('PASS node memory matches Ubuntu /proc/meminfo:', host_memory)
query('node_memory_MemAvailable_bytes')
query('sum by (service) (container_cpu_usage_seconds_total{job="cadvisor"})')
containers = query('sum by (service) (container_memory_working_set_bytes{job="cadvisor"})')
assert {item['metric'].get('service') for item in containers} == {'wordpress', 'mysql', 'nginx', 'phpmyadmin'}
for port in (18080, 18081):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=20) as r:
        assert r.status == 200
    print('HTTP', port, 200)
names = ['beautyglow-website-' + service + '-1' for service in ('wordpress','mysql','nginx','phpmyadmin','prometheus','node-exporter','cadvisor')]
if args.batch43:
    names += ['beautyglow-website-' + s + '-1' for s in ('nginx-exporter','mysqld-exporter','blackbox-exporter')]
info = json.loads(subprocess.check_output(['docker','inspect',*names]))
for item in info:
    state = item['State']
    print(item['Name'], item['Id'][:12], 'started=',state['StartedAt'], 'restarts=',item['RestartCount'], 'health=', state.get('Health',{}).get('Status','none'))
    assert state['Running'] and item['RestartCount'] == 0
    if item['Name'].endswith(('-wordpress-1','-mysql-1')):
        assert state['Health']['Status'] == 'healthy'
    if item['Name'].endswith(('-node-exporter-1','-cadvisor-1','-nginx-exporter-1','-mysqld-exporter-1','-blackbox-exporter-1')):
        assert not item['HostConfig'].get('PortBindings')
        assert not item['HostConfig']['Privileged']
        assert all('docker.sock' not in mount.get('Source','') for mount in item['Mounts'])
php = b"<?php require '/var/www/html/wp-load.php'; $p=get_posts(['post_type'=>'bg_product','post_status'=>'publish','numberposts'=>-1]); echo json_encode(array_map(fn($x)=>get_post_meta($x->ID,'_bg_code',true),$p));"
products = json.loads(subprocess.check_output(['docker','exec','-i','beautyglow-website-wordpress-1','php'], input=php))
assert len(products) == 10 and set(products) == {f'BG{i:03}' for i in range(1,11)}
print('PASS ten published products BG001–BG010, no writes.')
