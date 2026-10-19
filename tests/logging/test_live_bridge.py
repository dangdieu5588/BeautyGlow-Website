"""Live-path code with fake sockets only; no Docker/HTTP/Unix socket is opened."""
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
import io
import json
from pathlib import Path
import stat
import tempfile
import traceback
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from test_bridge import b, BASE, NOW, container, record, FakeAdapter
from test_docker_adapter import (FakeTransport, SECRET, ID, summary, inspection,
                                 frame, line, json_response, log_response)


INSPECT = '/v1.51/containers/' + ID + '/json'
ROOT = Path(__file__).resolve().parents[2]


def http(body=b'{}', headers=None, status=200):
    fields = {'Content-Type': 'application/json', 'Content-Length': str(len(body)), 'Connection': 'close'}
    if headers is not None:
        fields = headers
    return (f'HTTP/1.1 {status} Result\r\n' + ''.join(f'{k}: {v}\r\n' for k,v in fields.items()) + '\r\n').encode() + body


def chunked(body, *, ending=b'0\r\n\r\n'):
    return http(f'{len(body):x}\r\n'.encode()+body+b'\r\n'+ending,
                {'Content-Type':'application/json','Transfer-Encoding':'chunked','Connection':'close'})


class RawSocket:
    def __init__(self, body, *, max_read=8192, fail_at=None, on_read=lambda:None,
                 connect_error=None, read_error=None, close_error=False):
        self.body, self.pos, self.sent = body, 0, bytearray()
        self.max_read, self.fail_at, self.on_read = max_read, fail_at, on_read
        self.connect_error, self.read_error, self.close_error = connect_error, read_error, close_error
        self.closed, self.connected, self.timeouts = False, None, []

    def settimeout(self, value): self.timeouts.append(value)
    def connect(self, path):
        if self.connect_error: raise self.connect_error
        self.connected=path
    def sendall(self, data): self.sent.extend(data)
    def recv_into(self, buffer):
        self.on_read()
        if callable(self.body): self.body=self.body(bytes(self.sent))
        if self.fail_at is not None and self.pos>=self.fail_at:
            raise self.read_error or ConnectionResetError(SECRET)
        end=min(len(self.body),self.pos+len(buffer),self.pos+self.max_read)
        if self.fail_at is not None:end=min(end,self.fail_at)
        data=self.body[self.pos:end];buffer[:len(data)]=data;self.pos=end
        return len(data)
    def close(self):
        self.closed=True
        if self.close_error:raise RuntimeError(SECRET)


class Event:
    def __init__(self, stop_after=None):self.flag=False;self.delays=[];self.stop_after=stop_after
    def is_set(self):return self.flag
    def set(self):self.flag=True
    def wait(self, delay):
        self.delays.append(delay)
        if self.stop_after is not None and len(self.delays)>=self.stop_after:self.flag=True
        return self.flag


class WindowAdapter(FakeAdapter):
    def __init__(self, records=None):
        cs=[container(s,chr(97+i)) for i,s in enumerate(('nginx','wordpress','mysql'))]
        super().__init__(cs,records)
        self.windows=[]
    def read_window(self, ident, since, until):
        self.windows.append((ident,since,until))
        low,high=b.timestamp(since)[1],b.timestamp(until)[1]
        for item in self.read_logs(ident,since):
            if low<=b.timestamp(item.timestamp)[1]<=high:yield item


