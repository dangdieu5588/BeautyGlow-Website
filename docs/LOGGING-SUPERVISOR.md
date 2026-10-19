# Supervisor catch-up BeautyGlow

Supervisor đã được kiểm thử offline và chạy một đợt được phê duyệt: 234 poll,
348 giây, CAUGHT_UP, exit 0; JSONL từ 212 lên 3.977, không còn tiến trình giữ
nhóm Docker tạm thời. Cursor kết thúc 2026-10-19T18:16:40Z. Không có dịch vụ nền.
Các lệnh live dưới đây vẫn cần phê duyệt cho mỗi lần chạy mới.
Tests dùng state tạm và Python child giả, không socket hoặc Bridge thật.
Xem [runbook](LOGGING-OPERATIONS.md) cho trạng thái và giới hạn nghiệm thu.

## Contract

`scripts/beautyglow-log-supervisor.py` wraps the existing Bridge without importing
or modifying it. Its CLI fixes the approved live paths and identity:

- Bridge `/opt/beautyglow-bridge/beautyglow-log-bridge.py`, owner UID 474, mode 0500;
  SHA-256 must match the operator-supplied hash.
- Explicit protected policy `/opt/beautyglow-bridge/bridge-policy.json`, root:474,
  mode 0440; deployment directory root:474 mode 0750. Preflight rejects writable
  ownership/modes, symlinked paths or a hash differing from the approved bytes
  `fb0c75df818c03d6ef18d313eea8c17fe11756d768474946fc4b5ead6ac8478a`.
  Live supervisor/Bridge no longer read policy from the Windows workspace.
- Spool `/var/lib/beautyglow-logs`, existing bootstrap `2026-10-19T10:13:25Z`.
- Linux effective UID/GID 474:474, reader group 475 and actual socket group.
  The supervisor does not grant privileges, run sudo, change groups or connect
  to Docker itself. A separately approved launcher supplies the groups.
- At most 300 polls, batch 120 seconds, interval 1 second, work deadline 900
  monotonic seconds, logical-file-byte stop limit 8,388,608. No policy increase.

Preflight reads metadata, checks for another Bridge/socket holder under UID 474,
and tests the existing lock using flock on a read-only handle. It never constructs
the Bridge Spool class: that constructor can perform recovery writes. Existing
pending append, missing/invalid state or an already-full spool refuses startup.
Readiness/container baseline/source availability must also pass the deployment
preflight immediately before any approved live run; this script does not replace
that operational check.

## Stop and cleanup

After each validated POLL_OK, the supervisor reads the atomic checkpoint and
requires all three source cursors to advance, with identities/bootstrap retained
and no pending append. It stops on false catchup_pending (confirmed lag <120s),
300 reports, retry/rejection, protocol/error/stderr, checkpoint failure, spool
limit, deadline or SIGTERM/SIGINT. Output buffers are bounded to 4096 bytes per
line; raw child diagnostics are never echoed. Spool size is sampled between
reports as well. The unchanged Bridge's reservation before writes, not supervisor
sampling, enforces the preventive byte limit, including temporary checkpoint copies.

Bridge starts in a new session/process group. Cleanup sends TERM to that group,
allows 5 seconds, KILLs survivors and reaps the direct child. A further bounded
5-second check confirms no executing process remains in that group; zombies are
excluded because they cannot execute or hold socket privileges. Work time is
limited to 15 minutes; cleanup may extend beyond this deadline. Linux
PR_SET_PDEATHSIG=SIGKILL also kills the direct Bridge if supervisor dies unexpectedly.

This manages a trusted, unchanged Bridge, which does not spawn descendants. It is
not containment for malicious descendants deliberately escaping their session,
and cannot guarantee cleanup while the kernel is hung in uninterruptible I/O.
Cleanup failures are reported as failures, never success. The supervisor itself
holds its supplied groups until it exits. There is no daemon, restart or second run.

