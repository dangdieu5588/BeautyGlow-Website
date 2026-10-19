# BeautyGlow Alloy / Bridge — trạng thái Giai đoạn 5

Đã triển khai và nghiệm thu bộ dữ liệu 3.977 dòng ngày 20/10/2026 (UTC+7).
Bridge chỉ chạy theo đợt được duyệt, không có dịch vụ nền. Alloy UID 473:473,
nhóm đọc 475, chỉ mount đọc ba thư mục service; không có Docker socket.

Tài liệu vận hành hiện hành: [Logging runbook](../../docs/LOGGING-OPERATIONS.md).
Giới hạn supervisor: [Supervisor](../../docs/LOGGING-SUPERVISOR.md).
Các mô tả bên dưới được giữ làm hồ sơ thiết kế/test giai đoạn 5.2; các câu
“chưa chạy/chưa xác minh” chỉ áp dụng thời điểm đó, không thay thế runbook.

## Hồ sơ thiết kế ban đầu (lịch sử)

## Architecture and boundaries

    Docker Logs API -> approved Ubuntu adapter -> Bridge -> JSONL spool
      -> loki.source.file -> loki.process -> loki.write -> Loki -> Grafana

`scripts/beautyglow-log-bridge.py` contains the bridge core, `DockerAdapter`
protocol and a `DockerAPIAdapter` implementing API request/response logic with
an injected `HTTPTransport`. A concrete `UnixHTTPTransport`, bounded polling loop
and explicit live CLI are implemented. Tests supply fake sockets/transports only.
No real Docker connection or service installation has occurred. Offline checks:

    python -B scripts/beautyglow-log-bridge.py --check-policy config/alloy/bridge-policy.json
    python -B scripts/beautyglow-log-bridge.py --dry-run
    python -B -m unittest discover -s tests/logging -p "test_*.py" -v

Run from the repository root. Tests create and clean up only temporary directories
under `tests/logging/`. `-B` avoids bytecode files. No real logs or credentials are
test inputs. Standard-library Python 3.10+ is sufficient; no install is required.

The adapter requests running containers filtered by project, independently checks
the service allowlist, validates inspect identity/state/TTY and projects metadata
to ID plus the two Compose labels. Approved labels are immutable. Log reads need
a discovered, allowlisted full ID and repeat inspect validation immediately before
reading. Failed discovery revokes all previous IDs; a failed per-ID revalidation
revokes that ID until successful discovery. No Env, mounts, arbitrary labels or
credentials are returned, cached as metadata, written to spool or printed.

Important: a genuine Docker inspect response can contain Env and other sensitive
fields before projection. Dropping fields is NOT an API permission boundary and
does not prevent the host process/transport from seeing that response in memory.
All inspection responses used in tests are fake. Any real adapter/transport use
must be explicitly approved with this remaining privilege in view; do not claim
the current code enforces daemon-side resource isolation. Alloy gets no socket.

## Docker API adapter contract — verified with fake transport only

`HTTPTransport.request(method, path, timeout=...)` is dependency-injected, with no
host/socket default. `HTTPResponse` supplies status, headers, bounded `read(n)` and
`close()`. Tests block socket creation, urllib requests and subprocess creation.
`UnixHTTPTransport` uses the standard-library HTTP parser for Content-Length and
chunked responses, including trailers. It enforces a 16 KiB initial-header limit,
rejects interim/upgrade responses, conflicting/ambiguous framing and successful
unframed bodies, detects premature EOF, and closes resources on every exit path.
Its AF_UNIX connection has no DNS/TCP fallback, redirects or debug logging. Both
per-operation timeouts and a total deadline bound reads, including slow trickles.
The adapter separately decodes Docker multiplex framing. Tests use fake sockets
with real HTTP parsing; actual Ubuntu socket/Engine compatibility is unverified.

The only API operations produced are GET list-containers with a project/running
filter, GET inspect for prefiltered IDs, and GET logs for validated IDs. API prefix
is explicitly pinned to `/v1.51` by default. No version negotiation, automatic
fallback, redirects, API writes or arbitrary resource path is available. This is
a tested request contract, NOT confirmation that API v1.51 works on the real Engine.
Reference: https://docs.docker.com/reference/api/engine/version/v1.51/

Log requests use stdout=true, stderr=true, timestamps=true, tail=all, follow=false
and precise inclusive `since`/`until` in live mode. Each call reads a finite
snapshot. The live loop polls bounded windows rather than following indefinitely.
Bridge retry creates a new discovery/read attempt using durable checkpoints.

