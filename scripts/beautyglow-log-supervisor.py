"""Bounded Linux supervisor. Import/dry-run never starts Bridge or opens a socket.

Only the unchanged Bridge writes spool/state. The supervisor reads metadata and
controls one trusted Bridge process group; it is not a sandbox for hostile code
which deliberately escapes its session. No restart or automatic second run.
"""
from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import time

BOOTSTRAP = "2026-10-19T10:13:25Z"
BOOT_NS = 1792404805000000000
SERVICES = {"nginx", "wordpress", "mysql"}
SPOOL = Path("/var/lib/beautyglow-logs")
POLICY = Path("/opt/beautyglow-bridge/bridge-policy.json")
POLICY_SHA256 = "fb0c75df818c03d6ef18d313eea8c17fe11756d768474946fc4b5ead6ac8478a"
BRIDGE = Path("/opt/beautyglow-bridge/beautyglow-log-bridge.py")


class Stop(Exception):
    pass


@dataclass(frozen=True)
class Limits:
    polls: int = 300
    seconds: float = 900
    spool_bytes: int = 8388608
    grace: float = 5

    def __post_init__(self):
        if (type(self.polls) is not int or not 1 <= self.polls <= 300
                or type(self.spool_bytes) is not int or not 0 < self.spool_bytes <= 8388608
                or not 0 < self.seconds <= 900 or not 0 < self.grace <= 5):
            raise ValueError("SUPERVISOR_CONFIG_INVALID")


def snapshot(root):
    """No Spool constructor: it would recover/write state even on open."""
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise Stop("SPOOL_PATH_UNSAFE")
    cp = root / "checkpoint.json"
    if cp.is_symlink() or cp.stat().st_size > 1048576:
        raise Stop("CHECKPOINT_INVALID")
    state = json.loads(cp.read_bytes())
    if state.get("version") != 1 or state.get("bootstrap_ns") != BOOT_NS:
        raise Stop("BOOTSTRAP_OR_STATE_INVALID")
    scans = state.get("scans", {})
    if len(scans) != 3 or {v.get("service") for v in scans.values()} != SERVICES:
        raise Stop("CHECKPOINT_INVALID")
    cursors = {}
    for ident, scan in scans.items():
        until = scan.get("until")
        if type(until) is not int or until < BOOT_NS:
            raise Stop("CHECKPOINT_INVALID")
        cursors[ident] = until
    return cursors, usage_bytes(root), bool(state.get("pending"))


def usage_bytes(root):
    usage = 0
    for p in root.rglob("*"):
        if p.is_symlink():
            raise Stop("SPOOL_PATH_UNSAFE")
        try:
            info = p.stat()
        except FileNotFoundError:
            continue  # An atomic checkpoint.tmp rename is normal during a poll.
        if stat.S_ISREG(info.st_mode):
            usage += info.st_size
    return usage


def bridge_command(python, bridge, policy, root, limits):
    return [python, "-I", "-B", str(bridge), "--live", "--policy", str(policy),
            "--spool", str(root), "--read-gid", "475", "--bootstrap-since", BOOTSTRAP,
            "--batch-seconds", "120", "--max-polls", str(limits.polls),
            "--poll-interval", "1", "--stop-spool-bytes", str(limits.spool_bytes)]


def group_alive(pgid):
    # Linux zombies have no executable or effective socket access. Ignore them.
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            fields = (p / "stat").read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) == pgid and fields[0] != "Z":
                return True
        except FileNotFoundError:
            continue
    return False


def finish(proc, grace, clock=time.monotonic):
    """TERM the whole group, wait at most grace, then KILL survivors and reap."""
    def send(sig):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass
    try:
        send(signal.SIGTERM)
        end = clock() + grace
        while group_alive(proc.pid) and clock() < end:
            proc.poll()
            time.sleep(0.02)
        killed = group_alive(proc.pid)
        if killed:
            send(signal.SIGKILL)
        proc.wait(timeout=5)
        end = clock() + 5
        while group_alive(proc.pid) and clock() < end:
            time.sleep(0.02)
        if group_alive(proc.pid):
            raise Stop("CLEANUP_FAILED")
        return killed
    except BaseException:
        # Inspection failures must not bypass the kill/reap attempt.
        send(signal.SIGKILL)
        proc.wait(timeout=5)
        raise


def child_setup(parent):
    # This supervisor is single-threaded. Kernel kills the direct Bridge child
    # even if supervisor receives SIGKILL/crashes before its finally block.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        os._exit(127)
    if os.getppid() != parent:  # Parent may die before prctl was installed.
        os.kill(os.getpid(), signal.SIGKILL)