Stop is asynchronous: Bridge may have started work after a report before TERM
arrives. Bridge's existing signal/transaction handling preserves resumability;
supervisor never rewinds/deletes data or repairs pending state. Final checkpoint,
counts and socket-holder scan must be checked after a live run. A forced kill may
leave a pending journal; review it before authorizing recovery/resume.

`catchup_pending=false` means lag below one batch (120 seconds), not zero lag or
proof of source completeness. Docker rotation may already have removed unread
logs. Supervisor's accepted count covers observed successful poll reports only;
partially completed polls must be reconciled against final JSONL counts.

## Exit codes

| Exit | Meaning |
|---|---|
| 0 | CAUGHT_UP, lag below 120 seconds |
| 2 | Error/preflight/checkpoint/protocol/cleanup failure |
| 3 | Logical spool stop limit |
| 4 | 900-second work deadline |
| 5 | 300-poll cap, backlog may remain |
| 130 | Operator interruption, cleanup attempted |

## Validation and proposed live launch

Offline suite (unprivileged Linux):

```sh
python3 -B -m unittest discover -s tests/logging -p 'test_*.py' -q
```

Validated on 2026-10-20 (UTC+7), after protecting the policy path: Linux 238/238
tests initially passed, including 31 supervisor tests. After adding isolated
Python flags to the Bridge child, Linux 239/239 tests passed, including 32
supervisor tests. A real synthetic child confirms isolated=1, environment ignored,
user site disabled and bytecode disabled. Python syntax,
trailing whitespace and `git diff --check` passed. Bridge source SHA-256 remains
`f461bd7c6e9e56c8f3ae90c9fa3f6b3282dd810b956bc7f5bc98277f7e840814`.

Protected deployment completed without live execution: supervisor root:474 0550,
SHA-256 `06d437d8c0bba8e8e05f4df4cf0f626b395a85fe5927da13baa5d025fa0b29db`;
policy root:474 0440, hash as above; directory root:474 0750. The existing Bridge
file remained unchanged. Protected-policy validation and isolated dry-run passed
as UID/GID 474:474 with empty supplementary groups. No Docker socket or real spool
was accessed by deployment validation.

Tests cover successful near-catch-up, no-progress/pending/bootstrap failures,
full/growing spool and Bridge exit 3, malformed/truncated/oversized output,
Bridge error/retry/stderr, poll/deadline caps, ignored TERM, process-group cleanup,
actual SIGTERM/SIGINT and supervisor SIGKILL/parent-death handling. Windows skips
Linux process tests. Socket creation/connect/DNS in parent tests and the main
synthetic Bridge are guarded. No live security claims are inferred from fake tests.

The following is a proposal only, NOT executed. Reverify the deployed Bridge hash,
supervisor source, policy, checkpoint, free space, no other holder, all services'
readiness and actual Docker socket GID before approval. GID 986 below is the last
verified value, not a permanent assumption. Launch from an existing Ubuntu session:

```sh
sudo /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent \
  /usr/bin/setpriv --reuid=474 --regid=474 --groups=475,986 --no-new-privs \
  /usr/bin/python3 -I -B /opt/beautyglow-bridge/beautyglow-log-supervisor.py \
  --run --bridge-sha256 f461bd7c6e9e56c8f3ae90c9fa3f6b3282dd810b956bc7f5bc98277f7e840814
```

Do not add an outer SIGKILL-only timeout. Use SIGINT/SIGTERM on the supervisor
for normal operator cancellation. This proposed launcher temporarily gives the
supervisor and Bridge socket-group access; no account/group/socket permissions
are changed. It requires separate explicit authorization for the real catch-up.

Post-run: confirm zero remaining temporary socket-group holders, checkpoint and
bootstrap, pending state, spool usage, per-service JSONL deltas, readiness and
unchanged container IDs/StartedAt/RestartCount. Reconcile JSONL/Loki counts over
the same event-time range, allowing natural idle flush for old backfill, then
verify the same query in Grafana. No automatic retry or data deletion.