The decoder checks the 8-byte header (stream byte, three zero reserved bytes,
big-endian length), handles arbitrary read/frame splitting, combines partial
UTF-8 lines per stream and validates RFC3339Nano timestamps. It keeps stdout and
stderr buffers separate. Multiple physical log lines become individual records;
it does not reconstruct application stack traces into one event. TTY is rejected.
Original core tests for an already-grouped multiline Record remain unchanged.

EOF inside a frame/header or an unterminated line is retryable; no incomplete
record is committed. This deliberately also rejects legitimate final messages
without a newline. EOF at a clean frame/line boundary completes the snapshot.
The transport must distinguish premature HTTP EOF/reset from a complete HTTP body;
otherwise a disconnect exactly at a clean Docker frame boundary is undetectable.

Default adapter limits (independent from unchanged bridge-policy.json):

| Limit | Value |
|---|---|
| Transport timeout | 5 seconds, required on connect and every blocking read |
| Response elapsed budget | 10 seconds |
| Metadata response body | 256 KiB |
| Discovery candidates | 64 |
| Docker frame payload | 128 KiB |
| Snapshot bytes including headers | 4 MiB |
| Frames / records per snapshot | 8192 / 8192 |
| Maximum read request | 8 KiB |
| Buffered log line | Policy input size plus bounded timestamp overhead |

The adapter checks elapsed time before/after reads; the Unix transport also applies
remaining deadlines to blocking socket operations. Actual OS scheduling, socket
permissions and shutdown latency need Ubuntu verification. Quota violations fail
closed; even one dense catch-up window can exceed its byte/record quota and stop.

Timeouts, resets, incomplete streams and HTTP 404/408/429/500/502/503/504 are
retryable within the existing three-attempt bridge bound. Other HTTP errors,
schema/UTF-8 errors, invalid frame headers, oversize data and scope changes stop.
Error response bodies are never read, redirects are not followed, and response
headers/body/error strings never enter diagnostics. Responses are closed on
completion, errors and explicit generator close. The bridge explicitly closes
the iterator even if a spool write fails; other callers must also close a partially
consumed iterator. Fixed diagnostic codes suppress raw exception traces.

All 101 preexisting tests (45 core and 56 adapter) were retained byte-for-byte.
Slice 5.2.4 adds 55 tests: total 156 synthetic tests. Coverage includes HTTP
framing/deadlines/EOF/cleanup, multiplexed frames, nanosecond timestamps,
scope/identity, checkpoint replay, bootstrap/catch-up, ID replacement, shutdown,
polling/backoff, offline CLI and mocked POSIX permissions. Network, subprocess and
Docker calls are blocked in the new suite. No real logs, daemon/socket or external
spool are used. Windows tests do not verify actual Linux permissions or mounts.

## Scope and record format

The code independently checks the exact project `beautyglow-website`, and exact
services `nginx`, `wordpress`, `mysql`. It rejects unknown/missing metadata,
non-running containers, invalid 64-character IDs and TTY containers (TTY merges
streams). Multiple candidates for one service fail closed. Validated labels are
copied before reading. No container names, Env or command lines are persisted.

Each physical UTF-8 JSONL line has exactly these fields:

    {"timestamp":"2026-10-19T08:00:00.123456789Z","project":"beautyglow-website","service":"nginx","stream":"stdout","message":"synthetic message"}

Offset timestamps normalize to UTC without dropping nanoseconds. Invalid or
future timestamps fail with a fixed diagnostic code. Multiline messages remain
one JSONL record, with escaped newlines. No stack-trace correlation is claimed.

## Redaction and errors

Before spool writes, the bridge masks nested JSON sensitive keys, common
password/token/API-key assignments, Authorization/Proxy-Authorization, Cookie/
Set-Cookie, Basic/Bearer values and URL userinfo. It removes the entire query
string on URL/request paths, including encoded query keys. This intentionally
removes non-sensitive query parameters too. Redaction precedes truncation.

Redaction is heuristic, not a guarantee for arbitrary free text, custom encodings,
unknown secret formats or secrets split across separate records. Production
approval requires synthetic cases resembling the approved real log formats;
never print real secrets to test redaction. Exceptions expose fixed allowlisted
codes, not adapter errors, record contents, credentials or sensitive paths.
No raw messages are written to checkpoint or diagnostics; checkpoint stores
timestamps, service identity and SHA-256 fingerprints of redacted records.

## Checkpoint, retry and limits

Only one writer can hold the spool lock (fcntl on Linux, msvcrt on Windows).
For each record the writer persists a pending journal, appends and fsyncs JSONL,
then atomically replaces checkpoint state. Linux also fsyncs directories.
Recovery commits a complete matching append, or truncates only an interrupted
uncommitted tail before source replay. Conflicts/corrupt state stop processing.
No prior complete log segment is deleted or overwritten by recovery.

