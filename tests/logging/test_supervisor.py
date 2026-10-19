"""Offline only: disposable spool, synthetic Python children, no Docker/WSL API.

Linux process integration tests must run unprivileged. No actual Bridge import,
live spool or Docker socket is used. Windows runs portable validation tests.
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    'beautyglow_supervisor', Path(__file__).resolve().parents[2]/'scripts/beautyglow-log-supervisor.py')
s = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = s
SPEC.loader.exec_module(s)

FAKE = r'''
import json,os,pathlib,signal,sys,time,socket
def forbidden(*a, **kw):raise AssertionError('NETWORK_FORBIDDEN')
socket.socket=socket.create_connection=socket.getaddrinfo=forbidden
root=pathlib.Path(sys.argv[1]);scenario=sys.argv[2]
(root/'child.pid').write_text(str(os.getpid()))
def report(progress=True,pending=True,retries=0):
 state=json.loads((root/'checkpoint.json').read_bytes())
 if progress:
  for scan in state['scans'].values():scan['until']=time.time_ns()-1000000000 if not pending else scan['until']+120000000000
 (root/'checkpoint.new').write_text(json.dumps(state));os.replace(root/'checkpoint.new',root/'checkpoint.json')
 print(json.dumps(dict(code='POLL_OK',accepted=1,duplicates=0,retries=retries,rejected_containers=0,catchup_pending=pending,max_lag_seconds=500 if pending else 1)),flush=True)
if scenario=='success':report(pending=False);time.sleep(20)
elif scenario=='error':print('FAIL BRIDGE_OPERATION_FAILED',flush=True);time.sleep(20)
elif scenario=='retry':print('POLL_RETRY',flush=True);time.sleep(20)
elif scenario=='reported_retry':report(retries=1);time.sleep(20)
elif scenario=='nostep':report(progress=False);time.sleep(20)
elif scenario=='spoolstop':print('FAIL SPOOL_STOP_LIMIT',flush=True);sys.exit(3)
elif scenario=='exit3':sys.exit(3)
elif scenario=='exit2':sys.exit(2)
elif scenario=='early0':sys.exit(0)
elif scenario=='stderr':print('SYNTHETIC_SECRET',file=sys.stderr,flush=True);time.sleep(20)
elif scenario=='oversized':print('x'*5000,flush=True);time.sleep(20)
elif scenario=='truncated':print('{',end='',flush=True)
elif scenario=='limit':
 for _ in range(3):report();time.sleep(.15)
 time.sleep(20)
elif scenario=='growth':(root/'padding').write_bytes(b'x'*10000);time.sleep(20)
elif scenario=='kill':signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(20)
elif scenario=='descendant':
 import subprocess
 p=subprocess.Popen([sys.executable,'-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(20)'])
 (root/'descendant.pid').write_text(str(p.pid));time.sleep(.1);report(pending=False);time.sleep(20)
elif scenario=='pending':
 state=json.loads((root/'checkpoint.json').read_bytes());state['pending']={'synthetic':True};(root/'checkpoint.json').write_text(json.dumps(state));report();time.sleep(20)
else:time.sleep(20)
'''


class PortableTests(unittest.TestCase):
    def test_default_limits_and_command(self):
        limits = s.Limits()
        self.assertEqual((limits.polls, limits.seconds, limits.spool_bytes, limits.grace),
                         (300, 900, 8388608, 5))
        cmd = s.bridge_command('python3', s.BRIDGE, s.POLICY, s.SPOOL, limits)
        for option, value in [('--batch-seconds','120'),('--max-polls','300'),
                              ('--poll-interval','1'),('--stop-spool-bytes','8388608'),
                              ('--bootstrap-since',s.BOOTSTRAP),('--policy',str(s.POLICY))]:
            self.assertEqual(cmd[cmd.index(option)+1],value)
        self.assertNotIn('--allow-source-replacement',cmd)
        self.assertEqual(s.POLICY.as_posix(),'/opt/beautyglow-bridge/bridge-policy.json')
        self.assertFalse(any('/mnt/d' in arg for arg in cmd))

    def test_child_interpreter_is_actually_isolated(self):
        # Execute only a synthetic Python snippet with the actual command flags.
        # Never execute Bridge or connect to any socket.
        cmd = s.bridge_command(sys.executable, s.BRIDGE, s.POLICY, s.SPOOL, s.Limits())
        flags = cmd[1:cmd.index(str(s.BRIDGE))]
        environment = dict(os.environ, PYTHONPATH='/synthetic-untrusted-path',
                           PYTHONINSPECT='1')
        result = subprocess.run([sys.executable, *flags, '-c',
            'import sys,json;print(json.dumps(dict(isolated=sys.flags.isolated,'
            'ignore_environment=sys.flags.ignore_environment,'
            'no_user_site=sys.flags.no_user_site,dont_write_bytecode=sys.dont_write_bytecode,'
            'unsafe_path="/synthetic-untrusted-path" in sys.path)))'],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            env=environment, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, '')
        self.assertEqual(json.loads(result.stdout), dict(isolated=1,
            ignore_environment=1, no_user_site=1, dont_write_bytecode=True,
            unsafe_path=False))

    def test_protected_policy_metadata_and_hash(self):
        raw=(Path(__file__).resolve().parents[2]/'config/alloy/bridge-policy.json').read_bytes()
        directory=SimpleNamespace(st_uid=0,st_gid=474,st_mode=0o40750)
        file=SimpleNamespace(st_uid=0,st_gid=474,st_mode=0o100440)
        def metadata(path):return directory if path==s.POLICY.parent else file
        with patch.object(s.Path,'is_symlink',return_value=False), \
                patch.object(s.Path,'stat',autospec=True,side_effect=metadata), \
                patch.object(s.Path,'read_bytes',return_value=raw):
            s.protected_policy(s.POLICY)
            file.st_uid=474
            with self.assertRaisesRegex(s.Stop,'POLICY_FILE_INVALID'):s.protected_policy(s.POLICY)
            file.st_uid=0;file.st_mode=0o100640
            with self.assertRaisesRegex(s.Stop,'POLICY_FILE_INVALID'):s.protected_policy(s.POLICY)
            file.st_mode=0o100440;directory.st_uid=474
            with self.assertRaisesRegex(s.Stop,'POLICY_FILE_INVALID'):s.protected_policy(s.POLICY)
            directory.st_uid=0
            with patch.object(s.Path,'read_bytes',return_value=raw+b' '):
                with self.assertRaisesRegex(s.Stop,'POLICY_HASH_INVALID'):s.protected_policy(s.POLICY)

    def test_policy_external_path_and_symlink_refused(self):
        with self.assertRaisesRegex(s.Stop,'POLICY_FILE_INVALID'):
            s.protected_policy(Path('/mnt/d/BeautyGlow-Website/config/alloy/bridge-policy.json'))
        with patch.object(s.Path,'is_symlink',return_value=True):
            with self.assertRaisesRegex(s.Stop,'POLICY_FILE_INVALID'):s.protected_policy(s.POLICY)

    def test_cannot_raise_limits(self):
        for kw in [dict(polls=301),dict(polls=0),dict(seconds=901),dict(seconds=0),
                   dict(spool_bytes=8388609),dict(grace=6)]:
            with self.subTest(kw=kw), self.assertRaises(ValueError):s.Limits(**kw)

    def test_dry_run_has_no_io(self):
        with patch.object(s,'live_preflight',side_effect=AssertionError), \
                patch.object(s,'supervise',side_effect=AssertionError), \
                patch('socket.socket',side_effect=AssertionError), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(s.main(['--dry-run','--bridge-sha256','0'*64]),0)

    def test_root_live_launcher_is_refused_before_file_access(self):
        with patch.object(s.sys,'platform','linux'), \
                patch.object(s.os,'geteuid',create=True,return_value=0), \
                patch.object(s.os,'getegid',create=True,return_value=0), \
                patch.object(s.Path,'stat',side_effect=AssertionError):
            with self.assertRaisesRegex(s.Stop,'IDENTITY_INVALID'):
                s.live_preflight(s.BRIDGE,'0'*64,s.POLICY,s.Limits())


@unittest.skipUnless(sys.platform=='linux','Linux process/session integration')
class ProcessTests(unittest.TestCase):
    def setUp(self):
        # Parent and fake children all forbid network; only synthetic Popen allowed.
        for name in ['socket.socket','socket.create_connection','socket.getaddrinfo']:
            guard=patch(name,side_effect=AssertionError('NETWORK_FORBIDDEN'));guard.start();self.addCleanup(guard.stop)
        tmp=tempfile.TemporaryDirectory(prefix='_supervisor-',dir=Path(__file__).parent)
        self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.script=self.root/'fake.py';self.script.write_text(FAKE)
        self.state={'version':1,'bootstrap_ns':s.BOOT_NS,'containers':{},'scans':{
            str(i)*64:{'service':svc,'until':s.BOOT_NS} for i,svc in enumerate(sorted(s.SERVICES),1)}}
        (self.root/'checkpoint.json').write_text(json.dumps(self.state))

    def run_fake(self,scenario,limits=None,cancelled=lambda:False):
        result=s.supervise([sys.executable,'-B',str(self.script),str(self.root),scenario],
                           self.root,limits or s.Limits(polls=3,seconds=2,grace=.15),cancelled)
        if (self.root/'child.pid').exists():
            self.assertFalse(s.group_alive(int((self.root/'child.pid').read_text())))
        return result

    def test_success_stops_after_first_poll(self):
        r=self.run_fake('success');self.assertEqual(r['reason'],'CAUGHT_UP');self.assertEqual(r['polls'],1)

    def test_bridge_fail_and_no_raw_output(self):
        out=io.StringIO()
        with contextlib.redirect_stdout(out):r=self.run_fake('error')
        self.assertEqual(r['reason'],'BRIDGE_ERROR_OR_OUTPUT_INVALID');self.assertEqual(out.getvalue(),'')

    def test_retry_stops_immediately(self):
        self.assertEqual(self.run_fake('retry')['reason'],'BRIDGE_ERROR_OR_OUTPUT_INVALID')

    def test_reported_retry_stops(self):
        self.assertEqual(self.run_fake('reported_retry')['reason'],'BRIDGE_RETRY_OR_REJECTION')

    def test_checkpoint_no_progress(self):
        self.assertEqual(self.run_fake('nostep')['reason'],'CHECKPOINT_NOT_ADVANCING')

    def test_spool_limit_diagnostic(self):
        self.assertEqual(self.run_fake('spoolstop')['reason'],'SPOOL_STOP_LIMIT')

    def test_exit_codes(self):
        for scenario,reason in [('exit3','SPOOL_STOP_LIMIT'),('exit2','BRIDGE_FAILED'),('early0','BRIDGE_EXIT_UNEXPECTED')]:
            with self.subTest(scenario=scenario):self.assertEqual(self.run_fake(scenario)['reason'],reason)

    def test_stderr_is_not_echoed(self):
        self.assertEqual(self.run_fake('stderr')['reason'],'BRIDGE_STDERR')

    def test_output_size_bounded(self):
        self.assertEqual(self.run_fake('oversized')['reason'],'BRIDGE_OUTPUT_INVALID')

    def test_truncated_output(self):
        self.assertEqual(self.run_fake('truncated')['reason'],'BRIDGE_OUTPUT_TRUNCATED')

    def test_poll_cap_enforced_even_if_child_hangs(self):
        r=self.run_fake('limit');self.assertEqual(r['reason'],'POLL_LIMIT');self.assertEqual(r['polls'],3)

    def test_timeout(self):
        self.assertEqual(self.run_fake('sleep',s.Limits(seconds=.15,grace=.1))['reason'],'TIME_LIMIT')

    def test_term_ignored_is_killed(self):
        r=self.run_fake('kill',s.Limits(seconds=.25,grace=.1))
        self.assertEqual(r['reason'],'TIME_LIMIT');self.assertTrue(r['kill_required'])

    def test_cleanup_inspection_failure_still_kills_and_reaps(self):
        p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)'],start_new_session=True)
        try:
            with patch.object(s,'group_alive',side_effect=PermissionError):
                with self.assertRaises(PermissionError):s.finish(p,.1)
            self.assertIsNotNone(p.returncode)
        finally:
            if p.poll() is None:os.killpg(p.pid,signal.SIGKILL);p.wait()

    def test_descendant_is_cleaned(self):
        r=self.run_fake('descendant');self.assertEqual(r['reason'],'CAUGHT_UP');self.assertTrue(r['kill_required'])

    def test_spool_growth_stops(self):
        self.assertEqual(self.run_fake('growth',s.Limits(spool_bytes=8000,seconds=2,grace=.1))['reason'],'SPOOL_STOP_LIMIT')

    def test_pending_after_poll_is_not_recovered(self):
        r=self.run_fake('pending');self.assertEqual(r['reason'],'CHECKPOINT_CHANGED_OR_PENDING')
        self.assertTrue(json.loads((self.root/'checkpoint.json').read_bytes())['pending'])

    def test_cancel_before_start_does_not_spawn(self):
        with patch.object(s.subprocess,'Popen',side_effect=AssertionError):
            r=self.run_fake('sleep',cancelled=lambda:True)
        self.assertEqual(r['reason'],'INTERRUPTED')

    def test_cancel_while_running(self):
        start=time.monotonic()
        r=self.run_fake('sleep',cancelled=lambda:time.monotonic()-start>.15)
        self.assertEqual(r['reason'],'INTERRUPTED')

    def test_initial_pending_refused_without_writes(self):
        self.state['pending']={'synthetic':True};path=self.root/'checkpoint.json';path.write_text(json.dumps(self.state));before=path.read_bytes()
        with self.assertRaisesRegex(s.Stop,'PENDING_REVIEW_REQUIRED'):self.run_fake('sleep')
        self.assertEqual(path.read_bytes(),before);self.assertFalse((self.root/'child.pid').exists())

    def test_bootstrap_change_refused(self):
        self.state['bootstrap_ns']+=1;(self.root/'checkpoint.json').write_text(json.dumps(self.state))
        with self.assertRaisesRegex(s.Stop,'BOOTSTRAP_OR_STATE_INVALID'):self.run_fake('sleep')

    def test_initial_spool_full_does_not_spawn(self):
        before=(self.root/'checkpoint.json').read_bytes()
        with self.assertRaisesRegex(s.Stop,'SPOOL_STOP_LIMIT'):
            self.run_fake('sleep',s.Limits(spool_bytes=1))
        self.assertFalse((self.root/'child.pid').exists())
        self.assertEqual(before,(self.root/'checkpoint.json').read_bytes())

    def test_symlink_refused(self):
        (self.root/'unsafe').symlink_to(self.root/'checkpoint.json')
        with self.assertRaisesRegex(s.Stop,'SPOOL_PATH_UNSAFE'):self.run_fake('sleep')

    def test_kernel_stops_child_if_supervisor_is_killed(self):
        driver=self.root/'kill-driver.py'
        driver.write_text('import importlib.util,sys\n'
            'spec=importlib.util.spec_from_file_location("sv",sys.argv[1]);s=importlib.util.module_from_spec(spec);sys.modules["sv"]=s;spec.loader.exec_module(s)\n'
            's.supervise([sys.executable,"-B",sys.argv[2],sys.argv[3],"sleep"],s.Path(sys.argv[3]),s.Limits(seconds=5,grace=.1))\n')
        p=subprocess.Popen([sys.executable,'-B',str(driver),str(Path(s.__file__)),str(self.script),str(self.root)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        child=None
        try:
            end=time.monotonic()+2
            while not (self.root/'child.pid').exists() and time.monotonic()<end:time.sleep(.01)
            self.assertTrue((self.root/'child.pid').exists());child=int((self.root/'child.pid').read_text())
            p.kill();p.wait(timeout=3)
            end=time.monotonic()+2
            while s.group_alive(child) and time.monotonic()<end:time.sleep(.01)
            self.assertFalse(s.group_alive(child))
        finally:
            if p.poll() is None:p.kill();p.wait()
            if child is not None and s.group_alive(child):os.killpg(child,signal.SIGKILL)

    def test_actual_sigterm_to_supervisor(self):
        driver=self.root/'driver.py'
        driver.write_text('import importlib.util,sys,signal,json\n'
            'spec=importlib.util.spec_from_file_location("sv",sys.argv[1]);s=importlib.util.module_from_spec(spec);sys.modules["sv"]=s;spec.loader.exec_module(s)\n'
            'cancel=[False]\n'
            'def handler(*a):cancel[0]=True\n'
            'signal.signal(signal.SIGTERM,handler);signal.signal(signal.SIGINT,handler)\n'
            'r=s.supervise([sys.executable,"-B",sys.argv[2],sys.argv[3],"sleep"],s.Path(sys.argv[3]),s.Limits(seconds=3,grace=.1),lambda:cancel[0]);print(json.dumps(r))\n')
        for sig in [signal.SIGTERM,signal.SIGINT]:
            with self.subTest(sig=sig):
                (self.root/'child.pid').unlink(missing_ok=True)
                p=subprocess.Popen([sys.executable,'-B',str(driver),str(Path(s.__file__)),str(self.script),str(self.root)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                try:
                    deadline=time.monotonic()+2
                    while not (self.root/'child.pid').exists() and time.monotonic()<deadline:time.sleep(.01)
                    self.assertTrue((self.root/'child.pid').exists());p.send_signal(sig)
                    out,err=p.communicate(timeout=5);self.assertEqual(p.returncode,0);self.assertFalse(err)
                    self.assertEqual(json.loads(out)['reason'],'INTERRUPTED')
                    self.assertFalse(s.group_alive(int((self.root/'child.pid').read_text())))
                finally:
                    if p.poll() is None:p.kill();p.wait()


if __name__=='__main__':unittest.main()
