"""Synthetic tests only. Temporary spool directories live inside this folder."""
import ast
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("beautyglow_bridge", ROOT / "scripts/beautyglow-log-bridge.py")
b = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = b
SPEC.loader.exec_module(b)
BASE = "2026-10-19T08:00:00.123456789Z"
NOW = b.timestamp("2026-10-19T09:00:00Z")[1]


def container(service="nginx", ident="a", project=b.PROJECT, **kwargs):
    return b.Container(ident * 64, {"com.docker.compose.project": project,
                                   "com.docker.compose.service": service}, **kwargs)


def record(message="synthetic message", stamp=BASE, stream="stdout"):
    return b.Record(stamp, stream, message)


class FakeAdapter:
    def __init__(self, containers=None, records=None):
        self.containers = [container()] if containers is None else containers
        self.records = records or {}
        self.calls = []
        self.failures = 0
        self.partial_failures = 0

    def discover(self, project, services):
        assert project == b.PROJECT and set(services) == b.SERVICES
        if self.failures:
            self.failures -= 1
            raise b.RetryableAdapterError("password=FAKE_NOT_FOR_OUTPUT")
        return self.containers

    def read_logs(self, ident, since):
        self.calls.append((ident, since))
        count = 0
        for item in self.records.get(ident, []):
            # Invalid timestamps reach core validation, not hidden by the fake.
            if since is not None:
                try:
                    if b.timestamp(item.timestamp)[1] < b.timestamp(since)[1]:
                        continue
                except b.BridgeError:
                    pass
            yield item
            count += 1
            if count == 1 and self.partial_failures:
                self.partial_failures -= 1
                raise b.RetryableAdapterError("Authorization: Bearer FAKE_NOT_FOR_OUTPUT")


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="_test-runtime-", dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "spool"
        self.policy = b.Policy()

    def rows(self):
        return [json.loads(line) for p in sorted(self.root.glob("*/*.jsonl"))
                for line in p.read_text(encoding="utf8").splitlines()]

    def run_fake(self, adapter, policy=None, fault=None, sleep=lambda _: None):
        policy = policy or self.policy
        with b.Spool(self.root, policy, fault) as spool:
            return b.Bridge(adapter, spool, policy, now=lambda: NOW, sleep=sleep).run_once()

    def one(self, item):
        return FakeAdapter(records={container().id: [item]})

    def test_accept_exact_three_services(self):
        cs = [container(s, chr(97+i)) for i, s in enumerate(("nginx", "wordpress", "mysql"))]
        stats = self.run_fake(FakeAdapter(cs, {c.id: [record()] for c in cs}))
        self.assertEqual(stats.accepted, 3)
        self.assertEqual({r["service"] for r in self.rows()}, b.SERVICES)
        self.assertTrue(all(set(r) == {"timestamp", "project", "service", "stream", "message"} for r in self.rows()))

    def test_reject_other_project_and_service(self):
        cs = [container(project="old-lab"), container("grafana", "b"), container("nginx-extra", "c")]
        fake = FakeAdapter(cs)
        self.assertEqual(self.run_fake(fake).rejected_containers, 3)
        self.assertEqual(fake.calls, [])

    def test_reject_missing_or_invalid_metadata(self):
        cs = [b.Container("bad", {}), b.Container("a"*64, {}), None, container(running=False), container(tty=True)]
        fake = FakeAdapter(cs)
        self.assertEqual(self.run_fake(fake).rejected_containers, 5)
        self.assertEqual(fake.calls, [])

    def test_ambiguous_service_fails_before_read(self):
        fake = FakeAdapter([container(), container(ident="b")])
        with self.assertRaisesRegex(b.BridgeError, "CONTAINER_AMBIGUOUS"):
            self.run_fake(fake)
        self.assertFalse(fake.calls)

    def test_invalid_timestamp_no_append(self):
        for stamp in ["not-a-date", "2026-02-30T00:00:00Z", "2026-10-19T01:00:00+99:99", "2026-10-19T00:00:00"]:
            with self.subTest(stamp=stamp), self.assertRaisesRegex(b.BridgeError, "TIMESTAMP_INVALID"):
                self.run_fake(self.one(record(stamp=stamp)))
        self.assertEqual(self.rows(), [])

    def test_timestamp_normalizes_timezone_preserves_nanoseconds(self):
        self.run_fake(self.one(record(stamp="2026-10-19T15:00:00.123456789+07:00")))
        self.assertEqual(self.rows()[0]["timestamp"], BASE)

    def test_future_timestamp_rejected(self):
        with self.assertRaisesRegex(b.BridgeError, "TIMESTAMP_IN_FUTURE"):
            self.run_fake(self.one(record(stamp="2027-01-01T00:00:00Z")))

    def test_invalid_stream_rejected(self):
        with self.assertRaisesRegex(b.BridgeError, "RECORD_INVALID"):
            self.run_fake(self.one(record(stream="merged")))

    def test_password_assignments_redacted(self):
        message = 'password="FAKE PASS" pwd=FAKE_PWD token=FAKE_TOKEN api_key=FAKE_KEY'
        safe = b.redact(message)
        self.assertNotIn("FAKE", safe)

    def test_headers_redacted(self):
        safe = b.redact("Authorization: Bearer FAKE_AUTH\nCookie: sid=FAKE_COOKIE\nSet-Cookie: FAKE_SET\nnormal")
        self.assertNotIn("FAKE", safe)
        self.assertIn("normal", safe)

    def test_nested_json_redacted(self):
        safe = b.redact(json.dumps({"nested": [{"password": "FAKE_PASS", "Authorization": "FAKE_AUTH"}], "cookie": "FAKE_COOKIE"}))
        self.assertNotIn("FAKE", safe)

    def test_sensitive_query_even_encoded_key_removed(self):
        safe = b.redact('GET /search?%74oken=FAKE_QUERY&name=FAKE_NAME HTTP/1.1')
        self.assertNotIn("FAKE", safe)
        self.assertIn("/search?[REDACTED]", safe)

    def test_url_userinfo_removed(self):
        self.assertNotIn("FAKE", b.redact("https://FAKE_USER:FAKE_PASS@example.invalid/a"))

    def test_malformed_json_sensitive_assignment_redacted(self):
        self.assertNotIn("FAKE", b.redact('{"password":"FAKE_SECRET", broken'))

    def test_multiline_single_jsonl_and_streams(self):
        fake = FakeAdapter(records={container().id: [record("line one\nline two", stream="stderr"), record("stdout", stream="stdout")]})
        self.run_fake(fake)
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(self.rows()[0]["message"], "line one\nline two")
        self.assertEqual({r["stream"] for r in self.rows()}, {"stdout", "stderr"})

    def test_resume_does_not_duplicate(self):
        fake = self.one(record())
        self.run_fake(fake)
        stats = self.run_fake(fake)
        self.assertEqual((stats.accepted, stats.duplicates, len(self.rows())), (0, 1, 1))
        self.assertIsNotNone(fake.calls[-1][1])

    def test_identical_records_multiplicity_preserved(self):
        fake = FakeAdapter(records={container().id: [record(), record(), record()]})
        self.run_fake(fake)
        self.run_fake(fake)
        self.assertEqual(len(self.rows()), 3)

    def test_new_identical_occurrence_after_resume_preserved(self):
        fake = self.one(record())
        self.run_fake(fake)
        fake.records[container().id].append(record())
        self.run_fake(fake)
        self.assertEqual(len(self.rows()), 2)

    def test_retry_discovery_bounded(self):
        fake = self.one(record()); fake.failures = 2
        delays = []
        stats = self.run_fake(fake, sleep=delays.append)
        self.assertEqual(stats.retries, 2)
        self.assertEqual(delays, [0.25, 0.5])

    def test_partial_read_retry_deduplicates(self):
        fake = FakeAdapter(records={container().id: [record("first"), record("second")]})
        fake.partial_failures = 1
        stats = self.run_fake(fake)
        self.assertEqual(stats.retries, 1)
        self.assertEqual(len(self.rows()), 2)

    def test_retry_exhaustion_does_not_leak_error_text(self):
        fake = FakeAdapter(); fake.failures = 99
        with self.assertRaises(b.BridgeError) as caught:
            self.run_fake(fake)
        self.assertEqual(str(caught.exception), "ADAPTER_RETRIES_EXHAUSTED")

    def test_container_id_change_independent_checkpoint(self):
        self.run_fake(self.one(record()))
        c = container(ident="b")
        self.run_fake(FakeAdapter([c], {c.id: [record()]}))
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(len(json.loads((self.root/"checkpoint.json").read_bytes())["containers"]), 2)

    def test_out_of_order_within_window_resume(self):
        fake = FakeAdapter(records={container().id: [record("later", "2026-10-19T08:00:30Z"), record("earlier", BASE)]})
        self.run_fake(fake); self.run_fake(fake)
        self.assertEqual(len(self.rows()), 2)

    def test_record_outside_window_fails(self):
        fake = FakeAdapter(records={container().id: [record("new", "2026-10-19T08:10:00Z"), record("late", BASE)]})
        with self.assertRaisesRegex(b.BridgeError, "OUTSIDE_REPLAY_WINDOW"):
            self.run_fake(fake)

    def test_serialized_line_byte_limit_with_unicode(self):
        policy = replace(self.policy, max_line_bytes=512)
        self.run_fake(self.one(record("sen 🌸\n" * 1000)), policy)
        data = next(self.root.glob("*/*.jsonl")).read_bytes()
        self.assertLessEqual(len(data), 512)
        self.assertIn("[TRUNCATED]", self.rows()[0]["message"])

    def test_input_limit_rejects_no_checkpoint(self):
        with self.assertRaisesRegex(b.BridgeError, "INPUT_TOO_LARGE"):
            self.run_fake(self.one(record("x" * 65537)))
        self.assertEqual(self.rows(), [])

    def test_segment_rotation_and_total_spool_limit(self):
        policy = replace(self.policy, max_line_bytes=256, max_segment_bytes=400,
                         max_spool_bytes=12000, max_state_bytes=8000)
        fake = FakeAdapter(records={container().id: [record("synthetic-"+str(i)) for i in range(100)]})
        with self.assertRaisesRegex(b.BridgeError, "SPOOL_FULL"):
            self.run_fake(fake, policy)
        sizes = [p.stat().st_size for p in self.root.rglob("*") if p.is_file()]
        self.assertLessEqual(sum(sizes), policy.max_spool_bytes)
        self.assertTrue(all(p.stat().st_size <= 400 for p in self.root.glob("*/*.jsonl")))
        before = len(self.rows())
        try:
            self.run_fake(fake, policy)
        except b.BridgeError:
            pass
        self.assertEqual(before, len(self.rows()))

    def test_window_state_bound(self):
        policy = replace(self.policy, max_window_keys=2)
        fake = FakeAdapter(records={container().id: [record(str(i)) for i in range(3)]})
        with self.assertRaisesRegex(b.BridgeError, "REPLAY_WINDOW_LIMIT"):
            self.run_fake(fake, policy)
        self.assertEqual(len(self.rows()), 2)

    def test_old_container_state_bound(self):
        policy = replace(self.policy, max_checkpoint_containers=1)
        self.run_fake(self.one(record()), policy)
        c = container(ident="b")
        with self.assertRaisesRegex(b.BridgeError, "CONTAINER_STATE_LIMIT"):
            self.run_fake(FakeAdapter([c], {c.id: [record()]}), policy)

    def test_crash_after_prepare_replays_once(self):
        def crash(stage):
            if stage == "prepared": raise RuntimeError("synthetic crash")
        with self.assertRaises(b.BridgeError): self.run_fake(self.one(record()), fault=crash)
        self.run_fake(self.one(record()))
        self.assertEqual(len(self.rows()), 1)

    def test_crash_after_append_recovers_without_duplicate(self):
        def crash(stage):
            if stage == "appended": raise RuntimeError("synthetic crash")
        with self.assertRaises(b.BridgeError): self.run_fake(self.one(record()), fault=crash)
        self.run_fake(self.one(record()))
        self.assertEqual(len(self.rows()), 1)

    def test_partial_append_recovery(self):
        def crash(stage):
            if stage == "appended": raise RuntimeError("synthetic crash")
        with self.assertRaises(b.BridgeError): self.run_fake(self.one(record()), fault=crash)
        path = next(self.root.glob("*/*.jsonl"))
        with path.open("r+b") as f: f.truncate(path.stat().st_size - 5)
        self.run_fake(self.one(record()))
        self.assertEqual(len(self.rows()), 1)

    def test_corrupted_checkpoint_fails_closed(self):
        self.root.mkdir()
        (self.root/"checkpoint.json").write_text('{broken')
        with self.assertRaisesRegex(b.BridgeError, "SPOOL_OPEN_FAILED"):
            self.run_fake(self.one(record()))

    def test_concurrent_writer_rejected(self):
        with b.Spool(self.root, self.policy):
            with self.assertRaisesRegex(b.BridgeError, "SPOOL_OPEN_FAILED"):
                self.run_fake(self.one(record()))

    def test_policy_rejects_scope_expansion(self):
        with self.assertRaises(b.BridgeError): replace(self.policy, project="old-lab")
        with self.assertRaises(b.BridgeError): replace(self.policy, services=("nginx", "wordpress", "grafana"))

    def test_no_real_transport_calls(self):
        with patch("socket.socket", side_effect=AssertionError("network forbidden")), \
             patch("subprocess.Popen", side_effect=AssertionError("subprocess forbidden")):
            self.assertEqual(self.run_fake(self.one(record())).accepted, 1)

    def test_python_and_policy_syntax(self):
        ast.parse((ROOT/"scripts/beautyglow-log-bridge.py").read_text(encoding="utf8"))
        self.assertEqual(b.Policy.load(ROOT/"config/alloy/bridge-policy.json"), self.policy)

    def test_unknown_adapter_exception_is_sanitized(self):
        fake = FakeAdapter()
        fake.discover = lambda *_: (_ for _ in ()).throw(RuntimeError("password=FAKE_SECRET"))
        with self.assertRaises(b.BridgeError) as caught:
            self.run_fake(fake)
        self.assertEqual(str(caught.exception), "BRIDGE_OPERATION_FAILED")

    def test_exception_code_cannot_carry_secret(self):
        self.assertEqual(str(b.BridgeError("token=FAKE_SECRET")), "BRIDGE_OPERATION_FAILED")

    def test_valid_json_corrupt_checkpoint_fails_closed(self):
        self.run_fake(self.one(record()))
        path = self.root/"checkpoint.json"
        state = json.loads(path.read_bytes())
        counts = state["containers"][container().id]["counts"]
        counts[next(iter(counts))] = -1
        path.write_text(json.dumps(state), encoding="utf8")
        with self.assertRaisesRegex(b.BridgeError, "STATE_INVALID"):
            self.run_fake(self.one(record()))
        self.assertEqual(len(self.rows()), 1)

    def test_pending_corruption_does_not_overwrite_spool(self):
        def crash(stage):
            if stage == "appended": raise RuntimeError("synthetic crash")
        with self.assertRaises(b.BridgeError): self.run_fake(self.one(record()), fault=crash)
        path = next(self.root.glob("*/*.jsonl"))
        data = path.read_bytes()
        path.write_bytes(data.replace(b"synthetic", b"different"))
        before = path.read_bytes()
        with self.assertRaisesRegex(b.BridgeError, "RECOVERY_CONFLICT"):
            self.run_fake(self.one(record()))
        self.assertEqual(path.read_bytes(), before)

    def test_replay_window_advances_without_unbounded_memory(self):
        policy = replace(self.policy, max_window_keys=2)
        rows = [record(str(i), b.stamp_from_ns(b.timestamp(BASE)[1] + i * 180 * 10**9)) for i in range(10)]
        fake = FakeAdapter(records={container().id: rows})
        self.run_fake(fake, policy); self.run_fake(fake, policy)
        self.assertEqual(len(self.rows()), 10)
        cp = json.loads((self.root/"checkpoint.json").read_bytes())["containers"][container().id]
        self.assertEqual(len(cp["counts"]), 1)

    def test_identical_message_on_separate_streams(self):
        fake = FakeAdapter(records={container().id: [record(stream="stdout"), record(stream="stderr")]})
        self.run_fake(fake); self.run_fake(fake)
        self.assertEqual(len(self.rows()), 2)

    def test_policy_rejects_nonfinite_retry(self):
        with self.assertRaises(b.BridgeError): replace(self.policy, retry_max_seconds=float("nan"))

    def test_metadata_is_copied_before_adapter_reads(self):
        c = container()
        fake = FakeAdapter([c], {c.id: [record()]})
        read = fake.read_logs
        def mutate(ident, since):
            c.labels["com.docker.compose.service"] = "../../outside"
            yield from read(ident, since)
        fake.read_logs = mutate
        self.run_fake(fake)
        self.assertEqual(self.rows()[0]["service"], "nginx")


if __name__ == "__main__":
    unittest.main()