Per-container checkpoints retain timestamp/fingerprint occurrence counts over a
120-second inclusive replay window. This preserves identical-record multiplicity
in controlled full replays, supports bounded out-of-order records and separates
container IDs. Old IDs remain until the configured state limit; no silent eviction.
The adapter must preserve multiplicity over the requested replay window. Source
rotation that already removed logs, altered replay sets, late logs beyond the
window, power loss/filesystem behavior, and restarts of the full Alloy/Loki path
are NOT covered by an exactly-once guarantee. Missing source data cannot be rebuilt.

Retryable adapter failures retry up to three attempts with bounded backoff;
permanent errors, quota errors and invalid data stop without advancing that record.
After partial-source failures, already durable records are deduplicated on replay.

Live bootstrap is an explicit RFC3339Nano cut-off, persisted once. A new cut-off
must be no older than 168 hours; restarting preserves the original cut-off and
scan progress even when it becomes older. A conflicting cut-off or populated
legacy state without bootstrap fails closed; there is no automatic reset/migration.
Live scans advance by at most 30 seconds by default, replay up to 120 seconds and
commit a scan cursor only after a complete, validated snapshot. Empty snapshots
also advance durably. Bootstrap deliberately excludes older logs: approve the
chosen coverage boundary. Diagnostics report `catchup_pending` and
`max_lag_seconds` for active sources, never raw log contents.

Missing required services retry within the configured bound. Replacement of an
observed ID pauses by default with `SOURCE_REPLACED_REVIEW_REQUIRED`. An operator
may explicitly approve `--allow-source-replacement`: old checkpoints remain and
new IDs get independent state. This cannot recover unread logs from a removed
container. Running-only discovery cannot automatically drain a stopped source.

Polling defaults to one pilot poll, five-second intervals, at most three failed
polls and exponential backoff capped at 30 seconds. Each poll also has the policy's
three-attempt adapter bound. Permanent errors stop immediately. `--max-polls 0`
explicitly enables continuous polling; retries remain bounded on consecutive
failures. SIGINT/SIGTERM set a stop event, waits are interruptible and socket reads
remain timeout-bounded. Completed appends stay durable; interrupted pending writes
are recovered on restart. A bounded pilot ends with `BRIDGE_POLL_LIMIT_REACHED`,
which does not mean all backlog has been collected.

Policy defaults:

| Limit | Value |
|---|---|
| Input message | 64 KiB; larger inputs rejected |
| Serialized JSONL line | 8 KiB; redacted message truncated with a marker |
| Segment | 256 KiB; numbered append-only files per service |
| Total spool files/state/atomic copy | 16 MiB logical file bytes |
| One state file | 1 MiB |
| Retained container checkpoints | 64 |
| Replay keys per container window | 4096 |
| Replay window | 120 seconds |

The quota includes the temporary checkpoint copy. It does not represent filesystem
allocated blocks, directory overhead, filesystem snapshots or external files added
concurrently. A full spool stops collection; it never purges old segments.
There is no acknowledgement-aware cleanup yet. Operators must not delete segments
merely because Alloy has read them; that does not prove delivery to Loki.
No unlimited in-memory buffer or retry loop is provided by this bridge core.

## Ubuntu identities and spool access — designed, not runtime-verified

The live CLI accepts only `/run/docker.sock` and `/var/lib/beautyglow-logs`.
It requires Linux, a root-owned real socket without world-write access, and a
positive reader GID different from root and the Docker socket group. A nonroot
bridge writer must belong to that reader group; it separately needs approved
Docker socket access. Docker group access is effectively privileged: application
allowlists do not restrict other code running under that host identity.

The bridge writer must own the spool and differ from the proposed Alloy UID 473.
UID 473 remains a candidate until the pinned image's actual identity is verified;
revise the guard before deployment if that verified UID differs. Use a dedicated
reader group, with only Alloy and the bridge writer as intended members:

| Path | Owner / group | Mode |
|---|---|---|
| Spool root | bridge writer | 0700 |
| nginx/, wordpress/, mysql/ | bridge writer / dedicated reader GID | 2750 |
| Numbered JSONL segments | bridge writer / dedicated reader GID | 0640 |
| checkpoint, temporary state, lock | bridge writer | 0600 |