def supervise(command, root, limits=Limits(), cancelled=lambda: False):
    """Reusable only for offline synthetic-process tests; CLI fixes live paths."""
    if sys.platform != "linux":
        raise Stop("LINUX_REQUIRED")
    previous, usage, pending = snapshot(root)
    if pending:
        raise Stop("PENDING_REVIEW_REQUIRED")
    if usage >= limits.spool_bytes:
        raise Stop("SPOOL_STOP_LIMIT")
    start = time.monotonic()
    proc = None
    polls = accepted = 0
    reason = "SUPERVISOR_FAILED"
    killed = False
    selector = selectors.DefaultSelector()
    buffers = {"out": b"", "err": b""}
    finished_line = False
    try:
        if cancelled():
            raise Stop("INTERRUPTED")
        parent = os.getpid()
        proc = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True, close_fds=True,
                                preexec_fn=lambda: child_setup(parent))
        for name, pipe in [("out", proc.stdout), ("err", proc.stderr)]:
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, name)
        while True:
            if cancelled():
                raise Stop("INTERRUPTED")
            if time.monotonic() - start >= limits.seconds:
                raise Stop("TIME_LIMIT")
            # Size-only sampling between reports; never parse a checkpoint.tmp.
            usage = usage_bytes(root)
            if usage >= limits.spool_bytes:
                raise Stop("SPOOL_STOP_LIMIT")
            for key, _ in selector.select(timeout=0.05):
                chunk = os.read(key.fileobj.fileno(), 4096)
                name = key.data
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if name == "err":
                    raise Stop("BRIDGE_STDERR")  # Never echo raw stderr.
                buffers[name] += chunk
                while b"\n" in buffers[name]:
                    line, buffers[name] = buffers[name].split(b"\n", 1)
                    if len(line) > 4096:
                        raise Stop("BRIDGE_OUTPUT_INVALID")
                    if line == b"FAIL SPOOL_STOP_LIMIT":
                        raise Stop("SPOOL_STOP_LIMIT")
                    if line in (b"BRIDGE_POLL_LIMIT_REACHED", b"BRIDGE_STOPPED"):
                        if polls != limits.polls or line != b"BRIDGE_POLL_LIMIT_REACHED":
                            raise Stop("BRIDGE_EXIT_UNEXPECTED")
                        finished_line = True
                        continue
                    if finished_line:
                        raise Stop("BRIDGE_OUTPUT_INVALID")
                    try:
                        row = json.loads(line)
                        expected = {"code", "accepted", "duplicates", "retries",
                                    "rejected_containers", "catchup_pending", "max_lag_seconds"}
                        if set(row) != expected or row["code"] != "POLL_OK":
                            raise ValueError()
                        for field in expected - {"code", "catchup_pending"}:
                            if type(row[field]) is not int or row[field] < 0:
                                raise ValueError()
                        if type(row["catchup_pending"]) is not bool:
                            raise ValueError()
                    except (ValueError, TypeError):
                        raise Stop("BRIDGE_ERROR_OR_OUTPUT_INVALID") from None
                    polls += 1
                    if polls > limits.polls:
                        raise Stop("POLL_LIMIT_PROTOCOL_ERROR")
                    if row["retries"] or row["rejected_containers"]:
                        raise Stop("BRIDGE_RETRY_OR_REJECTION")
                    current, usage, pending = snapshot(root)
                    if pending or set(current) != set(previous):
                        raise Stop("CHECKPOINT_CHANGED_OR_PENDING")
                    if any(current[k] <= previous[k] for k in current):
                        raise Stop("CHECKPOINT_NOT_ADVANCING")
                    if any(v > time.time_ns() for v in current.values()):
                        raise Stop("CHECKPOINT_IN_FUTURE")
                    previous = current
                    accepted += row["accepted"]
                    if not row["catchup_pending"]:
                        lag = max(0, (time.time_ns() - min(current.values())) // 10**9)
                        if row["max_lag_seconds"] >= 120 or lag >= 120:
                            raise Stop("CATCHUP_REPORT_INCONSISTENT")
                        raise Stop("CAUGHT_UP")
                    if polls == limits.polls:
                        raise Stop("POLL_LIMIT")
                if len(buffers[name]) > 4096:
                    raise Stop("BRIDGE_OUTPUT_INVALID")
            rc = proc.poll()
            if rc is not None and not selector.get_map():
                if any(buffers.values()):
                    raise Stop("BRIDGE_OUTPUT_TRUNCATED")
                if rc == 3:
                    raise Stop("SPOOL_STOP_LIMIT")
                if rc != 0:
                    raise Stop("BRIDGE_FAILED")
                raise Stop("POLL_LIMIT" if finished_line else "BRIDGE_EXIT_UNEXPECTED")
    except Stop as exc:
        reason = str(exc)
    except Exception:
        reason = "SUPERVISOR_FAILED"
    finally:
        try:
            if proc is not None:
                killed = finish(proc, limits.grace)
        except Exception:
            reason = "CLEANUP_FAILED"
        finally:
            selector.close()
            if proc is not None:
                proc.stdout.close()
                proc.stderr.close()
    # Do not recover a pending append or delete any partial data here.
    return {"reason": reason, "polls": polls, "accepted": accepted,
            "bridge_exit": proc.returncode if proc is not None else None,
            "kill_required": killed, "elapsed_seconds": round(time.monotonic() - start, 3)}


def protected_policy(policy):
    if policy != POLICY or any(p.is_symlink() for p in (policy, *policy.parents)):
        raise Stop("POLICY_FILE_INVALID")
    folder = policy.parent.stat()
    info = policy.stat()
    if ((folder.st_uid, folder.st_gid, stat.S_IMODE(folder.st_mode)) != (0, 474, 0o750)
            or not stat.S_ISREG(info.st_mode)
            or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 474, 0o440)):
        raise Stop("POLICY_FILE_INVALID")
    raw = policy.read_bytes()
    if hashlib.sha256(raw).hexdigest() != POLICY_SHA256:
        raise Stop("POLICY_HASH_INVALID")
    data = json.loads(raw)
    if (data.get("project") != "beautyglow-website" or set(data.get("services", [])) != SERVICES
            or data.get("replay_window_seconds") != 120
            or data.get("max_spool_bytes") != 16777216):
        raise Stop("POLICY_INVALID")


