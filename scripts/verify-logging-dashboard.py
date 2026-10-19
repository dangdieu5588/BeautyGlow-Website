"""Read-only live verification. No raw logs or authentication material emitted.
Run in existing Ubuntu as an operator permitted to read Grafana's local secret
and private JSONL. Does not start services, run Bridge or change provisioning.
"""
import base64
import hashlib
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / 'config/grafana/dashboards/beautyglow-logs-security.json'
PROM = ROOT / 'config/grafana/dashboards/beautyglow-overview.json'
PROM_HASH = '041391062f76e52d656b6dde5a9d1d9bf9451e01a725f2c6d7838b15b6a65ee6'
G = 'http://127.0.0.1:13000'
P = 'http://127.0.0.1:19090'
START = 1792404814000000000
END = 1792433800000000000
RANGE = str((END-START)//10**9)+'s'


def get(url, headers=None, data=None):
    req = urllib.request.Request(url, headers=headers or {}, data=data)
    with urllib.request.urlopen(req, timeout=30) as response:
        assert response.status == 200
        return json.load(response)


def resolve(expr, service='.+', stream='.+', interval='1m'):
    return (expr.replace('${service:regex}', service)
            .replace('${stream:regex}', stream)
            .replace('$__range', RANGE).replace('$__interval', interval))


def main():
    d = json.loads(DASH.read_bytes())
    assert d['uid'] == 'beautyglow-logs-security'
    assert d['title'] == 'BeautyGlow - Logs & Security'
    assert len(d['panels']) == 14
    assert len({p['id'] for p in d['panels']}) == 14
    assert hashlib.sha256(PROM.read_bytes()).hexdigest() == PROM_HASH
    assert [v['name'] for v in d['templating']['list']] == ['service', 'stream']
    for panel in d['panels']:
        pos = panel['gridPos']
        assert 0 <= pos['x'] and pos['x']+pos['w'] <= 24
        for t in panel.get('targets', []):
            assert t['datasource']['uid'] == 'beautyglow-loki'
            assert t['maxLines'] == 500
    print('PASS dashboard JSON, layout bounds, datasource, variables and Prometheus file hash', flush=True)
    password = Path('/var/lib/beautyglow-secrets/grafana-admin-password').read_text().strip()
    auth = {'Authorization': 'Basic '+base64.b64encode(
        ('beautyglow_grafana_admin:'+password).encode()).decode()}
    ds = get(G+'/api/datasources/uid/beautyglow-loki', auth)
    assert ds['type'] == 'loki' and ds['url'] == 'http://loki:3100'
    assert get(G+'/api/datasources/uid/beautyglow-loki/health', auth)['status'] == 'OK'
    proxy = G+'/api/datasources/proxy/uid/beautyglow-loki/loki/api/v1/'
    expected = {}
    for p in Path('/var/lib/beautyglow-logs').glob('*/*.jsonl'):
        for line in p.read_bytes().splitlines():
            item = json.loads(line)
            key = (item['service'], item['stream'])
            expected[key] = expected.get(key, 0)+1
    checked = 0
    for panel in d['panels']:
        for t in panel.get('targets', []):
            q = resolve(t['expr'])
            args = {'query': q}
            if t['queryType'] == 'instant':
                endpoint = 'query'
                args['time'] = str(END)
            else:
                endpoint = 'query_range'
                args.update(start=str(START), end=str(END), step='60', limit='500', direction='backward')
            a = get(proxy+endpoint+'?'+urllib.parse.urlencode(args), auth)
            assert a['status'] == 'success'
            result = a['data']['result']
            if a['data']['resultType'] != 'streams':
                assert all('__error__' not in x['metric'] for x in result)
            output = {'panel': panel['id'], 'ref': t['refId'], 'type': a['data']['resultType'], 'series': len(result)}
            if t['queryType'] == 'instant':
                output['total'] = sum(float(x['value'][1]) for x in result)
                if panel['id'] == 2:
                    assert output['total'] == sum(expected.values()) == 3977
                if panel['id'] == 8:
                    actual = {(x['metric']['service'], x['metric']['stream']): int(float(x['value'][1])) for x in result}
                    assert actual == expected
                if panel['id'] == 10:
                    statuses = {x['metric']['http_status']: int(float(x['value'][1])) for x in result}
                    assert statuses == {'200': 1070, '302': 1, '500': 1}
            if a['data']['resultType'] == 'streams':
                output['returned_lines'] = sum(len(x['values']) for x in result)
            print('PASS LogQL '+json.dumps(output), flush=True)
            checked += 1
    # Verify the same Grafana datasource frames consumed by affected renderers,
    # not just Loki HTTP success or a nonempty matrix.
    for panel in d['panels']:
        if panel['type'] not in ('timeseries', 'piechart'):
            continue
        queries = [{**t, 'expr':resolve(t['expr']), 'intervalMs':60000,
                    'maxDataPoints':600} for t in panel['targets']]
        payload = json.dumps({'from':str(START//10**6), 'to':str(END//10**6),
                              'queries':queries}).encode()
        a = get(G+'/api/ds/query', {**auth,'Content-Type':'application/json'}, payload)
        seen = {}
        for ref, result in a['results'].items():
            assert not result.get('error')
            series = []
            for frame in result.get('frames', []):
                fields = frame['schema']['fields']
                columns = frame['data']['values']
                times = [columns[i] for i,f in enumerate(fields) if f['type']=='time']
                assert len(times) == 1 and times[0]
                assert times[0] == sorted(times[0])
                assert max(times[0]) >= START//10**6 and min(times[0]) <= END//10**6
                for i, field in enumerate(fields):
                    if field['type'] != 'number':
                        continue
                    values = [v for v in columns[i] if v is not None]
                    assert values and len(columns[i]) == len(times[0])
                    assert all(isinstance(v,(int,float)) and v >= 0 for v in values)
                    series.append({'labels':field.get('labels',{}), 'max':max(values),
                                   'last':values[-1], 'points':len(values)})
            assert series
            seen[ref] = series
        if panel['type']=='timeseries':
            custom = panel['fieldConfig']['defaults']['custom']
            assert custom['drawStyle']=='line' and custom['showPoints']=='always'
            assert not custom['hideFrom']['viz'] and panel.get('transformations')==[]
            if panel['id'] == 5:
                assert seen['A'][0]['max'] > 0 and seen['A'][0]['points'] > 100
            if panel['id'] == 6:
                assert {x['labels']['service'] for x in seen['A']} == {'nginx','wordpress','mysql'}
            if panel['id'] == 9:
                assert seen['A'][0]['max']==0 and seen['B'][0]['max']==1
            if panel['id'] in (11,12):
                assert any(x['max'] > 0 for x in seen['A'])
        else:
            assert set(seen)=={'A','B'}
            assert len(seen['A'])==len(seen['B'])==1
            assert seen['A'][0]['last']==3726 and seen['B'][0]['last']==251
            assert panel['options']['reduceOptions']=={'calcs':['lastNotNull'],'fields':'','values':False}
            assert panel['options']['legend']['showLegend']
            overrides=panel['fieldConfig']['overrides']
            assert [x['matcher'] for x in overrides]==[{'id':'byFrameRefID','options':r} for r in ('A','B')]
            assert [x['properties'][0]['value'] for x in overrides]==['stdout','stderr']
            assert overrides[0]['properties'][1]['value'] != overrides[1]['properties'][1]['value']
        print('PASS Grafana renderer input '+json.dumps({'panel':panel['id'],'series':seen}), flush=True)
    # Check actual label-value endpoints used by chained query variables.
    for label, matcher, expected_values in [
            ('service', '{project="beautyglow-website"}', {'nginx','wordpress','mysql'}),
            ('stream', '{project="beautyglow-website",service=~"nginx|wordpress|mysql"}', {'stdout','stderr'})]:
        args = urllib.parse.urlencode({'query': matcher, 'start': str(START), 'end': str(END)})
        a = get(proxy+'label/'+label+'/values?'+args, auth)
        assert set(a['data']) == expected_values
    # Exercise 3 services x 2 streams against source counts.
    for (svc, stream), count in expected.items():
        q = resolve(d['panels'][1]['targets'][0]['expr'], svc, stream)
        a = get(proxy+'query?'+urllib.parse.urlencode({'query':q,'time':str(END)}), auth)
        assert sum(float(x['value'][1]) for x in a['data']['result']) == count
    print('PASS service/stream selectors and label variables', flush=True)
    # Grafana data frames: table and logs must receive actual Time/Line fields.
    for panelid in (13, 14):
        t = next(p for p in d['panels'] if p['id'] == panelid)['targets'][0]
        query = {**t, 'expr':resolve(t['expr']), 'maxLines':20, 'intervalMs':60000, 'maxDataPoints':500}
        payload = json.dumps({'from':str(START//10**6),'to':str(END//10**6),'queries':[query]}).encode()
        a = get(G+'/api/ds/query', {**auth,'Content-Type':'application/json'}, payload)
        entry = a['results']['A']
        assert not entry.get('error')
        fields = {f['name'] for frame in entry.get('frames',[]) for f in frame['schema']['fields']}
        assert {'Time','Line'} <= fields
        print('PASS Grafana detail data frames '+json.dumps({'panel':panelid,'fields':sorted(fields)}), flush=True)
    live = get(G+'/api/dashboards/uid/'+d['uid'], auth)
    assert live['meta'].get('provisioned') is True
    assert live['dashboard']['panels'] == d['panels']
    assert live['dashboard']['templating'] == d['templating']
    print('PASS dashboard file provisioning loaded, 14 panels match', flush=True)
    prom = get(G+'/api/dashboards/uid/beautyglow-overview', auth)['dashboard']
    local = json.loads(PROM.read_bytes())
    def exprs(dashboard):
        return [t['expr'] for p in dashboard['panels'] for t in p.get('targets',[]) if 'expr' in t]
    assert exprs(prom) == exprs(local)
    for expr in exprs(prom):
        q = expr.replace('$__rate_interval', '2m' if 'rate(container_cpu_usage_seconds_total{' in expr else '1m')
        a = get(P+'/api/v1/query?'+urllib.parse.urlencode({'query':q}))
        assert a['status'] == 'success' and a['data']['result']
    targets = get(P+'/api/v1/targets')['data']['activeTargets']
    assert len(targets) == 6 and all(t['health']=='up' for t in targets)
    assert hashlib.sha256(PROM.read_bytes()).hexdigest() == PROM_HASH
    print('PASS existing Prometheus dashboard unchanged, '+str(len(exprs(prom)))+' queries, 6/6 targets up', flush=True)
    print('PASS '+str(checked)+' dashboard LogQL queries; no raw logs emitted', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Never print response bodies/headers or credential-bearing exceptions.
        print('FAIL verification: '+type(error).__name__, flush=True)
        raise SystemExit(1)