State and lock files are never shared. Unexpected files/ownership or unsafe
permissions fail closed. This code does not create OS accounts/groups. Because
the root is private, Alloy must receive **three individual service-directory
read-only bind mounts**, at `/var/log/beautyglow/{nginx,wordpress,mysql}`; mounting
the private root would block traversal or expose state. Docker's host-side mount
resolution and numeric reader GID must be verified explicitly in slice 5.3.
Do not mount the Docker socket or checkpoint into Alloy. Give Alloy separate
writable storage for positions. No world-read or 777 workaround is acceptable.

Before real collection, test that the bridge can write; Alloy's actual UID plus
reader GID can read JSONL but cannot write, read checkpoint/lock, or access Docker;
and an unrelated UID cannot read JSONL. These checks have NOT been run here.

## Future Ubuntu pilot command — approval required, not executed

After approving identities, permissions, source coverage, API compatibility and
external spool creation, run from the repository root with Python 3.10+:

```sh
sudo -u beautyglow-bridge python3 -B scripts/beautyglow-log-bridge.py \
  --live --spool /var/lib/beautyglow-logs \
  --read-gid 'APPROVED_NUMERIC_GID' \
  --bootstrap-since 'APPROVED_RFC3339NANO_TIMESTAMP' \
  --max-polls 1
```

The account and placeholder values above are not provisioned. Substitute only
approved real values; placeholders intentionally fail validation. Preserve the
same bootstrap value on resume. First inspect fixed result codes/counters and
backlog status; do not enable continuous mode automatically after one pilot.
No systemd unit has been created or installed. Supervisor lifecycle, restart
policy and service hardening need a separately approved deployment step.

## Prepared Alloy configuration

`config.alloy` discovers only three explicit service JSONL directories under the
container path `/var/log/beautyglow`. It does NOT read checkpoint/lock files.
JSON processing extracts timestamp/project/service/stream/message, drops malformed
JSON and disallowed project/service/stream labels, sets RFC3339Nano time, emits
the redacted message and drops the rotating filename label.
`action_on_failure = "skip"` skips replacing a timestamp, not dropping the entry;
the bridge is responsible for validating timestamps. Additional malformed-spool
schema tests with the actual Alloy binary remain a gate before activation.

There is no Docker discovery/source in this chosen architecture. The source uses
built-in `file_match` globbing supported in the proposed Alloy version. Reference:
https://github.com/grafana/alloy/blob/v1.20.1/docs/sources/reference/components/loki/loki.source.file.md

Future Alloy storage.path must be a separate persistent volume for positions.
No write WAL/durable retry guarantee is implied by `loki.write` defaults. Its
delivery/backpressure behavior and outage tests must be verified before activation.
No new Grafana datasource or changes to existing 18 panels are included here.

## Recovery evidence and remaining loss/duplication risks

Controlled full replay tests preserve every complete synthetic record exactly
once in the local spool after a disconnect/timeout, including identical-record
multiplicity. Recreating the adapter/bridge resumes its on-disk checkpoint.
A 404 triggers fresh discovery; live mode then pauses on an observed ID change
unless replacement has explicitly been approved. Checkpoints are independent.
Completed records preceding a bad frame remain durable and are
deduplicated when a later valid replay is explicitly supplied.

This is NOT an end-to-end exactly-once claim. Docker rotation/deletion can remove
unread records; a changed replay set can make identical timestamp/message
occurrences ambiguous. The bounded replay window cannot recover arbitrarily late
entries. IDs are different sources: equal content on a new ID is intentionally
preserved, even if an application repeats an earlier message after replacement.
A newly stopped source may disappear from running-only discovery. Real polling
gaps, delivery to Loki, write backpressure and cleanup acknowledgement remain
untested. The 16 MiB pilot spool is not an unattended continuous-service capacity
guarantee; acknowledgement-aware cleanup remains a deployment design gate.

## Gates for the next approved slice

1. Approve real socket privileges, API v1.51 compatibility checks, fixed bootstrap
   coverage, replacement policy and backlog limits. The transport and CLI exist,
   but no real Docker access is authorized by passing synthetic tests.
2. Validate Alloy/Loki using their pinned binaries only after separate permission
   to obtain/run tools or images. YAML parsing alone is not Loki validation.
3. Approve the Linux spool and actual writer/Alloy identities. Test the permission
   matrix and individual directory mounts above before real logs. This slice
   creates no external path. Do not weaken permissions to make collection work.
4. Design bounded spool cleanup with delivery evidence, supervisor lifecycle and
   delivery/outage tests. Request approval before host service installation,
   Compose edits, external filesystem writes or live Docker access.

The prepared files do not authorize deployment. Existing containers, WordPress
products/media, Prometheus, Grafana, credentials and Lab resources remain unchanged.