class LiveTests(unittest.TestCase):
    def setUp(self):
        # This Windows Python has no AF_UNIX constant. Model Linux's constant
        # for injected fake sockets only; the default socket factory stays denied.
        family=patch.object(b.socket,'AF_UNIX',1,create=True)
        family.start();self.addCleanup(family.stop)
        for name in ('socket.socket','socket.create_connection','socket.getaddrinfo',
                     'subprocess.Popen','urllib.request.urlopen'):
            item=patch(name,side_effect=AssertionError('REAL_IO_FORBIDDEN'))
            item.start();self.addCleanup(item.stop)
        self.temp=tempfile.TemporaryDirectory(prefix='_live-runtime-',dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'spool'
        self.policy=b.Policy()

    def transport(self, raw, **kwargs):
        calls=[]
        def factory(family,kind):calls.append((family,kind));return raw
        return b.UnixHTTPTransport('/run/beautyglow-test.sock',socket_factory=factory,**kwargs),calls

    def body(self, raw, **kwargs):
        transport,calls=self.transport(raw,**kwargs)
        response=transport.request('GET',INSPECT,timeout=5)
        try:
            chunks=[]
            while True:
                part=response.read(16)
                if not part:break
                chunks.append(part)
            return b''.join(chunks)
        finally:transport.close()

    def bridge(self, adapter, spool, **kwargs):
        return b.Bridge(adapter,spool,self.policy,now=lambda:NOW,sleep=lambda _:None,
                        bootstrap_since=BASE,batch_seconds=30,**kwargs)

    def rows(self):
        return [json.loads(l) for p in self.root.glob('*/*.jsonl') for l in p.read_text(encoding='utf8').splitlines()]

    def test_transport_constructor_performs_no_io(self):
        b.UnixHTTPTransport('/run/beautyglow-test.sock')
        self.assertFalse(self.root.exists())

    def test_unix_http_content_length_one_byte_reads(self):
        raw=RawSocket(http(b'{"ok":true}'),max_read=1)
        self.assertEqual(self.body(raw),b'{"ok":true}')
        self.assertEqual(raw.connected,'/run/beautyglow-test.sock')
        self.assertTrue(raw.closed)
        self.assertTrue(bytes(raw.sent).startswith(('GET '+INSPECT+' HTTP/1.1').encode()))
        self.assertNotIn(b'Authorization:',raw.sent)

    def test_http_chunked_one_byte_reads(self):
        raw=RawSocket(chunked(b'hello'),max_read=1)
        self.assertEqual(self.body(raw),b'hello')
        self.assertTrue(raw.closed)

    def test_http_chunked_multiple_chunks_and_trailer(self):
        body=b'2\r\nhe\r\n3\r\nllo\r\n0\r\nX-Test: ignored\r\n\r\n'
        raw=RawSocket(http(body,{'Content-Type':'application/json','Transfer-Encoding':'chunked'}))
        self.assertEqual(self.body(raw),b'hello')

    def test_content_length_premature_eof_is_not_success(self):
        raw=RawSocket(http(b'short',{'Content-Type':'application/json','Content-Length':'50'}))
        with self.assertRaises(ConnectionError):self.body(raw)
        self.assertTrue(raw.closed)

    def test_chunked_premature_eof_and_bad_chunk_are_sanitized(self):
        for payload in (b'5\r\nabc',b'bad-length\r\n'+SECRET.encode()):
            raw=RawSocket(http(payload,{'Content-Type':'application/json','Transfer-Encoding':'chunked'}))
            with self.assertRaises((ConnectionError,b.BridgeError)) as caught:self.body(raw)
            self.assertNotIn(SECRET,''.join(traceback.format_exception(caught.exception)))
            self.assertTrue(raw.closed)

    def test_missing_or_conflicting_body_framing_rejected(self):
        cases=[{'Content-Type':'application/json'},
               {'Content-Type':'application/json','Content-Length':'-1'},
               {'Content-Type':'application/json','Content-Length':'2','Transfer-Encoding':'chunked'},
               {'Content-Type':'application/json','Transfer-Encoding':'gzip'}]
        for headers in cases:
            raw=RawSocket(http(b'{}',headers))
            with self.assertRaisesRegex(b.BridgeError,'TRANSPORT_PROTOCOL_INVALID'):self.body(raw)
            self.assertTrue(raw.closed)

    def test_duplicate_content_length_rejected(self):
        raw=RawSocket(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{}')
        with self.assertRaises(b.BridgeError):self.body(raw)
        self.assertTrue(raw.closed)

    def test_oversized_or_interim_headers_rejected(self):
        for data in [b'HTTP/1.1 200 OK\r\nX: '+b'x'*17000+b'\r\n\r\n',b'HTTP/1.1 100 Continue\r\n\r\n']:
            raw=RawSocket(data)
            with self.assertRaises(b.BridgeError):self.body(raw)
            self.assertTrue(raw.closed)

    def test_connect_and_read_timeouts_close_socket(self):
        raws=[RawSocket(http(),connect_error=TimeoutError(SECRET)),
              RawSocket(http(),fail_at=0,read_error=TimeoutError(SECRET))]
        for raw in raws:
            with self.assertRaises(ConnectionError) as caught:self.body(raw)
            self.assertTrue(raw.closed)
            self.assertNotIn(SECRET,''.join(traceback.format_exception(caught.exception)))

    def test_read_reset_after_headers_is_sanitized(self):
        data=http(b'x'*100)
        header_end=data.index(b'\r\n\r\n')+4
        raw=RawSocket(data,fail_at=header_end+2,max_read=1)
        with self.assertRaises(ConnectionError) as caught:self.body(raw)
        self.assertNotIn(SECRET,''.join(traceback.format_exception(caught.exception)))
        self.assertTrue(raw.closed)

    def test_total_deadline_stops_slow_trickle(self):
        ticks=[0.0]
        def tick():ticks[0]+=0.1
        raw=RawSocket(http(b'x'*50),max_read=1,on_read=tick)
        with self.assertRaises(ConnectionError):
            self.body(raw,clock=lambda:ticks[0],max_response_seconds=0.3)
        self.assertLessEqual(raw.pos,4)
        self.assertTrue(raw.closed)
        self.assertTrue(all(0<v<=0.3 for v in raw.timeouts))

    def test_shutdown_before_socket_creation(self):
        transport,calls=self.transport(RawSocket(http()),stop=lambda:True)
        with self.assertRaises(b.ShutdownRequested):transport.request('GET',INSPECT,timeout=5)
        self.assertEqual(calls,[])

    def test_shutdown_during_read_closes_socket(self):
        event=Event()
        raw=RawSocket(http(),on_read=event.set)
        with self.assertRaises(b.ShutdownRequested):self.body(raw,stop=event.is_set)
        self.assertTrue(raw.closed)

    def test_close_is_idempotent_and_close_errors_hidden(self):
        raw=RawSocket(http(),close_error=True)
        transport,_=self.transport(raw)
        output=io.StringIO()
        with redirect_stdout(output),redirect_stderr(output):
            response=transport.request('GET',INSPECT,timeout=5)
            response.close();response.close();transport.close()
        self.assertTrue(raw.closed)
        self.assertEqual(output.getvalue(),'')

    def test_parallel_request_rejected_without_new_socket(self):
        transport,calls=self.transport(RawSocket(http()))
        transport.request('GET',INSPECT,timeout=5)
        with self.assertRaisesRegex(b.BridgeError,'TRANSPORT_BUSY'):
            transport.request('GET',INSPECT,timeout=5)
        self.assertEqual(len(calls),1)
        transport.close()

    def test_transport_denies_write_tcp_and_path_injection(self):
        transport,calls=self.transport(RawSocket(http()))
        for method,path in [('POST',INSPECT),('GET','http://example.invalid'+INSPECT),
                            ('GET','/v1.51/containers/'+ID+'/stop'),('GET',INSPECT+'\r\nAuthorization: '+SECRET),
                            ('GET',INSPECT+'?unknown=true'),('GET',INSPECT+'?badquery')]:
            with self.assertRaises(b.BridgeError):transport.request(method,path,timeout=5)
        self.assertFalse(calls)

    def test_transport_requires_exact_project_discovery_query(self):
        transport,calls=self.transport(RawSocket(http()))
        for path in ['/v1.51/containers/json','/v1.51/containers/json?all=true',
                     '/v1.51/containers/json?all=false&filters={}']:
            with self.assertRaises(b.BridgeError):transport.request('GET',path,timeout=5)
        self.assertFalse(calls)

    def test_body_quota_is_bounded(self):
        raw=RawSocket(http(b'x'*65))
        with self.assertRaises(b.BridgeError):self.body(raw,max_body_bytes=64)
        self.assertTrue(raw.closed)

    def test_http_error_body_not_exposed_or_returned_as_headers(self):
        raw=RawSocket(http(SECRET.encode(),{'Authorization':SECRET,'Set-Cookie':SECRET},status=403))
        transport,_=self.transport(raw)
        response=transport.request('GET',INSPECT,timeout=5)
        self.assertEqual(response.status,403)
        self.assertNotIn(SECRET,repr(response.headers));response.close()

    def test_complete_fake_unix_transport_adapter_bridge_pipeline(self):
        cs=[summary(s,chr(97+i)*64) for i,s in enumerate(('nginx','wordpress','mysql'))]
        engine=FakeTransport(cs)
        for item in cs:engine.log_replies[item['Id']]=[log_response(frame(line('token='+SECRET)))]
        sockets=[]
        def reply(request):
            method,path,_=request.split(b'\r\n',1)[0].decode().split(' ')
            response=engine.request(method,path,timeout=5)
            return http(response.body,{'Content-Type':response.headers['Content-Type'],
                                        'Content-Length':str(len(response.body))})
        def factory(*_):
            raw=RawSocket(reply,max_read=7);sockets.append(raw);return raw
        transport=b.UnixHTTPTransport('/run/beautyglow-test.sock',socket_factory=factory)
        with b.Spool(self.root,self.policy) as spool:
            result=self.bridge(b.DockerAPIAdapter(transport,self.policy),spool).run_once()
        self.assertEqual(result.accepted,3)
        self.assertEqual({r['service'] for r in self.rows()},b.SERVICES)
        self.assertTrue(all(s.closed for s in sockets))
        for path in self.root.rglob('*'):
            if path.is_file():self.assertNotIn(SECRET.encode(),path.read_bytes())

    def test_window_bootstrap_and_empty_progress_are_persistent(self):
        adapter=WindowAdapter()
        with b.Spool(self.root,self.policy) as spool:self.bridge(adapter,spool).run_once()
        state=json.loads((self.root/'checkpoint.json').read_bytes())
        self.assertEqual(len(state['scans']),3)
        start=b.timestamp(BASE)[1]
        self.assertTrue(all(v['until']==start+30*10**9 for v in state['scans'].values()))
        with b.Spool(self.root,self.policy) as spool:self.bridge(adapter,spool).run_once()
        state=json.loads((self.root/'checkpoint.json').read_bytes())
        self.assertTrue(all(v['until']==start+60*10**9 for v in state['scans'].values()))

    def test_changed_bootstrap_cannot_skip_existing_data(self):
        with b.Spool(self.root,self.policy) as spool:
            self.bridge(WindowAdapter(),spool)
            with self.assertRaisesRegex(b.BridgeError,'BOOTSTRAP_CONFLICT'):
                b.Bridge(WindowAdapter(),spool,self.policy,now=lambda:NOW,
                         bootstrap_since='2026-10-19T08:01:00Z',batch_seconds=30)

    def test_legacy_checkpoint_needs_explicit_migration(self):
        fake=FakeAdapter(records={ID:[record()]})
        with b.Spool(self.root,self.policy) as spool:
            b.Bridge(fake,spool,self.policy,now=lambda:NOW).run_once()
            with self.assertRaisesRegex(b.BridgeError,'BOOTSTRAP_MIGRATION_REQUIRED'):
                self.bridge(WindowAdapter(),spool)

    def test_catchup_windows_preserve_records_and_replays(self):
        adapter=WindowAdapter({ID:[record('one'),record('two','2026-10-19T08:00:40Z')]})
        for _ in range(4):
            with b.Spool(self.root,self.policy) as spool:self.bridge(adapter,spool).run_once()
        self.assertEqual([r['message'] for r in self.rows()],['one','two'])

    def test_pilot_reports_backlog_instead_of_claiming_full_catchup(self):
        with b.Spool(self.root,self.policy) as spool:
            result=self.bridge(WindowAdapter(),spool).run_once()
        self.assertTrue(result.catchup_pending)
        self.assertGreater(result.max_lag_seconds,30)

    def test_failed_window_does_not_advance_scan_cursor(self):
        adapter=WindowAdapter({ID:[record('one'),record('two')]})
        adapter.partial_failures=99
        with b.Spool(self.root,self.policy) as spool:
            with self.assertRaisesRegex(b.BridgeError,'ADAPTER_RETRIES_EXHAUSTED'):
                self.bridge(adapter,spool).run_once()
            self.assertEqual(spool.state['scans'],{})
        adapter.partial_failures=0
        with b.Spool(self.root,self.policy) as spool:self.bridge(adapter,spool).run_once()
        self.assertEqual([r['message'] for r in self.rows()],['one','two'])

    def test_future_and_too_old_initial_bootstrap_rejected(self):
        for stamp in ['2027-01-01T00:00:00Z','2020-01-01T00:00:00Z']:
            with b.Spool(self.root,self.policy) as spool:
                with self.assertRaisesRegex(b.BridgeError,'BOOTSTRAP_INVALID'):
                    b.Bridge(WindowAdapter(),spool,self.policy,now=lambda:NOW,bootstrap_since=stamp,batch_seconds=30)

    def test_window_missing_source_fails_bounded(self):
        adapter=WindowAdapter();adapter.containers=adapter.containers[:1]
        with b.Spool(self.root,self.policy) as spool:
            with self.assertRaisesRegex(b.BridgeError,'ADAPTER_RETRIES_EXHAUSTED'):
                self.bridge(adapter,spool).run_once()
        self.assertFalse(adapter.windows)

    def test_replacement_pauses_without_deleting_old_checkpoint(self):
        adapter=WindowAdapter({ID:[record('old')]})
        with b.Spool(self.root,self.policy) as spool:self.bridge(adapter,spool).run_once()
        before=(self.root/'checkpoint.json').read_bytes()
        adapter.containers[0]=container(ident='d')
        with b.Spool(self.root,self.policy) as spool:
            with self.assertRaisesRegex(b.BridgeError,'SOURCE_REPLACED_REVIEW_REQUIRED'):
                self.bridge(adapter,spool).run_once()
        self.assertEqual((self.root/'checkpoint.json').read_bytes(),before)

    def test_explicit_replacement_keeps_independent_old_and_new_data(self):
        adapter=WindowAdapter({ID:[record('old')]})
        with b.Spool(self.root,self.policy) as spool:self.bridge(adapter,spool).run_once()
        new=container(ident='d');adapter.containers[0]=new;adapter.records[new.id]=[record('new')]
        with b.Spool(self.root,self.policy) as spool:self.bridge(adapter,spool,allow_replacement=True).run_once()
        self.assertEqual([r['message'] for r in self.rows()],['old','new'])
        state=json.loads((self.root/'checkpoint.json').read_bytes())
        self.assertEqual(set(state['containers']),{ID,new.id})

    def test_shutdown_after_append_recovers_journal_and_bootstrap(self):
        adapter=WindowAdapter({ID:[record('one')]})
        def stop(stage):
            if stage=='appended':raise b.ShutdownRequested()
        with b.Spool(self.root,self.policy,fault=stop) as spool:
            with self.assertRaises(b.ShutdownRequested):self.bridge(adapter,spool).run_once()
        with b.Spool(self.root,self.policy) as spool:self.bridge(adapter,spool).run_once()
        self.assertEqual(len(self.rows()),1)
        self.assertIn('bootstrap_ns',json.loads((self.root/'checkpoint.json').read_bytes()))

    def test_stop_before_run_performs_no_adapter_calls(self):
        adapter=WindowAdapter()
        with b.Spool(self.root,self.policy) as spool:
            with self.assertRaises(b.ShutdownRequested):
                self.bridge(adapter,spool,stop_requested=lambda:True).run_once()
        self.assertFalse(adapter.windows)

    def test_window_api_adds_until_and_rejects_outside_records(self):
        engine=FakeTransport();engine.log_replies[ID]=[log_response(frame(line(stamp='2026-10-19T09:00:00Z')))]
        adapter=b.DockerAPIAdapter(engine,self.policy);adapter.discover(b.PROJECT,self.policy.services)
        with self.assertRaisesRegex(b.BridgeError,'ADAPTER_RANGE_INVALID'):
            list(adapter.read_window(ID,BASE,'2026-10-19T08:00:30Z'))
        self.assertTrue(any('until=' in path for _,path,_ in engine.calls))

    def test_scan_corruption_is_rejected(self):
        with b.Spool(self.root,self.policy) as spool:self.bridge(WindowAdapter(),spool).run_once()
        path=self.root/'checkpoint.json';state=json.loads(path.read_bytes())
        state['scans'][ID]['until']=-1;path.write_text(json.dumps(state))
        with self.assertRaisesRegex(b.BridgeError,'SCAN_STATE_INVALID'):b.Spool(self.root,self.policy)

    def test_polling_success_has_finite_interval(self):
        fake=SimpleNamespace(run_once=lambda:b.Stats())
        event=Event();reports=[]
        self.assertEqual(b.polling_loop(fake,b.PollSettings(max_polls=3),event,lambda *x:reports.append(x)),3)
        self.assertEqual(event.delays,[5,5]);self.assertEqual(len(reports),3)

    def test_polling_retry_backoff_and_limit(self):
        def fail():raise b.BridgeError('ADAPTER_RETRIES_EXHAUSTED')
        event=Event()
        with self.assertRaisesRegex(b.BridgeError,'POLL_RETRIES_EXHAUSTED'):
            b.polling_loop(SimpleNamespace(run_once=fail),b.PollSettings(max_polls=0),event)
        self.assertEqual(event.delays,[1,2])

    def test_polling_one_failed_pilot_is_not_pass(self):
        def fail():raise b.BridgeError('ADAPTER_RETRIES_EXHAUSTED')
        with self.assertRaisesRegex(b.BridgeError,'POLL_RETRIES_EXHAUSTED'):
            b.polling_loop(SimpleNamespace(run_once=fail),b.PollSettings(),Event())

    def test_polling_resets_backoff_after_success(self):
        outcomes=[b.BridgeError('ADAPTER_RETRIES_EXHAUSTED'),b.Stats(),b.Stats()]
        def run():
            item=outcomes.pop(0)
            if isinstance(item,Exception):raise item
            return item
        event=Event();b.polling_loop(SimpleNamespace(run_once=run),b.PollSettings(max_polls=3),event)
        self.assertEqual(event.delays,[1,5])

    def test_polling_does_not_retry_spool_full(self):
        def fail():raise b.BridgeError('SPOOL_FULL')
        event=Event()
        with self.assertRaisesRegex(b.BridgeError,'SPOOL_FULL'):
            b.polling_loop(SimpleNamespace(run_once=fail),b.PollSettings(max_polls=0),event)
        self.assertFalse(event.delays)

    def test_polling_signal_wait_stops_without_next_poll(self):
        event=Event(stop_after=1)
        self.assertEqual(b.polling_loop(SimpleNamespace(run_once=lambda:b.Stats()),b.PollSettings(max_polls=0),event),1)

    def test_polling_shutdown_control_flow_is_successful_stop(self):
        def stop():raise b.ShutdownRequested()
        self.assertEqual(b.polling_loop(SimpleNamespace(run_once=stop),b.PollSettings(max_polls=0),Event()),1)

    def test_poll_config_rejects_busy_loop_and_unbounded_error_limit(self):
        for changes in [{'interval_seconds':0},{'max_polls':-1},{'max_failed_polls':99},{'interval_seconds':float('nan')}]:
            with self.assertRaises(b.BridgeError):b.PollSettings(**changes)

    def test_permission_plan_keeps_state_private_and_jsonl_group_readonly(self):
        applied=[];dirs=[]
        def permissions(fd,mode,gid=None):applied.append((mode,gid))
        with patch.object(b.Spool,'_directory_mode',side_effect=lambda path,mode,gid=None:dirs.append((path.name,mode,gid))), \
             patch.object(b,'posix_permissions',side_effect=permissions):
            with b.Spool(self.root,self.policy,reader_gid=19001) as spool:
                self.bridge(WindowAdapter({ID:[record()]}),spool).run_once()
        self.assertIn((0o640,19001),applied)
        self.assertIn((0o600,None),applied)
        self.assertIn(('spool',0o700,None),dirs)
        for service in b.SERVICES:self.assertIn((service,0o2750,19001),dirs)

    def test_reader_gid_zero_rejected_before_creation(self):
        with self.assertRaisesRegex(b.BridgeError,'SPOOL_PERMISSION_INVALID'):
            b.Spool(self.root,self.policy,reader_gid=0)
        self.assertFalse(self.root.exists())

    def test_dry_run_no_socket_stat_or_spool_creation(self):
        output=io.StringIO()
        with patch.object(b,'_run_live',side_effect=AssertionError('LIVE_FORBIDDEN')), \
             patch.object(b.Spool,'__init__',side_effect=AssertionError('SPOOL_FORBIDDEN')), \
             redirect_stdout(output):
            result=b.main(['--dry-run','--spool',str(self.root)])
        self.assertEqual(result,0);self.assertIn('Docker=disabled',output.getvalue())
        self.assertFalse(self.root.exists())

    def test_default_cli_cannot_run_live_and_arguments_are_sanitized(self):
        for args in [[],['--live'],['--dry-run','--poll-interval',SECRET]]:
            output=io.StringIO()
            with redirect_stdout(output),redirect_stderr(output):
                try:code=b.main(args)
                except SystemExit as exc:code=exc.code
            self.assertEqual(code,2)
            self.assertNotIn(SECRET,output.getvalue())
        self.assertFalse(self.root.exists())

    def test_policy_cli_compatibility(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(b.main(['--check-policy',str(ROOT/'config/alloy/bridge-policy.json')]),0)

    def test_live_dispatch_requires_explicit_arguments_without_running_it(self):
        with patch.object(b,'_run_live',return_value=0) as run,redirect_stdout(io.StringIO()):
            self.assertEqual(b.main(['--live','--spool',str(self.root),'--read-gid','19001','--bootstrap-since',BASE]),0)
        run.assert_called_once();self.assertFalse(self.root.exists())

    def test_live_environment_rejects_docker_group_for_reader(self):
        args=SimpleNamespace(socket=Path('/run/docker.sock'),spool=Path('/var/lib/beautyglow-logs'),read_gid=986)
        info=SimpleNamespace(st_mode=stat.S_IFSOCK|0o660,st_uid=0,st_gid=986)
        with patch.object(b.sys,'platform','linux'),patch.object(b.os,'geteuid',return_value=0,create=True), \
             patch.object(b.os,'stat',return_value=info):
            with self.assertRaisesRegex(b.BridgeError,'LIVE_GROUP_INVALID'):b._live_environment(args)

    def test_live_environment_rejects_alloy_writer_identity(self):
        with patch.object(b.sys,'platform','linux'),patch.object(b.os,'geteuid',return_value=473,create=True):
            with self.assertRaisesRegex(b.BridgeError,'LIVE_IDENTITY_INVALID'):
                b._live_environment(SimpleNamespace(socket='/run/test.sock',read_gid=19001))

    def test_live_spool_cannot_target_system_or_windows_directory(self):
        for path in ['/','/var/lib','/mnt/d/beautyglow-logs','/var/lib/beautyglow-logs/../other']:
            args=SimpleNamespace(socket=Path('/run/docker.sock'),spool=Path(path),read_gid=19001)
            with patch.object(b.sys,'platform','linux'),patch.object(b.os,'geteuid',return_value=0,create=True):
                with self.assertRaisesRegex(b.BridgeError,'SPOOL_PATH_UNSAFE'):b._live_environment(args)

    def test_live_spool_refuses_unknown_existing_files(self):
        self.root.mkdir();unknown=self.root/'unrelated.txt';unknown.write_text('synthetic unrelated data')
        with self.assertRaisesRegex(b.BridgeError,'SPOOL_PATH_UNSAFE'):
            b.Spool(self.root,self.policy,reader_gid=19001)
        self.assertEqual(unknown.read_text(),'synthetic unrelated data')

    def test_live_runner_lifecycle_with_only_fake_dependencies(self):
        engine=FakeTransport([summary(s,chr(97+i)*64) for i,s in enumerate(('nginx','wordpress','mysql'))])
        for ident in engine.inspections:engine.log_replies[ident]=[log_response(frame(line()))]
        closed=[];engine.close=lambda:closed.append(True)
        args=SimpleNamespace(socket=Path('/run/fake.sock'),spool=self.root,read_gid=19001,
                             api_version='1.51',bootstrap_since=BASE,batch_seconds=30,allow_source_replacement=False)
        original=b.Bridge
        def build(*a,**k):return original(*a,now=lambda:NOW,**k)
        with patch.object(b,'_live_environment'),patch.object(b,'UnixHTTPTransport',return_value=engine), \
             patch.object(b.Spool,'_directory_mode'),patch.object(b,'posix_permissions'), \
             patch.object(b,'Bridge',side_effect=build),patch.object(b.signal,'signal',return_value=None) as signals, \
             redirect_stdout(io.StringIO()):
            self.assertEqual(b._run_live(args,self.policy,b.PollSettings()),0)
        self.assertEqual(len(self.rows()),3)
        self.assertEqual(signals.call_count,4)
        self.assertEqual(closed,[True])

    def test_sigterm_handler_stops_runner_and_restores_handlers(self):
        engine=FakeTransport([summary(s,chr(97+i)*64) for i,s in enumerate(('nginx','wordpress','mysql'))])
        for ident in engine.inspections:engine.log_replies[ident]=[log_response(frame(line()))]
        engine.close=lambda:None
        args=SimpleNamespace(socket=Path('/run/fake.sock'),spool=self.root,read_gid=19001,
                             api_version='1.51',bootstrap_since=BASE,batch_seconds=30,allow_source_replacement=False)
        registered={};original_request=engine.request;original_bridge=b.Bridge
        def install(sig,handler):
            if handler is not None:registered[sig]=handler
            return None
        def request(*a,**k):
            registered[b.signal.SIGTERM](b.signal.SIGTERM,None)
            return original_request(*a,**k)
        engine.request=request
        def build(*a,**k):return original_bridge(*a,now=lambda:NOW,**k)
        output=io.StringIO()
        with patch.object(b,'_live_environment'),patch.object(b,'UnixHTTPTransport',return_value=engine), \
             patch.object(b.Spool,'_directory_mode'),patch.object(b,'posix_permissions'), \
             patch.object(b,'Bridge',side_effect=build),patch.object(b.signal,'signal',side_effect=install) as signals, \
             redirect_stdout(output):
            self.assertEqual(b._run_live(args,self.policy,b.PollSettings(max_polls=0)),0)
        self.assertIn('BRIDGE_STOPPED',output.getvalue());self.assertEqual(signals.call_count,4)
        self.assertEqual(self.rows(),[])


if __name__=='__main__':unittest.main()
