"""Logical-byte stop-budget tests using only disposable repository-local spools."""
from contextlib import redirect_stdout
import errno
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_bridge as fixture

b = fixture.b
POLICY = b.Policy()
LIMIT = 8388608


class SpoolStopTests(unittest.TestCase):
    def setUp(self):
        for name in ('socket.socket', 'socket.create_connection', 'socket.getaddrinfo',
                     'subprocess.Popen', 'urllib.request.urlopen'):
            guard = patch(name, side_effect=AssertionError('REAL_IO_FORBIDDEN'))
            guard.start()
            self.addCleanup(guard.stop)
        tmp = tempfile.TemporaryDirectory(prefix='_spool-stop-', dir=Path(__file__).parent)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'spool'

    def snapshot(self):
        # Windows denies a second handle's read of the actively locked byte.
        return {str(p.relative_to(self.root)): (bytes(p.stat().st_size)
                if p.name == 'bridge.lock' else p.read_bytes())
                for p in self.root.rglob('*') if p.is_file()}

    def record(self):
        c = fixture.container()
        data, ns, digest = b.encode_record(fixture.record(), 'nginx', POLICY, fixture.NOW)
        cp = dict(service='nginx', high=ns, counts={str(ns) + ':' + digest: 1})
        return c, data, cp

    def reservation(self, spool):
        c, data, cp = self.record()
        import hashlib
        pending = dict(file='nginx/00000000000000000001.jsonl', offset=0,
                       length=len(data), digest=hashlib.sha256(data).hexdigest(),
                       id=c.id, checkpoint=cp)
        prepared = {**spool.state, 'pending': pending}
        committed = {**spool.state, 'containers': {**spool.state['containers'], c.id: cp}}
        return spool._usage() + len(b._json_bytes(prepared)) + len(b._json_bytes(committed)) + len(data)

    def failed_pending(self, partial=False):
        c, data, cp = self.record()
        def crash(stage):
            if stage == ('prepared' if partial else 'appended'):
                if partial:
                    (self.root/'nginx/00000000000000000001.jsonl').write_bytes(data[:5])
                raise RuntimeError('synthetic crash')
        with b.Spool(self.root, POLICY, fault=crash) as spool:
            spool.bootstrap(b.timestamp(fixture.BASE)[1])
            with self.assertRaises(RuntimeError):
                spool.append(c, data, cp)

    def test_default_budget_preserves_existing_behavior(self):
        with b.Spool(self.root, POLICY) as spool:
            self.assertIsNone(spool.stop_spool_bytes)
            spool.append(*self.record())
            self.assertTrue(spool.state['containers'])

    def test_invalid_budget_rejected_before_filesystem_creation(self):
        for value in [0, -1, True, 1.5, '8388608', POLICY.max_spool_bytes+1]:
            with self.assertRaisesRegex(b.BridgeError, '^SPOOL_STOP_CONFIG_INVALID$'):
                b.Spool(self.root, POLICY, stop_spool_bytes=value)
            self.assertFalse(self.root.exists())

    def test_budget_equal_to_hard_limit_is_allowed(self):
        with b.Spool(self.root, POLICY, stop_spool_bytes=POLICY.max_spool_bytes) as spool:
            self.assertEqual(spool.stop_spool_bytes, POLICY.max_spool_bytes)

    def test_existing_oversized_spool_is_untouched(self):
        self.root.mkdir()
        (self.root/'padding').write_bytes(b'123')
        before = self.snapshot()
        with self.assertRaisesRegex(b.BridgeError, '^SPOOL_STOP_LIMIT$'):
            b.Spool(self.root, POLICY, stop_spool_bytes=2)
        self.assertEqual(self.snapshot(), before)

    def test_lock_marker_counts_as_one_byte(self):
        with b.Spool(self.root, POLICY, stop_spool_bytes=1) as spool:
            self.assertEqual(spool._usage(), 1)
            spool.lock.seek(0)
            self.assertEqual(spool.lock.read(1), b'0')

    def test_lock_growth_rejected_before_marker_write(self):
        self.root.mkdir()
        (self.root/'bridge.lock').touch()
        (self.root/'padding').write_bytes(b'x')
        before = self.snapshot()
        with self.assertRaisesRegex(b.BridgeError, '^SPOOL_STOP_LIMIT$'):
            b.Spool(self.root, POLICY, stop_spool_bytes=1)
        self.assertEqual(self.snapshot(), before)

    def test_existing_lock_is_not_deleted_at_stop(self):
        with b.Spool(self.root, POLICY):
            pass
        before = self.snapshot()
        with b.Spool(self.root, POLICY, stop_spool_bytes=1) as spool:
            with self.assertRaisesRegex(b.BridgeError, '^SPOOL_STOP_LIMIT$'):
                spool.bootstrap(b.timestamp(fixture.BASE)[1])
        self.assertEqual(self.snapshot(), before)

    def test_checkpoint_temporary_copy_exact_eight_mib_boundary(self):
        state = {'version': 1, 'containers': {}}
        with b.Spool(self.root, POLICY, stop_spool_bytes=LIMIT) as spool:
            # Sparse padding is synthetic; quota intentionally counts st_size.
            with (self.root/'padding').open('wb') as f:
                f.truncate(LIMIT-spool._usage()-len(b._json_bytes(state)))
            observed = []
            replace = b.os.replace
            def observe(src, dst):
                observed.append(spool._usage())
                replace(src, dst)
            with patch.object(b.os, 'replace', side_effect=observe):
                spool._save(state)
            self.assertEqual(max(observed), LIMIT)
            self.assertEqual(spool._usage(), LIMIT)
            before = self.snapshot()
            with self.assertRaisesRegex(b.BridgeError, '^SPOOL_STOP_LIMIT$'):
                spool._save(state)
            self.assertEqual(self.snapshot(), before)

    def test_checkpoint_one_byte_over_rejected_without_temporary_file(self):
        state = {'version': 1, 'containers': {}}
        with b.Spool(self.root, POLICY, stop_spool_bytes=LIMIT) as spool:
            with (self.root/'padding').open('wb') as f:
                f.truncate(LIMIT-spool._usage()-len(b._json_bytes(state))+1)
            before = self.snapshot()
            with self.assertRaisesRegex(b.BridgeError, '^SPOOL_STOP_LIMIT$'):
                spool._save(state)
            self.assertEqual(self.snapshot(), before)
            self.assertFalse((self.root/'checkpoint.tmp').exists())

    def test_append_reserves_journal_record_and_commit_before_writing(self):
        with b.Spool(self.root, POLICY) as spool:
            threshold = self.reservation(spool)-1
        before = self.snapshot()
        with b.Spool(self.root, POLICY, stop_spool_bytes=threshold) as spool:
            with self.assertRaisesRegex(b.BridgeError, '^SPOOL_STOP_LIMIT$'):
                spool.append(*self.record())
            self.assertFalse(spool.state.get('pending'))
            self.assertFalse(list(self.root.glob('*/*.jsonl')))
        self.assertEqual(self.snapshot(), before)

    def test_append_at_reservation_boundary_never_exceeds_budget(self):
        with b.Spool(self.root, POLICY) as spool:
            threshold = self.reservation(spool)
        with b.Spool(self.root, POLICY, stop_spool_bytes=threshold) as spool:
            peaks = []
            replace = b.os.replace
            def observe(src, dst):
                peaks.append(spool._usage())
                self.assertLessEqual(spool._usage(), threshold)
                replace(src, dst)
            spool.fault = lambda stage: peaks.append(spool._usage())
            with patch.object(b.os, 'replace', side_effect=observe):
                spool.append(*self.record())
            self.assertLessEqual(max(peaks), threshold)
            self.assertFalse(spool.state.get('pending'))

    def test_bootstrap_not_written_when_budget_cannot_fit_state(self):
        with b.Spool(self.root, POLICY, stop_spool_bytes=1) as spool:
            with self.assertRaisesRegex(b.BridgeError, '^SPOOL_STOP_LIMIT$'):
                spool.bootstrap(b.timestamp(fixture.BASE)[1])
            self.assertNotIn('bootstrap_ns', spool.state)
            self.assertFalse((self.root/'checkpoint.json').exists())

    def test_scan_cursor_not_advanced_on_budget_stop(self):
        with b.Spool(self.root, POLICY) as spool:
            spool.bootstrap(b.timestamp(fixture.BASE)[1])
            threshold = spool._usage()
        before = self.snapshot()
        with b.Spool(self.root, POLICY, stop_spool_bytes=threshold) as spool:
            with self.assertRaisesRegex(b.BridgeError, '^SPOOL_STOP_LIMIT$'):
                spool.complete_scan(fixture.container(), b.timestamp(fixture.BASE)[1]+30*10**9)
            self.assertEqual(spool.state['scans'], {})
        self.assertEqual(self.snapshot(), before)

    def test_partial_recovery_refuses_before_truncation_if_budget_too_small(self):
        self.failed_pending(partial=True)
        before = self.snapshot()
        limit = sum(len(v) for v in before.values())
        with self.assertRaisesRegex(b.BridgeError, '^SPOOL_STOP_LIMIT$'):
            b.Spool(self.root, POLICY, stop_spool_bytes=limit)
        self.assertEqual(self.snapshot(), before)

    def test_complete_pending_recovery_refuses_without_modifying_data(self):
        self.failed_pending()
        before = self.snapshot()
        with self.assertRaisesRegex(b.BridgeError, '^SPOOL_STOP_LIMIT$'):
            b.Spool(self.root, POLICY, stop_spool_bytes=sum(len(v) for v in before.values()))
        self.assertEqual(self.snapshot(), before)

    def test_resume_after_partial_crash_recovers_within_budget(self):
        self.failed_pending(partial=True)
        with b.Spool(self.root, POLICY, stop_spool_bytes=LIMIT) as spool:
            self.assertFalse(spool.state.get('pending'))
            bridge = b.Bridge(fixture.FakeAdapter([fixture.container()], {fixture.container().id: [fixture.record()]}),
                              spool, POLICY, now=lambda: fixture.NOW)
            self.assertEqual(bridge.run_once().accepted, 1)
            self.assertLessEqual(spool._usage(), LIMIT)

    def test_resume_after_soft_stop_has_no_duplicate_and_keeps_bootstrap(self):
        c = fixture.container()
        with b.Spool(self.root, POLICY) as spool:
            spool.bootstrap(b.timestamp(fixture.BASE)[1])
            spool.append(*self.record())
            limit = spool._usage()
        with b.Spool(self.root, POLICY, stop_spool_bytes=limit) as spool:
            adapter = fixture.FakeAdapter([c], {c.id: [fixture.record(), fixture.record('second')]})
            with self.assertRaisesRegex(b.BridgeError, '^SPOOL_STOP_LIMIT$'):
                b.Bridge(adapter, spool, POLICY, now=lambda: fixture.NOW).run_once()
        with b.Spool(self.root, POLICY, stop_spool_bytes=LIMIT) as spool:
            adapter = fixture.FakeAdapter([c], {c.id: [fixture.record(), fixture.record('second')]})
            stats = b.Bridge(adapter, spool, POLICY, now=lambda: fixture.NOW).run_once()
            self.assertEqual((stats.accepted, stats.duplicates), (1, 1))
            self.assertEqual(spool.state['bootstrap_ns'], b.timestamp(fixture.BASE)[1])
        lines = [line for p in self.root.glob('*/*.jsonl') for line in p.read_bytes().splitlines()]
        self.assertEqual(len(lines), 2)

    def test_soft_stop_is_permanent_and_not_retried_by_polling(self):
        import test_live_bridge as live
        class Bridge:
            calls = 0
            def run_once(self):
                self.calls += 1
                raise b.BridgeError('SPOOL_STOP_LIMIT')
        bridge = Bridge()
        with self.assertRaisesRegex(b.BridgeError, '^SPOOL_STOP_LIMIT$'):
            b.polling_loop(bridge, b.PollSettings(max_polls=5), live.Event())
        self.assertEqual(bridge.calls, 1)

    def test_disk_full_before_journal_does_not_advance_state(self):
        with b.Spool(self.root, POLICY, stop_spool_bytes=LIMIT) as spool:
            original = b.os.open
            def no_space(path, *args, **kwargs):
                if Path(path).name == 'checkpoint.tmp':
                    raise OSError(errno.ENOSPC, 'SYNTHETIC_SECRET')
                return original(path, *args, **kwargs)
            before = self.snapshot()
            c = fixture.container()
            with patch.object(b.os, 'open', side_effect=no_space):
                with self.assertRaisesRegex(b.BridgeError, '^BRIDGE_OPERATION_FAILED$'):
                    b.Bridge(fixture.FakeAdapter([c], {c.id: [fixture.record()]}), spool, POLICY,
                             now=lambda: fixture.NOW).run_once()
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(spool.state['containers'], {})

    def test_hard_limit_remains_enforced(self):
        with b.Spool(self.root, POLICY, stop_spool_bytes=LIMIT) as spool:
            with self.assertRaisesRegex(b.BridgeError, '^SPOOL_FULL$'):
                spool._require_capacity(POLICY.max_spool_bytes+1)

    def test_disk_full_after_prepared_journal_is_recoverable(self):
        c = fixture.container()
        with b.Spool(self.root, POLICY, stop_spool_bytes=LIMIT) as spool:
            original = b.os.open
            def no_space(path, *args, **kwargs):
                if Path(path).suffix == '.jsonl':
                    raise OSError(errno.ENOSPC, 'SYNTHETIC_SECRET')
                return original(path, *args, **kwargs)
            with patch.object(b.os, 'open', side_effect=no_space):
                with self.assertRaisesRegex(b.BridgeError, '^BRIDGE_OPERATION_FAILED$'):
                    b.Bridge(fixture.FakeAdapter([c], {c.id: [fixture.record()]}), spool, POLICY,
                             now=lambda: fixture.NOW).run_once()
            self.assertTrue(spool.state.get('pending'))
            self.assertFalse(spool.state['containers'])
            self.assertFalse(list(self.root.glob('*/*.jsonl')))
        with b.Spool(self.root, POLICY, stop_spool_bytes=LIMIT) as spool:
            self.assertFalse(spool.state.get('pending'))
            stats = b.Bridge(fixture.FakeAdapter([c], {c.id: [fixture.record()]}), spool,
                             POLICY, now=lambda: fixture.NOW).run_once()
            self.assertEqual(stats.accepted, 1)
            self.assertLessEqual(spool._usage(), LIMIT)

    def test_segment_rotation_is_included_in_total_budget(self):
        from dataclasses import replace
        policy = replace(POLICY, max_segment_bytes=300, max_line_bytes=300)
        c = fixture.container()
        with b.Spool(self.root, policy, stop_spool_bytes=LIMIT) as spool:
            records = [fixture.record('message-'+str(i)) for i in range(5)]
            stats = b.Bridge(fixture.FakeAdapter([c], {c.id: records}), spool, policy,
                             now=lambda: fixture.NOW).run_once()
            self.assertEqual(stats.accepted, 5)
            self.assertGreater(len(list(self.root.glob('*/*.jsonl'))), 1)
            usage = sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file())
            self.assertEqual(spool._usage(), usage)
            self.assertLessEqual(usage, LIMIT)

    def test_stop_with_no_pending_keeps_all_existing_jsonl_files(self):
        with b.Spool(self.root, POLICY) as spool:
            spool.append(*self.record())
            limit = spool._usage()
        before = self.snapshot()
        with b.Spool(self.root, POLICY, stop_spool_bytes=limit) as spool:
            with self.assertRaisesRegex(b.BridgeError, '^SPOOL_STOP_LIMIT$'):
                spool._save(spool.state)
        self.assertEqual(self.snapshot(), before)

    def test_cli_budget_validation_and_offline_mode(self):
        for value, expected in [('8388608', 0), ('16777216', 0), ('0', 2), ('16777217', 2)]:
            out = io.StringIO()
            with redirect_stdout(out):
                code = b.main(['--dry-run', '--stop-spool-bytes', value])
            self.assertEqual(code, expected)
            self.assertFalse(self.root.exists())

    def test_cli_passes_budget_to_live_runner_without_running_it(self):
        with patch.object(b, '_run_live', return_value=0) as runner:
            result = b.main(['--live', '--spool', str(self.root.resolve()), '--read-gid', '475',
                             '--bootstrap-since', fixture.BASE, '--stop-spool-bytes', str(LIMIT)])
        self.assertEqual(result, 0)
        self.assertEqual(runner.call_args.args[0].stop_spool_bytes, LIMIT)
        self.assertFalse(self.root.exists())

    def test_cli_soft_stop_has_dedicated_exit_code_without_live_io(self):
        for error, expected in [('SPOOL_STOP_LIMIT', 3), ('SPOOL_FULL', 2),
                                ('SPOOL_STOP_CONFIG_INVALID', 2)]:
            with self.subTest(error=error):
                out = io.StringIO()
                with patch.object(b, '_run_live', side_effect=b.BridgeError(error)), redirect_stdout(out):
                    result = b.main(['--live', '--spool', str(self.root.resolve()),
                                     '--read-gid', '475', '--bootstrap-since', fixture.BASE,
                                     '--stop-spool-bytes', str(LIMIT)])
                self.assertEqual(result, expected)
                self.assertEqual(out.getvalue(), 'FAIL ' + error + '\n')
                self.assertFalse(self.root.exists())


if __name__ == '__main__':
    unittest.main()
