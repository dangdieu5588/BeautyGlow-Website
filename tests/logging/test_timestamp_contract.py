"""Offline wire contract and safe diagnostics; no daemon or real spool access."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import re
import tempfile
import traceback
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

import test_docker_adapter as fixture
import test_live_bridge as live

b = fixture.b
ID = fixture.ID
SECRET = fixture.SECRET
BASE = fixture.BASE


class WireEngine(fixture.FakeTransport):
    def request(self, method, path, *, timeout):
        query = parse_qs(urlsplit(path).query)
        if urlsplit(path).path.endswith('/logs'):
            for name in ('since', 'until'):
                if name in query and not re.fullmatch(r'-?[0-9]+\.[0-9]{9}', query[name][0]):
                    return fixture.FakeResponse(status=400)
        return super().request(method, path, timeout=timeout)


class TimestampContractTests(unittest.TestCase):
    def setUp(self):
        for target in ('socket.socket', 'socket.create_connection', 'socket.getaddrinfo',
                       'subprocess.Popen', 'urllib.request.urlopen'):
            guard = patch(target, side_effect=AssertionError('REAL_IO_FORBIDDEN'))
            guard.start()
            self.addCleanup(guard.stop)
        family = patch.object(b.socket, 'AF_UNIX', 1, create=True)
        family.start()
        self.addCleanup(family.stop)
        tmp = tempfile.TemporaryDirectory(prefix='_timestamp-contract-', dir=Path(__file__).parent)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'spool'

    def test_one_nanosecond(self):
        self.assertEqual(b.unix_timestamp('1970-01-01T00:00:00.000000001Z'), '0.000000001')

    def test_second_boundary(self):
        self.assertEqual(b.unix_timestamp('1970-01-01T00:00:00.999999999Z'), '0.999999999')
        self.assertEqual(b.unix_timestamp('1970-01-01T00:00:01Z'), '1.000000000')

    def test_positive_timezone(self):
        self.assertEqual(b.unix_timestamp('2026-10-19T17:13:25.123456789+07:00'), '1792404805.123456789')

    def test_negative_timezone(self):
        self.assertEqual(b.unix_timestamp('2026-10-19T05:13:25.000000001-05:00'), '1792404805.000000001')

    def test_public_bootstrap_conversion_only(self):
        value = '2026-10-19T10:13:25.000000000Z'
        self.assertEqual(b.unix_timestamp(value), '1792404805.000000000')
        self.assertEqual(b.timestamp(value)[0], value)

    def test_before_epoch_uses_integer_divmod(self):
        self.assertEqual(b.unix_timestamp('1969-12-31T23:59:59.999999999Z'), '-1.999999999')

    def test_large_date_keeps_nanoseconds(self):
        self.assertEqual(b.unix_timestamp('2100-01-01T00:00:00.123456789Z'), '4102444800.123456789')

    def test_wire_validator_rejects_noncanonical_values(self):
        for value in [BASE, '1', '1.1', '01.000000000', '-0.000000001',
                      '1.0000000000', '1e9', 'nan', 'inf', True, None,
                      '9223372036854775808.000000000', '1.000000000\r\n' + SECRET]:
            with self.subTest(kind=type(value).__name__):
                with self.assertRaisesRegex(b.BridgeError, '^TIMESTAMP_INVALID$'):
                    b.validate_unix_timestamp(value)

    def test_wire_validator_accepts_integer_limits(self):
        for value in ['0.000000000', '-1.000000001',
                      '9223372036854775807.999999999', '-9223372036854775808.000000000']:
            self.assertEqual(b.validate_unix_timestamp(value), value)

    def test_fake_engine_refuses_rfc3339_and_accepts_unix(self):
        engine = WireEngine()
        path = '/v1.51/containers/' + ID + '/logs?'
        response = engine.request('GET', path + urlencode({'since': BASE}), timeout=5)
        self.assertEqual(response.status, 400)
        response.close()
        self.assertEqual(engine.request('GET', path + urlencode({'since': '1792396800.123456789'}), timeout=5).status, 200)

    def test_adapter_sends_precise_since_and_until(self):
        engine = WireEngine()
        adapter = b.DockerAPIAdapter(engine, b.Policy())
        adapter.discover(b.PROJECT, b.Policy().services)
        list(adapter.read_window(ID, BASE, '2026-10-19T08:00:15.123456789Z'))
        path = next(p for _, p, _ in engine.calls if '/logs?' in p)
        query = parse_qs(urlsplit(path).query)
        self.assertEqual(query['since'], ['1792396800.123456789'])
        self.assertEqual(query['until'], ['1792396815.123456789'])
        self.assertEqual(query['follow'], ['false'])

    def test_fake_unix_transport_obeys_wire_contract(self):
        engine = WireEngine()
        sockets = []
        def reply(request):
            method, path, _ = request.split(b'\r\n', 1)[0].decode().split(' ')
            response = engine.request(method, path, timeout=5)
            return live.http(response.body, {'Content-Type': response.headers['Content-Type'],
                                            'Content-Length': str(len(response.body))}, response.status)
        def factory(*_):
            raw = live.RawSocket(reply, max_read=3)
            sockets.append(raw)
            return raw
        adapter = b.DockerAPIAdapter(b.UnixHTTPTransport('/synthetic.sock', socket_factory=factory), b.Policy())
        adapter.discover(b.PROJECT, b.Policy().services)
        self.assertEqual(list(adapter.read_window(ID, BASE, '2026-10-19T08:00:15.123456789Z')), [])
        self.assertTrue(all(s.closed for s in sockets))

    def test_transport_refuses_rfc3339_before_opening_socket(self):
        called = []
        transport = b.UnixHTTPTransport('/synthetic.sock', socket_factory=lambda *_: called.append(True))
        query = dict(stdout='true', stderr='true', timestamps='true', follow='false', tail='all', since=BASE)
        with self.assertRaisesRegex(b.BridgeError, '^TIMESTAMP_INVALID$'):
            transport.request('GET', '/v1.51/containers/' + ID + '/logs?' + urlencode(query), timeout=5)
        self.assertFalse(called)

    def test_diagnostics_for_all_three_routes_without_query_or_id(self):
        routes = [('/containers/json', '/v1.51/containers/json'),
                  ('/containers/' + ID + '/json', '/v1.51/containers/{id}/json'),
                  ('/containers/' + ID + '/logs', '/v1.51/containers/{id}/logs')]
        for path, expected in routes:
            engine = fixture.FakeTransport()
            response = fixture.FakeResponse(SECRET.encode(), status=403)
            engine.request = lambda *a, **kw: response
            adapter = b.DockerAPIAdapter(engine, b.Policy())
            with self.assertRaises(b.BridgeError) as caught:
                adapter._open(path + '?token=' + SECRET, {'application/json'})
            diagnostic = dict(caught.exception.http_diagnostic)
            self.assertEqual(diagnostic, dict(method='GET', endpoint=expected, status=403, code='ADAPTER_HTTP_ERROR'))
            self.assertEqual(response.read_calls, 0)
            self.assertTrue(response.closed)
            self.assertNotIn(SECRET, repr(diagnostic))
            self.assertNotIn(ID, repr(diagnostic))

    def test_diagnostic_normalization_handles_host_crlf_and_unusual_paths(self):
        for path in ['http://user:' + SECRET + '@/v1.51/containers/json',
                     '/v1.51/containers/json\r\nAuthorization: ' + SECRET,
                     '/v1.51/containers/../../' + SECRET,
                     '/v1.51/containers/%2e%2e/' + SECRET, '//host/' + SECRET]:
            diagnostic = b.http_error_diagnostic(path, 400)
            self.assertEqual(diagnostic['endpoint'], '/invalid')
            self.assertNotIn(SECRET, repr(diagnostic))

    def test_diagnostic_rejects_untrusted_context(self):
        base = b.http_error_diagnostic('/v1.51/containers/json', 400)
        for patch_data in [dict(endpoint=SECRET), dict(method='POST'), dict(status=True),
                           dict(status=SECRET), dict(code=SECRET), dict(extra=SECRET)]:
            exc = b.BridgeError('ADAPTER_HTTP_ERROR', http_diagnostic={**base, **patch_data})
            self.assertIsNone(exc.http_diagnostic)
            self.assertNotIn(SECRET, repr(exc))

    def test_cli_prints_exact_four_safe_fields(self):
        error = b.BridgeError('ADAPTER_HTTP_ERROR', http_diagnostic=b.http_error_diagnostic(
            '/v1.51/containers/' + ID + '/logs?token=' + SECRET, 400))
        out, err = io.StringIO(), io.StringIO()
        with patch.object(b, '_run_live', side_effect=error), redirect_stdout(out), redirect_stderr(err):
            result = b.main(['--live', '--spool', str(self.root.resolve()), '--read-gid', '475', '--bootstrap-since', BASE])
        self.assertEqual(result, 2)
        self.assertEqual(json.loads(out.getvalue()), dict(method='GET', endpoint='/v1.51/containers/{id}/logs', status=400, code='ADAPTER_HTTP_ERROR'))
        self.assertEqual(err.getvalue(), '')
        self.assertNotIn(SECRET, out.getvalue())
        self.assertNotIn(ID, out.getvalue())

    def test_retry_status_classification_is_unchanged(self):
        for status in [404, 408, 429, 500, 502, 503, 504]:
            response = fixture.FakeResponse(SECRET.encode(), status=status)
            engine = fixture.FakeTransport()
            engine.request = lambda *a, **kw: response
            with self.assertRaises(b.RetryableAdapterError):
                b.DockerAPIAdapter(engine, b.Policy())._open('/containers/json', {'application/json'})
            self.assertTrue(response.closed)
            self.assertEqual(response.read_calls, 0)

    def test_permanent_http_statuses_keep_code_and_do_not_read_body(self):
        for status in [204, 301, 302, 400, 401, 403, 409, 501]:
            response = fixture.FakeResponse(SECRET.encode(), status=status)
            engine = fixture.FakeTransport()
            engine.request = lambda *a, **kw: response
            with self.assertRaises(b.BridgeError) as caught:
                b.DockerAPIAdapter(engine, b.Policy())._open('/containers/json', {'application/json'})
            self.assertEqual(str(caught.exception), 'ADAPTER_HTTP_ERROR')
            self.assertEqual(caught.exception.http_diagnostic['status'], status)
            self.assertTrue(response.closed)
            self.assertEqual(response.read_calls, 0)

    def test_encoded_query_and_crlf_never_enter_diagnostic(self):
        for query in ['?token=' + SECRET, '?token=%0d%0a' + SECRET,
                      '?Authorization=Bearer%20' + SECRET, '?cookie=' + SECRET]:
            diagnostic = b.http_error_diagnostic('/v1.51/containers/' + ID + '/logs' + query, 400)
            self.assertEqual(diagnostic['endpoint'], '/v1.51/containers/{id}/logs')
            self.assertNotIn(SECRET, json.dumps(diagnostic))
            self.assertNotIn('?', json.dumps(diagnostic))

    def test_fake_http_error_reason_headers_and_body_are_not_diagnosed(self):
        payload = live.http(SECRET.encode(), {'Content-Length': str(len(SECRET)),
                           'Authorization': SECRET, 'Set-Cookie': SECRET}, 403)
        payload = payload.replace(b'403 Result', ('403 ' + SECRET).encode())
        raw = live.RawSocket(payload)
        transport = b.UnixHTTPTransport('/synthetic.sock', socket_factory=lambda *_: raw)
        with self.assertRaises(b.BridgeError) as caught:
            b.DockerAPIAdapter(transport, b.Policy())._open('/containers/json?' + urlencode({
                'all': 'false', 'filters': json.dumps({'label': ['com.docker.compose.project=beautyglow-website'],
                                                     'status': ['running']})}), {'application/json'})
        self.assertEqual(caught.exception.http_diagnostic['status'], 403)
        self.assertNotIn(SECRET, json.dumps(dict(caught.exception.http_diagnostic)))
        self.assertNotIn(SECRET, ''.join(traceback.format_exception(caught.exception)))
        self.assertTrue(raw.closed)

    def test_converter_rejects_sensitive_invalid_input_without_echo(self):
        for value in ['invalid?token=' + SECRET, '2026-10-19T10:13:25+99:00',
                      '2026-10-19T10:13:25Z\r\n' + SECRET]:
            with self.assertRaises(b.BridgeError) as caught:
                b.unix_timestamp(value)
            self.assertEqual(str(caught.exception), 'TIMESTAMP_INVALID')
            self.assertNotIn(SECRET, ''.join(traceback.format_exception(caught.exception)))

    def test_bridge_preserves_safe_diagnostic_and_saves_no_error_body(self):
        engine = fixture.FakeTransport()
        response = fixture.FakeResponse(SECRET.encode(), status=400)
        engine.list_replies = [response]
        with b.Spool(self.root, b.Policy()) as spool:
            bridge = b.Bridge(b.DockerAPIAdapter(engine, b.Policy()), spool, b.Policy(),
                              bootstrap_since=BASE, batch_seconds=15, now=lambda: fixture.NOW)
            with self.assertRaises(b.BridgeError) as caught:
                bridge.run_once()
            self.assertEqual(caught.exception.http_diagnostic['status'], 400)
            self.assertNotIn(SECRET, ''.join(traceback.format_exception(caught.exception)))
        for p in self.root.rglob('*'):
            if p.is_file():
                self.assertNotIn(SECRET.encode(), p.read_bytes())

    def test_resume_keeps_existing_bootstrap_and_deduplicates(self):
        cs = [fixture.summary(s, chr(97+i)*64) for i, s in enumerate(('nginx', 'wordpress', 'mysql'))]
        def adapter():
            engine = WireEngine(cs)
            for item in cs:
                engine.log_replies[item['Id']] = [fixture.log_response(fixture.frame(fixture.line('synthetic record')))]
            return b.DockerAPIAdapter(engine, b.Policy())
        for attempt in range(2):
            with b.Spool(self.root, b.Policy()) as spool:
                bridge = b.Bridge(adapter(), spool, b.Policy(), bootstrap_since=BASE,
                                  batch_seconds=15, now=lambda: fixture.NOW)
                result = bridge.run_once()
                self.assertEqual(result.accepted, 3 if attempt == 0 else 0)
                self.assertEqual(spool.state['bootstrap_ns'], b.timestamp(BASE)[1])
                self.assertEqual(len(spool.state['containers']), 3)
                self.assertEqual(len(spool.state['scans']), 3)
        rows = [json.loads(line) for p in self.root.glob('*/*.jsonl') for line in p.read_text().splitlines()]
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row['timestamp'] == BASE for row in rows))

    def test_resume_from_failed_pilot_bootstrap_only_checkpoint(self):
        bootstrap = '2026-10-19T10:13:25.000000000Z'
        start = b.timestamp(bootstrap)[1]
        with b.Spool(self.root, b.Policy()) as spool:
            engine = fixture.FakeTransport()
            engine.list_replies = [fixture.FakeResponse(status=400)]
            bridge = b.Bridge(b.DockerAPIAdapter(engine, b.Policy()), spool, b.Policy(),
                              bootstrap_since=bootstrap, batch_seconds=15, now=lambda: start+60*10**9)
            with self.assertRaisesRegex(b.BridgeError, 'ADAPTER_HTTP_ERROR'):
                bridge.run_once()
            self.assertEqual(spool.state['containers'], {})
            self.assertEqual(spool.state['scans'], {})
        cs = [fixture.summary(s, chr(97+i)*64) for i, s in enumerate(('nginx', 'wordpress', 'mysql'))]
        for attempt in range(2):
            engine = WireEngine(cs)
            for item in cs:
                engine.log_replies[item['Id']] = [fixture.log_response(fixture.frame(
                    fixture.line('synthetic resume', stamp=bootstrap)))]
            with b.Spool(self.root, b.Policy()) as spool:
                bridge = b.Bridge(b.DockerAPIAdapter(engine, b.Policy()), spool, b.Policy(),
                                  bootstrap_since=bootstrap, batch_seconds=15, now=lambda: start+60*10**9)
                result = bridge.run_once()
                self.assertEqual(spool.state['bootstrap_ns'], start)
                self.assertEqual(result.accepted, 3 if attempt == 0 else 0)
                self.assertEqual(result.duplicates, 0 if attempt == 0 else 3)


if __name__ == '__main__':
    unittest.main()
