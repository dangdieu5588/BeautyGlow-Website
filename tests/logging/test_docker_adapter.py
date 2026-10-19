"""Docker API contract tests with in-memory responses; all real I/O is denied."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import re
import tempfile
import traceback
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from test_bridge import b, BASE, NOW


SECRET = "SYNTHETIC_CREDENTIAL_NEVER_PRINT"
ID = "a" * 64


def summary(service="nginx", ident=ID, project=b.PROJECT, state="running"):
    return {"Id": ident, "State": state, "Labels": {
        "com.docker.compose.project": project,
        "com.docker.compose.service": service,
        "unrelated-label": SECRET,
    }, "Command": SECRET}


def inspection(item):
    return {"Id": item["Id"], "State": {"Running": True, "Error": SECRET},
            "Config": {"Tty": False, "Labels": dict(item["Labels"]), "Env": ["PASSWORD=" + SECRET]},
            "Mounts": [{"Source": SECRET}]}


def line(message="synthetic log", stamp=BASE):
    return (stamp + " " + message + "\n").encode("utf8")


def frame(payload, stream=1, reserved=b"\0\0\0"):
    return bytes([stream]) + reserved + len(payload).to_bytes(4, "big") + payload


class FakeResponse:
    def __init__(self, body=b"", status=200, kind="application/json", max_read=8192,
                 fail_at=None, failure=None, bad_read=None, close_error=False):
        self.body, self.status = body, status
        self.headers = {"Content-Type": kind, "Authorization": SECRET, "Set-Cookie": SECRET}
        self.pos, self.read_calls, self.closed = 0, 0, False
        self.max_read, self.fail_at = max_read, fail_at
        self.failure, self.bad_read, self.close_error = failure, bad_read, close_error

    def read(self, size):
        self.read_calls += 1
        if self.bad_read is not None:
            return self.bad_read(size)
        if self.fail_at is not None and self.pos >= self.fail_at:
            raise self.failure or ConnectionResetError(SECRET)
        end = min(self.pos + size, self.pos + self.max_read, len(self.body))
        if self.fail_at is not None:
            end = min(end, self.fail_at)
        result = self.body[self.pos:end]
        self.pos = end
        return result

    def close(self):
        self.closed = True
        if self.close_error:
            raise RuntimeError(SECRET)


def json_response(value, **kwargs):
    return FakeResponse(json.dumps(value).encode(), **kwargs)


def log_response(body, **kwargs):
    return FakeResponse(body, kind="application/vnd.docker.raw-stream", **kwargs)


class FakeTransport:
    def __init__(self, summaries=None):
        self.summaries = [summary()] if summaries is None else summaries
        self.inspections = {s["Id"]: inspection(s) for s in self.summaries if isinstance(s, dict) and "Id" in s and "Labels" in s}
        self.list_replies, self.inspect_replies, self.log_replies = [], {}, {}
        self.calls, self.responses = [], []

    def request(self, method, path, *, timeout):
        assert method == "GET", "Only GET is allowed"
        parsed = urlsplit(path)
        assert not parsed.netloc and not parsed.scheme, "No host or real endpoint"
        self.calls.append((method, path, timeout))
        route = re.sub(r"^/v1\.\d{1,2}", "", parsed.path)
        if route == "/containers/json":
            response = self.list_replies.pop(0) if self.list_replies else json_response(self.summaries)
        else:
            match = re.fullmatch(r"/containers/([0-9a-f]{64})/(json|logs)", route)
            assert match, "Unexpected API route"
            ident, operation = match.groups()
            if operation == "json":
                queue = self.inspect_replies.get(ident, [])
                response = queue.pop(0) if queue else json_response(self.inspections[ident])
            else:
                queue = self.log_replies.get(ident, [])
                response = queue.pop(0) if queue else log_response(b"")
        if isinstance(response, Exception):
            raise response
        self.responses.append(response)
        return response


class AdapterTests(unittest.TestCase):
    def setUp(self):
        # Every test, including full Bridge integration, has these tripwires.
        for target in ("socket.socket", "socket.create_connection", "subprocess.Popen", "urllib.request.urlopen"):
            patcher = patch(target, side_effect=AssertionError("REAL_IO_FORBIDDEN"))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.temp = tempfile.TemporaryDirectory(prefix="_adapter-runtime-", dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/"spool"
        self.policy = b.Policy()

    def adapter(self, transport=None, **kwargs):
        transport = transport or FakeTransport()
        return transport, b.DockerAPIAdapter(transport, self.policy, **kwargs)

    def collect(self, body, **response_kwargs):
        transport, adapter = self.adapter()
        response = log_response(body, **response_kwargs)
        transport.log_replies[ID] = [response]
        adapter.discover(b.PROJECT, self.policy.services)
        return list(adapter.read_logs(ID, None)), response

    def rows(self):
        return [json.loads(l) for p in self.root.glob("*/*.jsonl") for l in p.read_text(encoding="utf8").splitlines()]

    def run_bridge(self, adapter, sleep=lambda _: None, fault=None):
        with b.Spool(self.root, self.policy, fault=fault) as spool:
            return b.Bridge(adapter, spool, self.policy, now=lambda: NOW, sleep=sleep).run_once()

    def test_requires_injected_transport(self):
        with self.assertRaisesRegex(b.BridgeError, "ADAPTER_CONFIG_INVALID"):
            b.DockerAPIAdapter(None, self.policy)

    def test_discovery_requests_project_and_running_filter(self):
        transport, adapter = self.adapter()
        result = adapter.discover(b.PROJECT, self.policy.services)
        query = parse_qs(urlsplit(transport.calls[0][1]).query)
        filters = json.loads(query["filters"][0])
        self.assertEqual(filters, {"label": ["com.docker.compose.project=beautyglow-website"], "status": ["running"]})
        self.assertEqual(query["all"], ["false"])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, ID)

    def test_accept_all_three_services(self):
        transport, adapter = self.adapter(FakeTransport([summary(s, chr(97+i)*64) for i, s in enumerate(self.policy.services)]))
        result = adapter.discover(b.PROJECT, self.policy.services)
        self.assertEqual({c.labels["com.docker.compose.service"] for c in result}, b.SERVICES)

    def test_wrong_project_or_service_never_inspected(self):
        transport, adapter = self.adapter(FakeTransport([summary(project="old-lab"), summary(service="grafana", ident="b"*64)]))
        self.assertEqual(adapter.discover(b.PROJECT, self.policy.services), ())
        self.assertEqual(len(transport.calls), 1)

    def test_scope_arguments_cannot_expand(self):
        transport, adapter = self.adapter()
        for project, services in [("old-lab", self.policy.services), (b.PROJECT, ("nginx", "mysql", "grafana"))]:
            with self.assertRaisesRegex(b.BridgeError, "ADAPTER_SCOPE_DENIED"):
                adapter.discover(project, services)
        self.assertFalse(transport.calls)

    def test_unapproved_or_injected_id_never_requested(self):
        transport, adapter = self.adapter()
        adapter.discover(b.PROJECT, self.policy.services)
        count = len(transport.calls)
        for ident in ["b"*64, "../secrets", ID+"?all=true", "short"]:
            with self.assertRaisesRegex(b.BridgeError, "ADAPTER_SCOPE_DENIED"):
                list(adapter.read_logs(ident, None))
        self.assertEqual(len(transport.calls), count)

    def test_invalid_container_id_rejected(self):
        transport, adapter = self.adapter(FakeTransport([summary(ident="../bad")]))
        with self.assertRaisesRegex(b.BridgeError, "ADAPTER_METADATA_INVALID"):
            adapter.discover(b.PROJECT, self.policy.services)
        self.assertEqual(len(transport.calls), 1)

    def test_ambiguous_service_rejected_before_inspect(self):
        transport, adapter = self.adapter(FakeTransport([summary(), summary(ident="b"*64)]))
        with self.assertRaisesRegex(b.BridgeError, "CONTAINER_AMBIGUOUS"):
            adapter.discover(b.PROJECT, self.policy.services)
        self.assertEqual(len(transport.calls), 1)

    def test_stopped_summary_not_inspected(self):
        transport, adapter = self.adapter(FakeTransport([summary(state="exited")]))
        self.assertEqual(adapter.discover(b.PROJECT, self.policy.services), ())
        self.assertEqual(len(transport.calls), 1)

    def test_inspect_id_labels_and_running_revalidated(self):
        for change in ("id", "project", "service", "running"):
            with self.subTest(change=change):
                transport, adapter = self.adapter()
                obj = transport.inspections[ID]
                if change == "id": obj["Id"] = "b"*64
                if change == "project": obj["Config"]["Labels"]["com.docker.compose.project"] = "old-lab"
                if change == "service": obj["Config"]["Labels"]["com.docker.compose.service"] = "mysql"
                if change == "running": obj["State"]["Running"] = False
                with self.assertRaisesRegex(b.BridgeError, "ADAPTER_METADATA_CHANGED"):
                    adapter.discover(b.PROJECT, self.policy.services)

    def test_tty_or_missing_tty_rejected(self):
        for tty in (True, None, "false"):
            transport, adapter = self.adapter()
            transport.inspections[ID]["Config"]["Tty"] = tty
            with self.assertRaisesRegex(b.BridgeError, "ADAPTER_TTY_UNSUPPORTED"):
                adapter.discover(b.PROJECT, self.policy.services)

    def test_missing_inspect_fields_rejected(self):
        transport, adapter = self.adapter()
        transport.inspections[ID] = {"Id": ID}
        with self.assertRaisesRegex(b.BridgeError, "ADAPTER_METADATA_INVALID"):
            adapter.discover(b.PROJECT, self.policy.services)

    def test_scope_change_before_logs_blocks_read(self):
        transport, adapter = self.adapter()
        adapter.discover(b.PROJECT, self.policy.services)
        transport.inspections[ID]["Config"]["Labels"]["com.docker.compose.project"] = "old-lab"
        with self.assertRaisesRegex(b.BridgeError, "ADAPTER_METADATA_CHANGED"):
            list(adapter.read_logs(ID, None))
        self.assertFalse(any("/logs?" in p for _, p, _ in transport.calls))

    def test_failed_discovery_revokes_old_ids(self):
        transport, adapter = self.adapter()
        adapter.discover(b.PROJECT, self.policy.services)
        transport.list_replies = [FakeResponse(status=403)]
        with self.assertRaises(b.BridgeError): adapter.discover(b.PROJECT, self.policy.services)
        count = len(transport.calls)
        with self.assertRaisesRegex(b.BridgeError, "ADAPTER_SCOPE_DENIED"):
            list(adapter.read_logs(ID, None))
        self.assertEqual(len(transport.calls), count)

    def test_metadata_credentials_not_returned_or_cached(self):
        transport, adapter = self.adapter()
        result = adapter.discover(b.PROJECT, self.policy.services)
        self.assertEqual(set(result[0].labels), {"com.docker.compose.project", "com.docker.compose.service"})
        self.assertNotIn(SECRET, repr(result))
        self.assertNotIn(SECRET, repr(adapter._approved))
        self.assertTrue(all(r.closed for r in transport.responses))

    def test_returned_metadata_cannot_mutate_authority(self):
        transport, adapter = self.adapter()
        result = adapter.discover(b.PROJECT,self.policy.services)
        with self.assertRaises(TypeError):
            result[0].labels['com.docker.compose.service']='grafana'
        self.assertEqual(adapter._approved[ID].labels['com.docker.compose.service'],'nginx')

    def test_stdout_stderr_and_nanoseconds(self):
        records, response = self.collect(frame(line("out"), 1)+frame(line("err"), 2))
        self.assertEqual([(r.stream, r.message, r.timestamp) for r in records], [("stdout", "out", BASE), ("stderr", "err", BASE)])
        self.assertTrue(response.closed)

    def test_split_headers_and_payload_one_byte_reads(self):
        records, _ = self.collect(frame(line("sen 🌸")), max_read=1)
        self.assertEqual(records[0].message, "sen 🌸")

    def test_line_split_across_frames(self):
        payload = line("joined")
        records, _ = self.collect(frame(payload[:10])+frame(payload[10:20])+frame(payload[20:]))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].message, "joined")

    def test_interleaved_stream_buffers_do_not_mix(self):
        first, second = line("out"), line("err")
        records, _ = self.collect(frame(first[:15],1)+frame(second,2)+frame(first[15:],1))
        self.assertEqual([(r.stream,r.message) for r in records], [("stderr","err"),("stdout","out")])

    def test_multiple_lines_in_frame(self):
        records, _ = self.collect(frame(line("one")+line("two")))
        self.assertEqual([r.message for r in records], ["one","two"])

    def test_timezone_normalized(self):
        records, _ = self.collect(frame(line(stamp="2026-10-19T15:00:00.123456789+07:00")))
        self.assertEqual(records[0].timestamp, BASE)

    def test_empty_stream_and_zero_length_frame(self):
        records, _ = self.collect(b"")
        self.assertEqual(records, [])
        records, _ = self.collect(frame(b"")+frame(line()))
        self.assertEqual(len(records), 1)

    def test_bad_stream_and_reserved_bytes_rejected(self):
        for body in [frame(line(),0),frame(line(),3),frame(line(),255),frame(line(),reserved=b"\1\0\0")]:
            with self.subTest(body_length=len(body)), self.assertRaisesRegex(b.BridgeError, "ADAPTER_FRAME_INVALID"):
                self.collect(body)

    def test_partial_header_and_frame_are_retryable(self):
        for body in [b"\1\0",frame(line())[:-3]]:
            with self.assertRaises(b.RetryableAdapterError) as caught: self.collect(body)
            self.assertNotIn(SECRET, str(caught.exception))

    def test_unterminated_line_not_committed(self):
        with self.assertRaises(b.RetryableAdapterError):
            self.collect(frame(line()[:-1]))

    def test_utf8_and_timestamp_errors_sanitized(self):
        for payload, code in [(b"\xff\n","ADAPTER_ENCODING_INVALID"), (b"invalid "+SECRET.encode()+b"\n","TIMESTAMP_INVALID")]:
            with self.assertRaises(b.BridgeError) as caught: self.collect(frame(payload))
            self.assertEqual(str(caught.exception), code)
            self.assertNotIn(SECRET, "".join(traceback.format_exception(caught.exception)))

    def test_since_is_inclusive_precise_and_snapshot_is_bounded(self):
        transport, adapter = self.adapter()
        adapter.discover(b.PROJECT, self.policy.services)
        list(adapter.read_logs(ID, BASE))
        path = [p for _,p,_ in transport.calls if "/logs?" in p][0]
        params = parse_qs(urlsplit(path).query)
        # Docker wire time differs from the RFC3339Nano record/CLI value.
        self.assertEqual(params["since"], ["1792396800.123456789"])
        self.assertEqual(params["follow"], ["false"])
        self.assertEqual(params["stdout"], ["true"])
        self.assertEqual(params["stderr"], ["true"])
        self.assertEqual(params["timestamps"], ["true"])
        self.assertTrue(all(timeout==5.0 for _,_,timeout in transport.calls))

    def test_bad_since_rejected_without_logs_request(self):
        transport, adapter = self.adapter()
        adapter.discover(b.PROJECT, self.policy.services)
        with self.assertRaisesRegex(b.BridgeError, "TIMESTAMP_INVALID"):
            list(adapter.read_logs(ID, "invalid?token="+SECRET))
        self.assertFalse(any("/logs?" in p for _,p,_ in transport.calls))

    def test_retryable_and_permanent_http_statuses_never_read_body(self):
        for status in (404,408,429,500,502,503,504,400,401,403,301,302):
            with self.subTest(status=status):
                transport, adapter = self.adapter()
                response = FakeResponse(SECRET.encode(),status=status)
                transport.list_replies = [response]
                error = b.RetryableAdapterError if status in (404,408,429,500,502,503,504) else b.BridgeError
                with self.assertRaises(error) as caught: adapter.discover(b.PROJECT,self.policy.services)
                self.assertEqual(response.read_calls,0)
                self.assertTrue(response.closed)
                self.assertNotIn(SECRET,"".join(traceback.format_exception(caught.exception)))

    def test_open_timeout_and_unknown_transport_error_sanitized(self):
        for exc,kind in [(TimeoutError(SECRET),b.RetryableAdapterError),(ConnectionResetError(SECRET),b.RetryableAdapterError),(RuntimeError(SECRET),b.BridgeError)]:
            transport, adapter = self.adapter(); transport.list_replies=[exc]
            with self.assertRaises(kind) as caught: adapter.discover(b.PROJECT,self.policy.services)
            self.assertNotIn(SECRET,"".join(traceback.format_exception(caught.exception)))

    def test_read_timeout_closes_response(self):
        transport, adapter = self.adapter()
        response = FakeResponse(fail_at=0,failure=TimeoutError(SECRET))
        transport.list_replies=[response]
        with self.assertRaises(b.RetryableAdapterError): adapter.discover(b.PROJECT,self.policy.services)
        self.assertTrue(response.closed)

    def test_elapsed_deadline_is_retryable(self):
        transport = FakeTransport()
        ticks=iter([0.0,11.0])
        adapter=b.DockerAPIAdapter(transport,self.policy,clock=lambda:next(ticks))
        with self.assertRaises(b.RetryableAdapterError): adapter.discover(b.PROJECT,self.policy.services)
        self.assertTrue(transport.responses[0].closed)

    def test_bad_content_type_headers_and_status(self):
        responses=[FakeResponse(kind="text/plain"),FakeResponse(kind="application/json",status="200")]
        missing=FakeResponse();missing.headers={}
        responses.append(missing)
        duplicate=FakeResponse();duplicate.headers["content-type"]="application/json"
        responses.append(duplicate)
        for response in responses:
            transport,adapter=self.adapter();transport.list_replies=[response]
            with self.assertRaisesRegex(b.BridgeError,"ADAPTER_RESPONSE_INVALID"):
                adapter.discover(b.PROJECT,self.policy.services)
            self.assertTrue(response.closed)

    def test_invalid_json_duplicate_keys_and_nonfinite_number(self):
        for raw in [b'{broken',b'{"Id":1,"Id":2}',b'[NaN]',b'\xff',b'{}',b'[null]']:
            transport,adapter=self.adapter();transport.list_replies=[FakeResponse(raw)]
            with self.assertRaisesRegex(b.BridgeError,"ADAPTER_METADATA_INVALID"):
                adapter.discover(b.PROJECT,self.policy.services)

    def test_metadata_body_and_candidate_limits(self):
        limits=replace(b.AdapterLimits(),max_metadata_bytes=64)
        transport,adapter=self.adapter(limits=limits)
        with self.assertRaisesRegex(b.BridgeError,"ADAPTER_METADATA_LIMIT"):
            adapter.discover(b.PROJECT,self.policy.services)
        limits=replace(b.AdapterLimits(),max_candidates=1)
        transport,adapter=self.adapter(FakeTransport([summary(),summary("mysql","b"*64)]),limits=limits)
        with self.assertRaisesRegex(b.BridgeError,"ADAPTER_METADATA_LIMIT"):
            adapter.discover(b.PROJECT,self.policy.services)

    def test_transport_cannot_exceed_read_contract(self):
        for bad in [lambda n:b'x'*(n+1),lambda n:"not bytes"]:
            transport,adapter=self.adapter();transport.list_replies=[FakeResponse(bad_read=bad)]
            with self.assertRaisesRegex(b.BridgeError,"ADAPTER_RESPONSE_INVALID"):
                adapter.discover(b.PROJECT,self.policy.services)

    def test_oversized_frame_rejected_before_payload(self):
        limits=replace(b.AdapterLimits(),max_frame_bytes=32,read_chunk_bytes=8)
        transport,adapter=self.adapter(limits=limits)
        adapter.discover(b.PROJECT,self.policy.services)
        response=log_response(b'\1\0\0\0'+(33).to_bytes(4,'big')+SECRET.encode())
        transport.log_replies[ID]=[response]
        with self.assertRaisesRegex(b.BridgeError,"ADAPTER_FRAME_LIMIT"):
            list(adapter.read_logs(ID,None))
        self.assertEqual(response.pos,8)
        self.assertTrue(response.closed)

    def test_input_line_limit_across_frames(self):
        transport,adapter=self.adapter()
        transport.log_replies[ID]=[log_response(frame(line('x'*65537)))]
        adapter.discover(b.PROJECT,self.policy.services)
        with self.assertRaisesRegex(b.BridgeError,"ADAPTER_INPUT_LIMIT"):
            list(adapter.read_logs(ID,None))

    def test_stream_byte_frame_and_record_limits(self):
        cases=[(replace(b.AdapterLimits(),max_stream_bytes=64,max_frame_bytes=64,read_chunk_bytes=8),frame(line('one'))+frame(line('two')),"ADAPTER_STREAM_LIMIT"),
               (replace(b.AdapterLimits(),max_frames=1),frame(b'')+frame(b''),"ADAPTER_STREAM_LIMIT"),
               (replace(b.AdapterLimits(),max_records=1),frame(line('one')+line('two')),"ADAPTER_RECORD_LIMIT")]
        for limits,body,code in cases:
            transport,adapter=self.adapter(limits=limits);transport.log_replies[ID]=[log_response(body)]
            adapter.discover(b.PROJECT,self.policy.services)
            with self.assertRaisesRegex(b.BridgeError,code): list(adapter.read_logs(ID,None))

    def test_multiplexed_content_type_supported(self):
        transport,adapter=self.adapter();response=log_response(frame(line()))
        response.headers["Content-Type"]="application/vnd.docker.multiplexed-stream"
        transport.log_replies[ID]=[response];adapter.discover(b.PROJECT,self.policy.services)
        self.assertEqual(len(list(adapter.read_logs(ID,None))),1)

    def test_consumer_can_close_stream_early(self):
        transport,adapter=self.adapter();response=log_response(frame(line('one')+line('two')))
        transport.log_replies[ID]=[response];adapter.discover(b.PROJECT,self.policy.services)
        reader=adapter.read_logs(ID,None)
        self.assertEqual(next(reader).message,'one')
        reader.close()
        self.assertTrue(response.closed)

    def test_close_error_does_not_leak(self):
        output=io.StringIO()
        with redirect_stdout(output),redirect_stderr(output):
            records,response=self.collect(frame(line()),close_error=True)
        self.assertEqual(len(records),1)
        self.assertEqual(output.getvalue(),"")
        self.assertTrue(response.closed)

    def test_pinned_api_version_no_negotiation_or_fallback(self):
        transport,adapter=self.adapter(api_version="1.51")
        transport.list_replies=[FakeResponse(status=400)]
        with self.assertRaises(b.BridgeError): adapter.discover(b.PROJECT,self.policy.services)
        self.assertEqual(len(transport.calls),1)
        self.assertTrue(transport.calls[0][1].startswith('/v1.51/'))
        for value in ['latest','1.51/../../secrets','https://real-host','1.51?token='+SECRET]:
            with self.assertRaisesRegex(b.BridgeError,'ADAPTER_CONFIG_INVALID'):
                b.DockerAPIAdapter(transport,self.policy,api_version=value)

    def test_adapter_limits_reject_invalid_timeouts(self):
        for value in [0,-1,float('inf'),float('nan'),True,'5']:
            with self.assertRaisesRegex(b.BridgeError,'ADAPTER_CONFIG_INVALID'):
                replace(b.AdapterLimits(),timeout_seconds=value)

    def test_mid_frame_disconnect_replays_without_loss_or_duplicate(self):
        transport,adapter=self.adapter()
        complete=frame(line('first'))
        full=complete+frame(line('second'))
        transport.log_replies[ID]=[log_response(full,fail_at=len(complete)+10),log_response(full)]
        stats=self.run_bridge(adapter)
        self.assertEqual(stats.retries,1)
        self.assertEqual([r['message'] for r in self.rows()],['first','second'])
        self.assertTrue(all(r.closed for r in transport.responses))

    def test_eof_in_payload_reconnects_without_partial_commit(self):
        transport,adapter=self.adapter()
        first,second=frame(line('first')),frame(line('second'))
        transport.log_replies[ID]=[log_response(first+second[:-3]),log_response(first+second)]
        self.run_bridge(adapter)
        self.assertEqual([r['message'] for r in self.rows()],['first','second'])

    def test_out_of_order_streams_and_snapshot_replay(self):
        transport,adapter=self.adapter()
        body=frame(line('later','2026-10-19T08:00:30Z'),2)+frame(line('earlier'),1)
        transport.log_replies[ID]=[log_response(body),log_response(body)]
        self.run_bridge(adapter);self.run_bridge(adapter)
        self.assertEqual([(r['stream'],r['message']) for r in self.rows()],[('stderr','later'),('stdout','earlier')])

    def test_identity_failure_requires_fresh_discovery(self):
        transport,adapter=self.adapter()
        adapter.discover(b.PROJECT,self.policy.services)
        transport.inspections[ID]['State']['Running']=False
        with self.assertRaises(b.BridgeError):list(adapter.read_logs(ID,None))
        count=len(transport.calls)
        with self.assertRaisesRegex(b.BridgeError,'ADAPTER_SCOPE_DENIED'):
            list(adapter.read_logs(ID,None))
        self.assertEqual(len(transport.calls),count)

    def test_timeout_after_complete_record_replays_multiplicity(self):
        transport,adapter=self.adapter()
        one=frame(line('same'))
        transport.log_replies[ID]=[log_response(one*2,fail_at=len(one),failure=TimeoutError(SECRET)),log_response(one*2)]
        self.run_bridge(adapter)
        self.assertEqual([r['message'] for r in self.rows()],['same','same'])

    def test_restart_adapter_and_bridge_resume_checkpoint(self):
        transport,adapter=self.adapter()
        body=frame(line('one'))+frame(line('two'))
        transport.log_replies[ID]=[log_response(body),log_response(body)]
        self.run_bridge(adapter)
        self.run_bridge(b.DockerAPIAdapter(transport,self.policy))
        self.assertEqual(len(self.rows()),2)
        paths=[p for _,p,_ in transport.calls if '/logs?' in p]
        self.assertNotIn('since',parse_qs(urlsplit(paths[0]).query))
        self.assertIn('since',parse_qs(urlsplit(paths[1]).query))

    def test_container_replaced_after_404_retry_rediscovery(self):
        transport,adapter=self.adapter()
        new=summary(ident='b'*64)
        # Simulate old ID disappearing at inspect, then a fresh discovery.
        transport.inspect_replies[ID]=[json_response(inspection(summary())),FakeResponse(status=404)]
        transport.list_replies=[json_response([summary()]),json_response([new])]
        transport.inspections[new['Id']]=inspection(new)
        transport.log_replies[new['Id']]=[log_response(frame(line('new container')))]
        stats=self.run_bridge(adapter)
        self.assertEqual(stats.retries,1)
        self.assertEqual(self.rows()[0]['message'],'new container')
        self.assertFalse(any('/containers/'+ID+'/logs?' in p for _,p,_ in transport.calls))

    def test_new_id_has_independent_checkpoint(self):
        transport,adapter=self.adapter()
        transport.log_replies[ID]=[log_response(frame(line('same content')))]
        self.run_bridge(adapter)
        new=summary(ident='b'*64)
        transport.summaries=[new];transport.inspections[new['Id']]=inspection(new)
        transport.log_replies[new['Id']]=[log_response(frame(line('same content')))]
        self.run_bridge(adapter)
        self.assertEqual(len(self.rows()),2)
        state=json.loads((self.root/'checkpoint.json').read_bytes())
        self.assertEqual(set(state['containers']),{ID,new['Id']})

    def test_realistic_multiline_and_redaction_no_sensitive_output(self):
        transport,adapter=self.adapter()
        body=frame(line('Authorization: Bearer '+SECRET),2)+frame(line('GET /?token='+SECRET+' HTTP/1.1'))+frame(line('password='+SECRET))
        transport.log_replies[ID]=[log_response(body)]
        output=io.StringIO()
        with redirect_stdout(output),redirect_stderr(output): self.run_bridge(adapter)
        self.assertEqual(len(self.rows()),3)
        self.assertNotIn(SECRET,output.getvalue())
        for path in self.root.rglob('*'):
            if path.is_file(): self.assertNotIn(SECRET.encode(),path.read_bytes())

    def test_permanent_error_after_prefix_resume_no_duplicate(self):
        transport,adapter=self.adapter()
        first=frame(line('committed'))
        transport.log_replies[ID]=[log_response(first+frame(line(),stream=3))]
        with self.assertRaisesRegex(b.BridgeError,'ADAPTER_FRAME_INVALID'): self.run_bridge(adapter)
        transport.log_replies[ID]=[log_response(first+frame(line('recovered')))]
        self.run_bridge(adapter)
        self.assertEqual([r['message'] for r in self.rows()],['committed','recovered'])

    def test_retry_exhaustion_keeps_checkpoint_no_secret_traceback(self):
        transport,adapter=self.adapter()
        one=frame(line('committed'))
        transport.log_replies[ID]=[log_response(one+b'\1') for _ in range(self.policy.retry_attempts)]
        with self.assertRaisesRegex(b.BridgeError,'ADAPTER_RETRIES_EXHAUSTED') as caught:
            self.run_bridge(adapter)
        self.assertNotIn(SECRET,''.join(traceback.format_exception(caught.exception)))
        self.assertEqual(len(self.rows()),1)


if __name__ == '__main__':
    unittest.main()