def live_preflight(bridge, expected_hash, policy, limits):
    if sys.platform != "linux" or (os.geteuid(), os.getegid()) != (474, 474):
        raise Stop("IDENTITY_INVALID")
    s = bridge.stat()
    if (bridge != BRIDGE or bridge.is_symlink() or s.st_uid != 474
            or stat.S_IMODE(s.st_mode) != 0o500
            or hashlib.sha256(bridge.read_bytes()).hexdigest() != expected_hash):
        raise Stop("BRIDGE_FILE_INVALID")
    info = os.stat("/run/docker.sock")
    if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o660
            or info.st_gid not in os.getgroups() or 475 not in os.getgroups()):
        raise Stop("GROUP_OR_SOCKET_INVALID")
    protected_policy(policy)
    # A second Bridge or supervisor with socket access must not run concurrently.
    for p in Path("/proc").iterdir():
        if not p.name.isdigit() or int(p.name) == os.getpid():
            continue
        try:
            status = (p / "status").read_text().splitlines()
            uid = next(x for x in status if x.startswith("Uid:")).split()[2]
            groups = next(x for x in status if x.startswith("Groups:")).split()[1:]
            if uid == "474" and (str(info.st_gid) in groups
                                 or b"beautyglow-log-bridge.py" in (p / "cmdline").read_bytes()):
                raise Stop("OTHER_BRIDGE_OR_SOCKET_HOLDER")
        except FileNotFoundError:
            continue
    import fcntl
    with (SPOOL / "bridge.lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(lock, fcntl.LOCK_UN)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--bridge-sha256", required=True)
    args = parser.parse_args(argv)
    limits = Limits()
    if args.dry_run:
        print("DRY_RUN; no Bridge, socket, checkpoint or spool accessed")
        return 0
    cancelled = [False]
    previous = {}
    def stop(_sig, _frame):
        cancelled[0] = True
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous[sig] = signal.signal(sig, stop)
        live_preflight(BRIDGE, args.bridge_sha256, POLICY, limits)
        result = supervise(bridge_command(sys.executable, BRIDGE, POLICY, SPOOL, limits),
                           SPOOL, limits, lambda: cancelled[0])
        print(json.dumps(result, sort_keys=True))
        return {"CAUGHT_UP": 0, "SPOOL_STOP_LIMIT": 3, "TIME_LIMIT": 4,
                "POLL_LIMIT": 5, "INTERRUPTED": 130}.get(result["reason"], 2)
    except Exception:
        print('FAIL SUPERVISOR_PREFLIGHT; no raw diagnostics emitted')
        return 2
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
