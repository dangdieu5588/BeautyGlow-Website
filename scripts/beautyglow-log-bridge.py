"""BeautyGlow bridge with explicit live CLI and an inert default/dry-run path.

The Unix socket transport is opened only when explicitly requested. Tests inject
fake sockets/transports; importing this module never performs Docker I/O.
Only the JSONL spool is an Alloy input; checkpoint/lock files are private state.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import http.client
import io
import json
import math
import os
from pathlib import Path
import re
import signal
import socket
import stat
import sys
import threading
import time
from types import MappingProxyType
from typing import Callable, Iterable, Mapping, Protocol
from urllib.parse import parse_qs, urlencode, urlsplit


PROJECT = "beautyglow-website"
SERVICES = frozenset(("nginx", "wordpress", "mysql"))
REDACTED = "[REDACTED]"


class BridgeError(Exception):
    """Expose fixed codes and, for HTTP failures, validated four-field context."""

    CODES = frozenset((
        "POLICY_SCOPE_INVALID", "POLICY_LIMIT_INVALID", "POLICY_RETRY_INVALID", "POLICY_INVALID",
        "TIMESTAMP_INVALID", "RECORD_INVALID", "RECORD_ENCODING_INVALID", "INPUT_TOO_LARGE",
        "TIMESTAMP_IN_FUTURE", "LINE_LIMIT_TOO_SMALL", "SPOOL_PATH_UNSAFE", "STATE_LIMIT",
        "STATE_INVALID", "SPOOL_OPEN_FAILED", "SPOOL_FULL", "STATE_PATH_INVALID",
        "RECOVERY_CONFLICT", "RECOVERY_REQUIRED", "CONTAINER_STATE_LIMIT", "SPOOL_CONCURRENT_CHANGE",
        "CONTAINER_AMBIGUOUS", "ADAPTER_RETRIES_EXHAUSTED", "BRIDGE_OPERATION_FAILED",
        "CONTAINER_IDENTITY_CHANGED", "RECORD_OUTSIDE_REPLAY_WINDOW", "REPLAY_WINDOW_LIMIT",
        "ADAPTER_CONFIG_INVALID", "ADAPTER_SCOPE_DENIED", "ADAPTER_HTTP_ERROR",
        "ADAPTER_RESPONSE_INVALID", "ADAPTER_METADATA_INVALID", "ADAPTER_METADATA_CHANGED",
        "ADAPTER_METADATA_LIMIT", "ADAPTER_FRAME_INVALID", "ADAPTER_FRAME_LIMIT",
        "ADAPTER_INPUT_LIMIT", "ADAPTER_STREAM_LIMIT", "ADAPTER_RECORD_LIMIT",
        "ADAPTER_ENCODING_INVALID", "ADAPTER_TRANSPORT_FAILED", "ADAPTER_TTY_UNSUPPORTED",
        "TRANSPORT_CONFIG_INVALID", "TRANSPORT_SCOPE_DENIED", "TRANSPORT_PROTOCOL_INVALID",
        "TRANSPORT_BUSY", "SPOOL_PERMISSION_INVALID", "BOOTSTRAP_INVALID",
        "BOOTSTRAP_CONFLICT", "BOOTSTRAP_MIGRATION_REQUIRED", "SCAN_STATE_INVALID",
        "SOURCE_REPLACED_REVIEW_REQUIRED", "ADAPTER_RANGE_INVALID", "RUN_CONFIG_INVALID",
        "POLL_RETRIES_EXHAUSTED", "CLI_ARGUMENT_INVALID", "LIVE_PLATFORM_REQUIRED",
        "LIVE_SOCKET_INVALID", "LIVE_GROUP_INVALID", "LIVE_IDENTITY_INVALID",
        "SOURCE_SET_INCOMPLETE",
        "SPOOL_STOP_CONFIG_INVALID", "SPOOL_STOP_LIMIT",
    ))

    def __init__(self, code, *, http_diagnostic=None):
        super().__init__(code if isinstance(code, str) and code in self.CODES else "BRIDGE_OPERATION_FAILED")
        self.http_diagnostic = None
        if str(self) == "ADAPTER_HTTP_ERROR" and isinstance(http_diagnostic, Mapping):
            # Revalidate on propagation. Never retain a raw path/query/response.
            endpoint = http_diagnostic.get("endpoint")
            status = http_diagnostic.get("status")
            if (set(http_diagnostic) == {"method", "endpoint", "status", "code"}
                    and http_diagnostic.get("method") == "GET"
                    and http_diagnostic.get("code") == "ADAPTER_HTTP_ERROR"
                    and isinstance(endpoint, str)
                    and (endpoint == "/invalid" or re.fullmatch(
                        r"/v1\.[0-9]{1,2}/containers/(?:json|\{id\}/(?:json|logs))", endpoint))
                    and type(status) is int and 100 <= status <= 599):
                self.http_diagnostic = MappingProxyType(dict(http_diagnostic))


def http_error_diagnostic(path, status):
    """Normalize an allowlisted route; no query or container identity survives."""
    endpoint = "/invalid"
    if (isinstance(path, str) and path.isascii() and len(path) <= 4096
            and not any(c in path for c in "\r\n\0#")):
        route = path.partition("?")[0]
        if re.fullmatch(r"/v1\.[0-9]{1,2}/containers/json", route):
            endpoint = route
        else:
            match = re.fullmatch(r"(/v1\.[0-9]{1,2}/containers/)[0-9a-f]{64}(/(?:json|logs))", route)
            if match:
                endpoint = match[1] + "{id}" + match[2]
    return {"method": "GET", "endpoint": endpoint, "status": status,
            "code": "ADAPTER_HTTP_ERROR"}


class RetryableAdapterError(Exception):
    """Transport failure; its potentially sensitive message is never reported."""


class ShutdownRequested(BaseException):
    """Control flow like KeyboardInterrupt; bypasses error/credential formatting."""


@dataclass(frozen=True)
class Policy:
    schema_version: int = 1
    project: str = PROJECT
    services: tuple[str, ...] = ("nginx", "wordpress", "mysql")
    max_input_bytes: int = 65536
    max_line_bytes: int = 8192
    max_segment_bytes: int = 262144
    max_spool_bytes: int = 16777216
    max_state_bytes: int = 1048576
    max_checkpoint_containers: int = 64
    max_window_keys: int = 4096
    replay_window_seconds: int = 120
    max_future_skew_seconds: int = 300
    retry_attempts: int = 3
    retry_base_seconds: float = 0.25
    retry_max_seconds: float = 2.0

    def __post_init__(self):
        if self.schema_version != 1 or self.project != PROJECT:
            raise BridgeError("POLICY_SCOPE_INVALID")
        if len(self.services) != 3 or frozenset(self.services) != SERVICES:
            raise BridgeError("POLICY_SCOPE_INVALID")
        ints = [v for k, v in vars(self).items() if k not in {
            "project", "services", "retry_base_seconds", "retry_max_seconds"}]
        if any(type(v) is not int or v <= 0 for v in ints):
            raise BridgeError("POLICY_LIMIT_INVALID")
        if not (256 <= self.max_line_bytes <= self.max_segment_bytes
                < self.max_spool_bytes):
            raise BridgeError("POLICY_LIMIT_INVALID")
        if self.max_state_bytes >= self.max_spool_bytes or self.retry_attempts > 10:
            raise BridgeError("POLICY_LIMIT_INVALID")
        retries = (self.retry_base_seconds, self.retry_max_seconds)
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in retries):
            raise BridgeError("POLICY_RETRY_INVALID")
        if not (0 < self.retry_base_seconds <= self.retry_max_seconds <= 60):
            raise BridgeError("POLICY_RETRY_INVALID")

    @classmethod
    def load(cls, path: Path):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["services"] = tuple(data["services"])
            return cls(**data)
        except Exception:
            raise BridgeError("POLICY_INVALID") from None


@dataclass(frozen=True)
class Container:
    id: str
    labels: Mapping[str, str]
    running: bool = True
    tty: bool = False


@dataclass(frozen=True)
class Record:
    timestamp: str
    stream: str
    message: str


class DockerAdapter(Protocol):
    """Docker Logs API boundary; no network implementation in this slice.

    discover MUST request running containers filtered by project, return only
    id/Compose labels/running/TTY, and not expose Env, mounts or credentials.
    Core independently validates metadata before opening a log reader.
    read_logs MUST decode Docker multiplex frames into stdout/stderr records,
    retain RFC3339Nano timestamps and use an INCLUSIVE since boundary. It must
    replay the same per-fingerprint multiplicity across reconnects. Records may
    be out of order within the bounded replay window. Live transport framing,
    timeouts, API versions and reconnect fidelity still require verification.
    """

    def discover(self, project: str, services: tuple[str, ...]) -> Iterable[Container]: ...

    def read_logs(self, container_id: str, since: str | None) -> Iterable[Record]: ...


class HTTPResponse(Protocol):
    """HTTP entity body: already dechunked; read(n) must return at most n bytes.

    A future transport MUST enforce the supplied timeout on connect AND reads,
    raise on an incomplete HTTP body, never follow redirects, and close reliably.
    The transport must not log headers, bodies, exceptions or request credentials.
    """

    status: int
    headers: Mapping[str, str]

    def read(self, size: int) -> bytes: ...

    def close(self) -> None: ...


class HTTPTransport(Protocol):
    def request(self, method: str, path: str, *, timeout: float) -> HTTPResponse: ...


class _SocketFile(io.RawIOBase):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.header = bytearray()
        self.headers_done = False

    def readable(self):
        return True

    def readinto(self, buffer):
        self.owner.budget()
        count = self.owner.raw.recv_into(buffer)
        self.owner.check()
        if not self.headers_done and count:
            self.header.extend(memoryview(buffer)[:count])
            end = self.header.find(b"\r\n\r\n")
            if (end < 0 and len(self.header) > 16384) or end > 16384:
                raise BridgeError("TRANSPORT_PROTOCOL_INVALID")
            if end >= 0:
                first = self.header.split(b"\r\n", 1)[0]
                # GET requires no interim response/upgrade protocol.
                if not re.fullmatch(rb"HTTP/1\.[01] [2-5][0-9]{2}(?: [^\r\n]*)?", first):
                    raise BridgeError("TRANSPORT_PROTOCOL_INVALID")
                self.headers_done = True
                self.header.clear()
        return count

    def close(self):
        if not self.closed:
            self.owner.files -= 1
            self.owner.finish_close()
        super().close()


class _BudgetSocket:
    """Per-recv deadline prevents a trickling peer defeating a read timeout."""

    def __init__(self, raw, timeout, deadline, clock, stop):
        self.raw, self.timeout, self.deadline = raw, timeout, deadline
        self.clock, self.stop = clock, stop
        self.files, self.owner_closed, self.physically_closed = 0, False, False

    def check(self):
        if self.stop():
            raise ShutdownRequested()
        if self.clock() >= self.deadline:
            raise TimeoutError("TRANSPORT_TIMEOUT")

    def budget(self):
        self.check()
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise TimeoutError("TRANSPORT_TIMEOUT")
        self.raw.settimeout(min(self.timeout, remaining))

    def sendall(self, data):
        self.budget()
        self.raw.sendall(data)
        self.check()

    def makefile(self, mode):
        if mode != "rb":
            raise BridgeError("TRANSPORT_PROTOCOL_INVALID")
        self.files += 1
        return io.BufferedReader(_SocketFile(self), buffer_size=8192)

    def finish_close(self):
        if self.owner_closed and self.files == 0 and not self.physically_closed:
            self.physically_closed = True
            try:
                self.raw.close()
            except Exception:
                pass

    def close(self):
        # HTTPConnection may close its socket while HTTPResponse still owns a file.
        self.owner_closed = True
        self.finish_close()


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout, deadline, clock, stop, factory):
        super().__init__("localhost", timeout=timeout)
        self.path, self.deadline = path, deadline
        self.clock, self.stop, self.factory = clock, stop, factory
        self.set_debuglevel(0)

    def connect(self):
        raw = self.factory(socket.AF_UNIX, socket.SOCK_STREAM)
        wrapped = _BudgetSocket(raw, self.timeout, self.deadline, self.clock, self.stop)
        try:
            wrapped.budget()
            raw.connect(self.path)
            wrapped.check()
            self.sock = wrapped
        except BaseException:
            wrapped.close()
            raise


class _UnixResponse:
    def __init__(self, response, connection, owner):
        self.response, self.connection, self.owner = response, connection, owner
        self.status, self.closed, self.total = response.status, False, 0
        pairs = response.getheaders()
        self.headers = {}
        for name, value in pairs:
            key = name.lower()
            if key in ("content-type", "content-length", "transfer-encoding"):
                if key in self.headers:
                    raise BridgeError("TRANSPORT_PROTOCOL_INVALID")
                self.headers[key] = value
        self.remaining = None
        if self.status == 200:
            transfer = self.headers.get("transfer-encoding")
            length = self.headers.get("content-length")
            if transfer is not None:
                if transfer.lower() != "chunked" or length is not None:
                    raise BridgeError("TRANSPORT_PROTOCOL_INVALID")
            elif length is not None and re.fullmatch(r"[0-9]{1,10}", length):
                self.remaining = int(length)
                if self.remaining > owner.max_body_bytes:
                    raise BridgeError("TRANSPORT_PROTOCOL_INVALID")
            else:
                # Close-delimited EOF cannot distinguish truncation from success.
                raise BridgeError("TRANSPORT_PROTOCOL_INVALID")

    def read(self, size):
        if self.closed or type(size) is not int or not 0 < size <= 65536:
            raise BridgeError("TRANSPORT_PROTOCOL_INVALID")
        try:
            self.owner.check()
            data = self.response.read(size)
            self.owner.check()
            if self.remaining is not None:
                self.remaining -= len(data)
                if self.remaining < 0 or (not data and self.remaining):
                    raise ConnectionError("TRANSPORT_EOF")
            self.total += len(data)
            if self.total > self.owner.max_body_bytes:
                raise BridgeError("TRANSPORT_PROTOCOL_INVALID")
            return data
        except ShutdownRequested:
            self.close()
            raise
        except (TimeoutError, ConnectionError, http.client.IncompleteRead,
                http.client.RemoteDisconnected, OSError):
            self.close()
            raise ConnectionError("TRANSPORT_INTERRUPTED") from None
        except Exception:
            self.close()
            raise BridgeError("TRANSPORT_PROTOCOL_INVALID") from None

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.response.close()
            finally:
                self.connection.close()
                self.owner.active = None


class UnixHTTPTransport:
    """No I/O in constructor; request() opens AF_UNIX only, never TCP or redirects."""

    def __init__(self, socket_path: str, *, socket_factory=None, clock=time.monotonic,
                 stop=lambda: False, max_response_seconds=10.0, max_body_bytes=4194304):
        if (not isinstance(socket_path, str) or not socket_path.startswith("/")
                or "\0" in socket_path or len(socket_path.encode()) > 107
                or type(max_response_seconds) not in (int, float)
                or not math.isfinite(max_response_seconds) or not 0 < max_response_seconds <= 60
                or type(max_body_bytes) is not int or max_body_bytes <= 0):
            raise BridgeError("TRANSPORT_CONFIG_INVALID")
        self.path, self.factory = socket_path, socket_factory
        self.clock, self.stop = clock, stop
        self.max_response_seconds, self.max_body_bytes = max_response_seconds, max_body_bytes
        self.active, self.deadline = None, 0.0

    def check(self):
        if self.stop():
            raise ShutdownRequested()
        if self.clock() >= self.deadline:
            raise TimeoutError("TRANSPORT_TIMEOUT")

    def request(self, method, path, *, timeout):
        if self.active is not None:
            raise BridgeError("TRANSPORT_BUSY")
        if (method != "GET" or not isinstance(path, str) or not path.isascii()
                or len(path) > 4096 or any(c in path for c in "\r\n\0#")):
            raise BridgeError("TRANSPORT_SCOPE_DENIED")
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc or not re.fullmatch(
                r"/v1\.[0-9]{1,2}/containers/(?:json|[0-9a-f]{64}/(?:json|logs))", parsed.path):
            raise BridgeError("TRANSPORT_SCOPE_DENIED")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise BridgeError("TRANSPORT_CONFIG_INVALID")
        try:
            params = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        except Exception:
            raise BridgeError("TRANSPORT_SCOPE_DENIED") from None
        if any(len(v) != 1 for v in params.values()):
            raise BridgeError("TRANSPORT_SCOPE_DENIED")
        if parsed.path.endswith("/containers/json"):
            try:
                allowed = {"label": ["com.docker.compose.project=" + PROJECT], "status": ["running"]}
                valid = (set(params) == {"all", "filters"} and params["all"] == ["false"]
                         and json.loads(params["filters"][0]) == allowed)
            except Exception:
                valid = False
            if not valid:
                raise BridgeError("TRANSPORT_SCOPE_DENIED")
        elif parsed.path.endswith("/logs"):
            fixed = {"stdout": ["true"], "stderr": ["true"], "timestamps": ["true"],
                     "follow": ["false"], "tail": ["all"]}
            if (not set(fixed) <= set(params) <= set(fixed) | {"since", "until"}
                    or any(params.get(k) != v for k, v in fixed.items())):
                raise BridgeError("TRANSPORT_SCOPE_DENIED")
            for key in ("since", "until"):
                if key in params:
                    validate_unix_timestamp(params[key][0])
        elif params:
            raise BridgeError("TRANSPORT_SCOPE_DENIED")
        self.deadline = self.clock() + self.max_response_seconds
        self.check()
        connection = _UnixConnection(self.path, timeout, self.deadline, self.clock,
                                     self.stop, self.factory or socket.socket)
        response = None
        try:
            connection.request("GET", path, headers={"Connection": "close", "Accept": "*/*"})
            response = connection.getresponse()
            wrapped = _UnixResponse(response, connection, self)
            self.active = wrapped
            return wrapped
        except BaseException as exc:
            if response is not None:
                response.close()
            connection.close()
            if isinstance(exc, ShutdownRequested):
                raise
            if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
                raise ConnectionError("TRANSPORT_INTERRUPTED") from None
            raise BridgeError("TRANSPORT_PROTOCOL_INVALID") from None

    def close(self):
        if self.active is not None:
            self.active.close()


@dataclass(frozen=True)
class AdapterLimits:
    timeout_seconds: float = 5.0
    max_stream_seconds: float = 10.0
    max_metadata_bytes: int = 262144
    max_candidates: int = 64
    max_frame_bytes: int = 131072
    max_stream_bytes: int = 4194304
    max_frames: int = 8192
    max_records: int = 8192
    read_chunk_bytes: int = 8192

    def __post_init__(self):
        for name, value in vars(self).items():
            if name.endswith("seconds"):
                if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 60:
                    raise BridgeError("ADAPTER_CONFIG_INVALID")
            elif type(value) is not int or value <= 0:
                raise BridgeError("ADAPTER_CONFIG_INVALID")
        if not self.read_chunk_bytes <= self.max_frame_bytes <= self.max_stream_bytes:
            raise BridgeError("ADAPTER_CONFIG_INVALID")


class DockerAPIAdapter:
    """Read-only API logic, usable only with an explicitly supplied transport.

    Uses bounded follow=false snapshots; reconnect/discovery is owned by Bridge.
    No API negotiation/fallback: incompatible pinned versions fail closed.
    Runtime daemon compatibility and a real transport remain unverified.
    """

    def __init__(self, transport: HTTPTransport, policy: Policy,
                 limits: AdapterLimits | None = None, api_version: str = "1.51",
                 clock: Callable[[], float] = time.monotonic):
        if transport is None or not callable(getattr(transport, "request", None)):
            raise BridgeError("ADAPTER_CONFIG_INVALID")
        if not isinstance(api_version, str) or not re.fullmatch(r"1\.[0-9]{1,2}", api_version):
            raise BridgeError("ADAPTER_CONFIG_INVALID")
        self._transport, self.policy = transport, policy
        self.limits, self._prefix, self._clock = limits or AdapterLimits(), "/v" + api_version, clock
        self._approved: dict[str, Container] = {}

    @staticmethod
    def _close(response):
        try:
            response.close()
        except Exception:
            pass  # Never surface an exception containing a response/header value.

    def _open(self, path, expected_type):
        response = None
        try:
            response = self._transport.request("GET", self._prefix + path,
                                               timeout=self.limits.timeout_seconds)
            if type(response.status) is not int:
                raise BridgeError("ADAPTER_RESPONSE_INVALID")
            if response.status in (404, 408, 429, 500, 502, 503, 504):
                raise RetryableAdapterError("ADAPTER_RETRYABLE_HTTP")
            if response.status != 200:
                raise BridgeError("ADAPTER_HTTP_ERROR", http_diagnostic=http_error_diagnostic(
                    self._prefix + path, response.status))
            headers = response.headers
            if not isinstance(headers, Mapping):
                raise BridgeError("ADAPTER_RESPONSE_INVALID")
            content_types = [v for k, v in headers.items()
                             if isinstance(k, str) and k.lower() == "content-type"]
            if len(content_types) != 1 or not isinstance(content_types[0], str):
                raise BridgeError("ADAPTER_RESPONSE_INVALID")
            kind = content_types[0].split(";", 1)[0].strip().lower()
            if kind not in expected_type:
                raise BridgeError("ADAPTER_RESPONSE_INVALID")
            return response
        except Exception as exc:
            if response is not None:
                self._close(response)
            if isinstance(exc, RetryableAdapterError):
                raise RetryableAdapterError("ADAPTER_RETRYABLE_HTTP") from None
            if isinstance(exc, (TimeoutError, ConnectionError)):
                raise RetryableAdapterError("ADAPTER_TRANSPORT_INTERRUPTED") from None
            if isinstance(exc, BridgeError):
                raise BridgeError(str(exc), http_diagnostic=exc.http_diagnostic) from None
            raise BridgeError("ADAPTER_TRANSPORT_FAILED") from None

    def _read(self, response, size, deadline):
        if self._clock() >= deadline:
            raise RetryableAdapterError("ADAPTER_DEADLINE")
        try:
            data = response.read(size)
        except (TimeoutError, ConnectionError):
            raise RetryableAdapterError("ADAPTER_TRANSPORT_INTERRUPTED") from None
        except Exception:
            raise BridgeError("ADAPTER_TRANSPORT_FAILED") from None
        if not isinstance(data, bytes) or len(data) > size:
            raise BridgeError("ADAPTER_RESPONSE_INVALID")
        if self._clock() >= deadline:
            raise RetryableAdapterError("ADAPTER_DEADLINE")
        return data

    def _json(self, path):
        response = self._open(path, {"application/json"})
        try:
            body = bytearray()
            deadline = self._clock() + self.limits.max_stream_seconds
            while True:
                chunk = self._read(response, min(self.limits.read_chunk_bytes,
                                   self.limits.max_metadata_bytes - len(body) + 1), deadline)
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > self.limits.max_metadata_bytes:
                    raise BridgeError("ADAPTER_METADATA_LIMIT")

            def pairs(items):
                result = {}
                for key, value in items:
                    if key in result:
                        raise ValueError
                    result[key] = value
                return result

            def invalid_constant(_):
                raise ValueError

            return json.loads(body.decode("utf-8"), object_pairs_hook=pairs,
                              parse_constant=invalid_constant)
        except BridgeError:
            raise
        except RetryableAdapterError:
            raise
        except Exception:
            raise BridgeError("ADAPTER_METADATA_INVALID") from None
        finally:
            self._close(response)

    def _inspect(self, candidate):
        obj = self._json("/containers/" + candidate.id + "/json")
        try:
            config, state = obj["Config"], obj["State"]
            labels = config["Labels"]
            if obj["Id"] != candidate.id or not isinstance(labels, dict):
                raise BridgeError("ADAPTER_METADATA_CHANGED")
            if (labels.get("com.docker.compose.project") != PROJECT
                    or labels.get("com.docker.compose.service") != candidate.labels["com.docker.compose.service"]
                    or state["Running"] is not True):
                raise BridgeError("ADAPTER_METADATA_CHANGED")
            if config["Tty"] is not False:
                raise BridgeError("ADAPTER_TTY_UNSUPPORTED")
            # Never retain Env, mounts, command, image or arbitrary Docker labels.
            return Container(candidate.id, MappingProxyType(dict(candidate.labels)))
        except BridgeError:
            raise
        except Exception:
            raise BridgeError("ADAPTER_METADATA_INVALID") from None

    def discover(self, project, services):
        self._approved = {}  # A failed discovery must not leave stale authority.
        if project != PROJECT or len(services) != 3 or frozenset(services) != SERVICES:
            raise BridgeError("ADAPTER_SCOPE_DENIED")
        filters = {"label": ["com.docker.compose.project=" + PROJECT], "status": ["running"]}
        result = self._json("/containers/json?" + urlencode({
            "all": "false", "filters": json.dumps(filters, separators=(",", ":"))}))
        if not isinstance(result, list):
            raise BridgeError("ADAPTER_METADATA_INVALID")
        if len(result) > self.limits.max_candidates:
            raise BridgeError("ADAPTER_METADATA_LIMIT")
        candidates, seen_services = {}, set()
        for obj in result:
            if not isinstance(obj, dict) or not isinstance(obj.get("Labels"), dict):
                raise BridgeError("ADAPTER_METADATA_INVALID")
            labels = obj["Labels"]
            service = labels.get("com.docker.compose.service")
            if (labels.get("com.docker.compose.project") != PROJECT
                    or not isinstance(service, str) or service not in SERVICES):
                continue  # Ignore outside scope even if the server ignores filters.
            ident = obj.get("Id")
            if not isinstance(ident, str) or not re.fullmatch(r"[0-9a-f]{64}", ident):
                raise BridgeError("ADAPTER_METADATA_INVALID")
            if obj.get("State") != "running":
                continue
            if ident in candidates or service in seen_services:
                raise BridgeError("CONTAINER_AMBIGUOUS")
            seen_services.add(service)
            candidates[ident] = Container(ident, {
                "com.docker.compose.project": PROJECT,
                "com.docker.compose.service": service,
            })
        approved = {ident: self._inspect(c) for ident, c in candidates.items()}
        self._approved = approved
        return tuple(approved.values())

    def read_logs(self, container_id, since):
        yield from self._logs(container_id, since, None)

    def read_window(self, container_id, since, until):
        yield from self._logs(container_id, since, until)

    def _logs(self, container_id, since, until):
        if not isinstance(container_id, str) or container_id not in self._approved:
            raise BridgeError("ADAPTER_SCOPE_DENIED")
        # Revalidate only an already allowlisted ID before requesting its logs.
        try:
            self._inspect(self._approved[container_id])
        except (BridgeError, RetryableAdapterError):
            self._approved.pop(container_id, None)
            raise
        params = {"stdout": "true", "stderr": "true", "timestamps": "true",
                  "follow": "false", "tail": "all"}
        if since is not None:
            params["since"] = unix_timestamp(since)
        if until is not None:
            params["until"] = unix_timestamp(until)
            if since is None or timestamp(since)[1] > timestamp(until)[1]:
                raise BridgeError("ADAPTER_RANGE_INVALID")
        response = self._open("/containers/" + container_id + "/logs?" + urlencode(params),
                              {"application/vnd.docker.raw-stream", "application/vnd.docker.multiplexed-stream"})
        try:
            for record in self._decode(response):
                if until is not None and not timestamp(since)[1] <= timestamp(record.timestamp)[1] <= timestamp(until)[1]:
                    raise BridgeError("ADAPTER_RANGE_INVALID")
                yield record
        finally:
            self._close(response)

    def _decode(self, response):
        total, frames, records = 0, 0, 0
        buffers = {1: bytearray(), 2: bytearray()}
        deadline = self._clock() + self.limits.max_stream_seconds

        def take(size, *, boundary=False):
            nonlocal total
            data = bytearray()
            while len(data) < size:
                # Read one extra byte only when needed to detect a quota breach.
                amount = min(size - len(data), self.limits.read_chunk_bytes,
                             self.limits.max_stream_bytes - total + 1)
                part = self._read(response, amount, deadline)
                if not part:
                    if boundary and not data:
                        return None
                    raise RetryableAdapterError("ADAPTER_STREAM_INTERRUPTED")
                total += len(part)
                if total > self.limits.max_stream_bytes:
                    raise BridgeError("ADAPTER_STREAM_LIMIT")
                data.extend(part)
            return data

        while True:
            header = take(8, boundary=True)
            if header is None:
                if any(buffers.values()):
                    # Never commit an unterminated line as a complete message.
                    raise RetryableAdapterError("ADAPTER_STREAM_INTERRUPTED")
                return
            frames += 1
            if frames > self.limits.max_frames:
                raise BridgeError("ADAPTER_STREAM_LIMIT")
            stream = header[0]
            if stream not in buffers or header[1:4] != b"\0\0\0":
                raise BridgeError("ADAPTER_FRAME_INVALID")
            length = int.from_bytes(header[4:8], "big")
            if length > self.limits.max_frame_bytes:
                raise BridgeError("ADAPTER_FRAME_LIMIT")
            remaining = length
            while remaining:
                part = take(min(remaining, self.limits.read_chunk_bytes))
                remaining -= len(part)
                pending = buffers[stream]
                pending.extend(part)
                while b"\n" in pending:
                    end = pending.index(b"\n")
                    if end > self.policy.max_input_bytes + 64:
                        raise BridgeError("ADAPTER_INPUT_LIMIT")
                    line = bytes(pending[:end])
                    del pending[:end + 1]
                    try:
                        stamp, message = line.decode("utf-8", errors="strict").split(" ", 1)
                    except UnicodeError:
                        raise BridgeError("ADAPTER_ENCODING_INVALID") from None
                    except ValueError:
                        raise BridgeError("TIMESTAMP_INVALID") from None
                    canonical, _ = timestamp(stamp)
                    if len(message.encode("utf-8")) > self.policy.max_input_bytes:
                        raise BridgeError("ADAPTER_INPUT_LIMIT")
                    records += 1
                    if records > self.limits.max_records:
                        raise BridgeError("ADAPTER_RECORD_LIMIT")
                    yield Record(canonical, "stdout" if stream == 1 else "stderr", message)
                if len(pending) > self.policy.max_input_bytes + 64:
                    raise BridgeError("ADAPTER_INPUT_LIMIT")


STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$")
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def timestamp(value: str) -> tuple[str, int]:
    try:
        match = STAMP.fullmatch(value)
        if not match:
            raise ValueError
        base, fraction, zone = match.groups()
        if zone != "Z" and (int(zone[1:3]) > 23 or int(zone[4:]) > 59):
            raise ValueError
        dt = datetime.fromisoformat(base + ("+00:00" if zone == "Z" else zone))
        delta = dt.astimezone(timezone.utc) - EPOCH
        seconds = delta.days * 86400 + delta.seconds
        ns = seconds * 10**9 + int((fraction or "").ljust(9, "0"))
        utc = EPOCH + timedelta(seconds=seconds)
        return utc.strftime("%Y-%m-%dT%H:%M:%S") + f".{ns % 10**9:09d}Z", ns
    except Exception:
        raise BridgeError("TIMESTAMP_INVALID") from None


def stamp_from_ns(ns: int) -> str:
    seconds, fraction = divmod(ns, 10**9)
    return (EPOCH + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S") + f".{fraction:09d}Z"


def validate_unix_timestamp(value: str) -> str:
    """Canonical Docker wire time, seconds.nanoseconds; integer arithmetic only."""
    if (not isinstance(value, str)
            or not re.fullmatch(r"-?(?:0|[1-9][0-9]{0,18})\.[0-9]{9}", value)
            or value.startswith("-0.")
            or not -(2**63) <= int(value.partition(".")[0]) < 2**63):
        raise BridgeError("TIMESTAMP_INVALID")
    return value


def unix_timestamp(value: str) -> str:
    # CLI, records and persisted state retain their existing representation.
    seconds, fraction = divmod(timestamp(value)[1], 10**9)
    return validate_unix_timestamp(f"{seconds}.{fraction:09d}")


SENSITIVE_KEY = re.compile(r"(?i)password|passwd|pwd|secret|token|cookie|authorization|api[-_]?key|session")
ASSIGNMENT = re.compile(
    r'''(?ix)\b(password|passwd|pwd|secret|(?:access[_-]?|refresh[_-]?)?token|api[_-]?key|session[_-]?id)\b
    ["']?(\s*[:=]\s*)(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;&]+)''')
HEADER = re.compile(r'''(?im)\b(authorization|proxy-authorization|cookie|set-cookie)["']?\s*[:=]\s*[^\r\n]*(?:\r?\n[ \t]+[^\r\n]*)*''')
QUERY = re.compile(r'''((?:https?://|/)[^\s?"<>]*)\?[^\s"<>]*''', re.I)


def redact(message: str) -> str:
    def text(value):
        value = HEADER.sub(lambda m: m.group(1) + ": " + REDACTED, value)
        value = ASSIGNMENT.sub(lambda m: m.group(1) + m.group(2) + REDACTED, value)
        value = re.sub(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", REDACTED, value)
        value = re.sub(r"(?i)(https?://)[^/\s@]+@", r"\1[REDACTED]@", value)
        return QUERY.sub(lambda m: m.group(1) + "?" + REDACTED, value)

    def walk(value):
        if isinstance(value, dict):
            return {k: REDACTED if SENSITIVE_KEY.search(k) else walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        return text(value) if isinstance(value, str) else value

    try:
        obj = json.loads(message)
    except (ValueError, RecursionError):
        return text(message)
    if isinstance(obj, (dict, list)):
        return json.dumps(walk(obj), ensure_ascii=False, separators=(",", ":"))
    return text(message)


def encode_record(record: Record, service: str, policy: Policy, now_ns: int) -> tuple[bytes, int, str]:
    if not isinstance(record.message, str) or record.stream not in ("stdout", "stderr"):
        raise BridgeError("RECORD_INVALID")
    try:
        raw = record.message.encode("utf-8")
    except UnicodeError:
        raise BridgeError("RECORD_ENCODING_INVALID") from None
    if len(raw) > policy.max_input_bytes:
        raise BridgeError("INPUT_TOO_LARGE")
    canonical, ns = timestamp(record.timestamp)
    if ns > now_ns + policy.max_future_skew_seconds * 10**9:
        raise BridgeError("TIMESTAMP_IN_FUTURE")
    # Hash before truncation to distinguish controlled replay of long records.
    safe = redact(record.message)
    fingerprint = hashlib.sha256(json.dumps([canonical, record.stream, safe],
                                           ensure_ascii=False).encode()).hexdigest()
    obj = dict(timestamp=canonical, project=PROJECT, service=service,
               stream=record.stream, message=safe)

    def pack(message):
        return (json.dumps({**obj, "message": message}, ensure_ascii=False,
                           separators=(",", ":")) + "\n").encode("utf-8")

    data = pack(safe)
    if len(data) > policy.max_line_bytes:
        lo, hi = 0, len(safe)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if len(pack(safe[:mid] + " [TRUNCATED]")) <= policy.max_line_bytes:
                lo = mid
            else:
                hi = mid - 1
        data = pack(safe[:lo] + " [TRUNCATED]")
    if len(data) > policy.max_line_bytes:
        raise BridgeError("LINE_LIMIT_TOO_SMALL")
    return data, ns, fingerprint


def _json_bytes(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def posix_permissions(fd, mode, gid=None):
    """Used only by an explicitly enabled live spool, not a Windows ACL claim."""
    if os.name != "posix":
        raise BridgeError("SPOOL_PERMISSION_INVALID")
    try:
        if os.fstat(fd).st_uid != os.geteuid():
            raise BridgeError("SPOOL_PERMISSION_INVALID")
        if gid is not None:
            os.fchown(fd, -1, gid)
        os.fchmod(fd, mode)
        current = os.fstat(fd)
        if stat.S_IMODE(current.st_mode) != mode or (gid is not None and current.st_gid != gid):
            raise BridgeError("SPOOL_PERMISSION_INVALID")
    except Exception:
        raise BridgeError("SPOOL_PERMISSION_INVALID") from None


class Spool:
    """Single writer; durable pending transaction resolves append/checkpoint gap.

    No eviction: full spool stops progress. Quotas count logical regular-file
    bytes, including lock, journal and atomic copies, NOT allocated blocks,
    directories/inodes, filesystem journal or concurrent external writers.
    Linux chmod/parent fsync are not Windows ACL QA.
    """

    def __init__(self, root: Path, policy: Policy, fault: Callable[[str], None] | None = None,
                 *, reader_gid: int | None = None, stop_spool_bytes: int | None = None):
        self.root = root.absolute()
        self.policy = policy
        if (stop_spool_bytes is not None and
                (type(stop_spool_bytes) is not int or not 0 < stop_spool_bytes <= policy.max_spool_bytes)):
            raise BridgeError("SPOOL_STOP_CONFIG_INVALID")
        self.stop_spool_bytes = stop_spool_bytes
        self.fault = fault or (lambda _: None)
        if reader_gid is not None and (type(reader_gid) is not int or reader_gid <= 0):
            raise BridgeError("SPOOL_PERMISSION_INVALID")
        self.reader_gid = reader_gid
        self.lock = None
        if any(p.is_symlink() for p in (self.root, *self.root.parents)):
            raise BridgeError("SPOOL_PATH_UNSAFE")
        try:
            if stop_spool_bytes is not None:
                # Refuse an oversized existing spool before chmod, cleanup or I/O.
                self._require_capacity(0)
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if reader_gid is not None:
                for entry in self.root.iterdir():
                    if entry.name in SERVICES and entry.is_dir() and not entry.is_symlink():
                        if any(not p.is_file() or p.is_symlink() or not re.fullmatch(r"[0-9]{20}\.jsonl", p.name)
                               for p in entry.iterdir()):
                            raise BridgeError("SPOOL_PATH_UNSAFE")
                    elif entry.name not in {"bridge.lock", "checkpoint.json", "checkpoint.tmp"} or not entry.is_file():
                        raise BridgeError("SPOOL_PATH_UNSAFE")
                self._directory_mode(self.root, 0o700)
            if any(p.is_symlink() for p in self.root.rglob("*")):
                raise BridgeError("SPOOL_PATH_UNSAFE")
            lockpath = self.root / "bridge.lock"
            if stop_spool_bytes is not None:
                # The lock marker is also one logical byte of spool metadata.
                self._require_capacity(0 if lockpath.exists() and lockpath.stat().st_size else 1)
            fd = os.open(lockpath, os.O_RDWR | os.O_CREAT, 0o600)
            self.lock = os.fdopen(fd, "r+b", buffering=0)
            if reader_gid is not None:
                posix_permissions(self.lock.fileno(), 0o600)
            if lockpath.stat().st_size == 0:
                self.lock.write(b"0")
            self.lock.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if reader_gid is not None:
                for service in sorted(SERVICES):
                    folder = self.root / service
                    folder.mkdir(mode=0o700, exist_ok=True)
                    self._directory_mode(folder, 0o2750, reader_gid)
            self.state_path = self.root / "checkpoint.json"
            stale = self.root / "checkpoint.tmp"
            if stale.exists():
                stale.unlink()  # Only an abandoned atomic copy in this private spool.
            if self.state_path.exists():
                if reader_gid is not None:
                    s = self.state_path.stat()
                    if s.st_uid != os.geteuid() or stat.S_IMODE(s.st_mode) != 0o600:
                        raise BridgeError("SPOOL_PERMISSION_INVALID")
                if self.state_path.stat().st_size > policy.max_state_bytes:
                    raise BridgeError("STATE_LIMIT")
                self.state = json.loads(self.state_path.read_bytes())
                if self.state.get("version") != 1 or not isinstance(self.state.get("containers"), dict):
                    raise BridgeError("STATE_INVALID")
                if len(self.state["containers"]) > policy.max_checkpoint_containers:
                    raise BridgeError("STATE_INVALID")
                for ident, cp in self.state["containers"].items():
                    self._validate_checkpoint(ident, cp)
                self._validate_scans()
            else:
                self.state = {"version": 1, "containers": {}}
            self.recover()
        except BridgeError:
            self.close()
            raise
        except Exception:
            self.close()
            raise BridgeError("SPOOL_OPEN_FAILED") from None

    def close(self):
        if self.lock is not None:
            self.lock.close()
            self.lock = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _sync_dir(self, path):
        if os.name != "nt":
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def _directory_mode(self, path, mode, gid=None):
        if os.name != "posix":
            raise BridgeError("SPOOL_PERMISSION_INVALID")
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            posix_permissions(fd, mode, gid)
        finally:
            os.close(fd)

    def _usage(self):
        return sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())

    def _require_capacity(self, additional):
        projected = self._usage() + additional
        if projected > self.policy.max_spool_bytes:
            raise BridgeError("SPOOL_FULL")
        if self.stop_spool_bytes is not None and projected > self.stop_spool_bytes:
            raise BridgeError("SPOOL_STOP_LIMIT")

    def _save(self, state):
        data = _json_bytes(state)
        if len(data) > self.policy.max_state_bytes:
            raise BridgeError("STATE_LIMIT")
        # Include atomic temporary copy in the spool budget.
        self._require_capacity(len(data))
        temp = self.root / "checkpoint.tmp"
        with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
            if self.reader_gid is not None:
                posix_permissions(f.fileno(), 0o600)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, self.state_path)
        self._sync_dir(self.root)
        self.state = state

    def _pending_path(self, relative):
        if not re.fullmatch(r"(?:nginx|wordpress|mysql)/\d{20}\.jsonl", relative):
            raise BridgeError("STATE_PATH_INVALID")
        path = self.root / relative
        if path.is_symlink() or path.parent.is_symlink():
            raise BridgeError("SPOOL_PATH_UNSAFE")
        return path

    def _validate_checkpoint(self, ident, cp):
        if (not re.fullmatch(r"[0-9a-f]{64}", ident) or not isinstance(cp, dict)
                or cp.get("service") not in SERVICES or type(cp.get("high")) is not int
                or not isinstance(cp.get("counts"), dict)
                or not (0 < len(cp["counts"]) <= self.policy.max_window_keys)):
            raise BridgeError("STATE_INVALID")
        cutoff = cp["high"] - self.policy.replay_window_seconds * 10**9
        for key, count in cp["counts"].items():
            if (not re.fullmatch(r"-?\d+:[0-9a-f]{64}", key)
                    or type(count) is not int or count < 1
                    or not (cutoff <= int(key.split(":", 1)[0]) <= cp["high"])):
                raise BridgeError("STATE_INVALID")

    def _validate_scans(self):
        scans = self.state.get("scans", {})
        bootstrap = self.state.get("bootstrap_ns")
        if bootstrap is not None and (type(bootstrap) is not int or bootstrap < 0):
            raise BridgeError("SCAN_STATE_INVALID")
        if not isinstance(scans, dict) or len(set(scans) | set(self.state["containers"])) > self.policy.max_checkpoint_containers:
            raise BridgeError("SCAN_STATE_INVALID")
        for ident, scan in scans.items():
            if (bootstrap is None or not re.fullmatch(r"[0-9a-f]{64}", ident)
                    or not isinstance(scan, dict) or scan.get("service") not in SERVICES
                    or type(scan.get("until")) is not int or scan["until"] < bootstrap):
                raise BridgeError("SCAN_STATE_INVALID")

    def bootstrap(self, ns):
        if type(ns) is not int or ns < 0:
            raise BridgeError("BOOTSTRAP_INVALID")
        if "bootstrap_ns" in self.state:
            if self.state["bootstrap_ns"] != ns:
                raise BridgeError("BOOTSTRAP_CONFLICT")
        elif self.state["containers"]:
            raise BridgeError("BOOTSTRAP_MIGRATION_REQUIRED")
        else:
            self._save({**self.state, "bootstrap_ns": ns, "scans": {}})

    def scan(self, container):
        scan = self.state.get("scans", {}).get(container.id)
        if scan and scan["service"] != container.labels["com.docker.compose.service"]:
            raise BridgeError("CONTAINER_IDENTITY_CHANGED")
        return scan

    def complete_scan(self, container, until):
        scan = self.scan(container)
        if until < self.state["bootstrap_ns"] or (scan and until < scan["until"]):
            raise BridgeError("SCAN_STATE_INVALID")
        scans = {**self.state.get("scans", {}), container.id: {
            "service": container.labels["com.docker.compose.service"], "until": until}}
        if len(set(scans) | set(self.state["containers"])) > self.policy.max_checkpoint_containers:
            raise BridgeError("CONTAINER_STATE_LIMIT")
        self._save({**self.state, "scans": scans})

    def recover(self):
        pending = self.state.get("pending")
        if not pending:
            return
        self._validate_checkpoint(pending["id"], pending["checkpoint"])
        path = self._pending_path(pending["file"])
        if path.parent.name != pending["checkpoint"]["service"]:
            raise BridgeError("STATE_INVALID")
        offset, length = pending["offset"], pending["length"]
        if type(offset) is not int or type(length) is not int or offset < 0 or not (0 < length <= self.policy.max_line_bytes):
            raise BridgeError("STATE_INVALID")
        size = path.stat().st_size if path.exists() else 0
        if size < offset or size > offset + length:
            raise BridgeError("RECOVERY_CONFLICT")
        state = {**self.state}
        state.pop("pending")
        if size == offset + length:
            with path.open("rb") as f:
                f.seek(offset)
                digest = hashlib.sha256(f.read(length)).hexdigest()
            if digest != pending["digest"]:
                raise BridgeError("RECOVERY_CONFLICT")
            state["containers"] = {**state["containers"], pending["id"]: pending["checkpoint"]}
        elif size > offset:
            if self.stop_spool_bytes is not None:
                # If recovery cannot save its state, do not truncate even the
                # uncommitted tail on account of a soft-limit stop.
                self._require_capacity(len(_json_bytes(state)))
            # Discard only the interrupted, uncommitted tail, never older records.
            with path.open("r+b") as f:
                f.truncate(offset)
                f.flush()
                os.fsync(f.fileno())
        self._save(state)

    def checkpoint(self, container):
        return self.state["containers"].get(container.id)

    def append(self, container: Container, data: bytes, checkpoint):
        service = container.labels["com.docker.compose.service"]
        if self.state.get("pending"):
            raise BridgeError("RECOVERY_REQUIRED")
        if len(set(self.state["containers"]) | set(self.state.get("scans", {})) | {container.id}) > self.policy.max_checkpoint_containers:
            raise BridgeError("CONTAINER_STATE_LIMIT")
        folder = self.root / service
        folder.mkdir(mode=0o700, exist_ok=True)
        if self.reader_gid is not None:
            self._directory_mode(folder, 0o2750, self.reader_gid)
        self._sync_dir(self.root)
        files = sorted(folder.glob("*.jsonl"))
        path = files[-1] if files else folder / "00000000000000000001.jsonl"
        if not re.fullmatch(r"\d{20}\.jsonl", path.name):
            raise BridgeError("SPOOL_PATH_UNSAFE")
        size = path.stat().st_size if path.exists() else 0
        if size + len(data) > self.policy.max_segment_bytes:
            path = folder / f"{int(path.stem) + 1:020d}.jsonl"
            size = 0
        pending = dict(file=path.relative_to(self.root).as_posix(), offset=size,
                       length=len(data), digest=hashlib.sha256(data).hexdigest(),
                       id=container.id, checkpoint=checkpoint)
        prepared = {**self.state, "pending": pending}
        committed = {**self.state, "containers": {**self.state["containers"], container.id: checkpoint}}
        # Reserve pending, final atomic copy and record before modifying a file.
        if max(len(_json_bytes(prepared)), len(_json_bytes(committed))) > self.policy.max_state_bytes:
            raise BridgeError("STATE_LIMIT")
        # Conservative transaction reservation: includes BOTH state images and
        # the record before the journal is written. Subsequent _save checks fit
        # within this reservation for a single writer with no external mutations.
        self._require_capacity(len(_json_bytes(prepared)) + len(_json_bytes(committed)) + len(data))
        self._save(prepared)
        self.fault("prepared")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "ab") as f:
            if self.reader_gid is not None:
                posix_permissions(f.fileno(), 0o640, self.reader_gid)
            if os.fstat(f.fileno()).st_size != size:
                raise BridgeError("SPOOL_CONCURRENT_CHANGE")
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        self._sync_dir(folder)
        self.fault("appended")
        self._save(committed)
        self.fault("committed")


@dataclass
class Stats:
    accepted: int = 0
    duplicates: int = 0
    rejected_containers: int = 0
    retries: int = 0
    catchup_pending: bool = False
    max_lag_seconds: int = 0


class Bridge:
    def __init__(self, adapter: DockerAdapter, spool: Spool, policy: Policy,
                 now: Callable[[], int] = time.time_ns, sleep: Callable[[float], None] = time.sleep,
                 *, bootstrap_since: str | None = None, batch_seconds: int | None = None,
                 stop_requested=lambda: False, allow_replacement=False):
        self.adapter, self.spool, self.policy = adapter, spool, policy
        self.now, self.sleep = now, sleep
        self.batch_seconds, self.stop_requested = batch_seconds, stop_requested
        self.allow_replacement = allow_replacement
        if (bootstrap_since is None) != (batch_seconds is None):
            raise BridgeError("RUN_CONFIG_INVALID")
        if batch_seconds is not None:
            if (type(batch_seconds) is not int or not 1 <= batch_seconds <= policy.replay_window_seconds
                    or not callable(getattr(adapter, "read_window", None))):
                raise BridgeError("RUN_CONFIG_INVALID")
            ns = timestamp(bootstrap_since)[1]
            if ns > now() or ns < 0:
                raise BridgeError("BOOTSTRAP_INVALID")
            if "bootstrap_ns" not in spool.state and ns < now() - 168 * 3600 * 10**9:
                raise BridgeError("BOOTSTRAP_INVALID")
            spool.bootstrap(ns)

    def _containers(self, stats):
        selected = {}
        services = set()
        for c in self.adapter.discover(PROJECT, self.policy.services):
            if not isinstance(c, Container) or not isinstance(c.labels, Mapping):
                stats.rejected_containers += 1
                continue
            service = c.labels.get("com.docker.compose.service")
            if (not isinstance(c.id, str) or not re.fullmatch(r"[0-9a-f]{64}", c.id)
                    or c.labels.get("com.docker.compose.project") != PROJECT
                    or service not in SERVICES or c.running is not True or c.tty is not False):
                stats.rejected_containers += 1
                continue
            if c.id in selected or service in services:
                raise BridgeError("CONTAINER_AMBIGUOUS")
            # Copy only approved metadata; adapter-owned mutable labels cannot
            # change the validated identity while another service is processed.
            selected[c.id] = Container(c.id, {
                "com.docker.compose.project": PROJECT,
                "com.docker.compose.service": service,
            })
            services.add(service)
        if self.batch_seconds is not None:
            if services != SERVICES:
                raise RetryableAdapterError("SOURCE_SET_INCOMPLETE")
            if not self.allow_replacement:
                known = {**self.spool.state.get("scans", {}), **self.spool.state["containers"]}
                for ident, cp in known.items():
                    if ident not in selected and cp["service"] in services:
                        raise BridgeError("SOURCE_REPLACED_REVIEW_REQUIRED")
        return selected.values()

    def run_once(self):
        stats = Stats()
        try:
            for attempt in range(self.policy.retry_attempts):
                if self.stop_requested():
                    raise ShutdownRequested()
                try:
                    active = list(self._containers(stats))
                    for container in active:
                        self._read(container, stats)
                    if self.batch_seconds is not None:
                        cursors = [self.spool.state["scans"][c.id]["until"] for c in active]
                        stats.max_lag_seconds = max(0, (self.now() - min(cursors)) // 10**9)
                        stats.catchup_pending = stats.max_lag_seconds >= self.batch_seconds
                    return stats
                except RetryableAdapterError:
                    if attempt + 1 == self.policy.retry_attempts:
                        raise BridgeError("ADAPTER_RETRIES_EXHAUSTED") from None
                    stats.retries += 1
                    self.sleep(min(self.policy.retry_max_seconds,
                                   self.policy.retry_base_seconds * 2**attempt))
        except BridgeError:
            raise
        except Exception:
            raise BridgeError("BRIDGE_OPERATION_FAILED") from None

    def _read(self, container, stats):
        cp = self.spool.checkpoint(container)
        if cp and cp.get("service") != container.labels["com.docker.compose.service"]:
            raise BridgeError("CONTAINER_IDENTITY_CHANGED")
        since = stamp_from_ns(cp["high"] - self.policy.replay_window_seconds * 10**9) if cp else None
        until_ns = None
        if self.batch_seconds is not None:
            scan = self.spool.scan(container)
            floor = self.spool.state["bootstrap_ns"]
            cursor = scan["until"] if scan else floor
            if cursor > self.now():
                raise BridgeError("SCAN_STATE_INVALID")
            until_ns = min(cursor + self.batch_seconds * 10**9, self.now())
            anchor = max(cursor, cp["high"] if cp else floor)
            since = stamp_from_ns(max(floor, anchor - self.policy.replay_window_seconds * 10**9))
            reader = iter(self.adapter.read_window(container.id, since, stamp_from_ns(until_ns)))
        else:
            reader = iter(self.adapter.read_logs(container.id, since))
        try:
            self._consume(container, reader, stats)
            if self.stop_requested():
                raise ShutdownRequested()
            if until_ns is not None:
                self.spool.complete_scan(container, until_ns)
        finally:
            close = getattr(reader, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass  # Never leak a transport's exception from cleanup.

    def _consume(self, container, reader, stats):
        observed = Counter()
        for record in reader:
            if self.stop_requested():
                raise ShutdownRequested()
            data, ns, digest = encode_record(record, container.labels["com.docker.compose.service"], self.policy, self.now())
            key = str(ns) + ":" + digest
            observed[key] += 1
            cp = self.spool.checkpoint(container)
            high = max(cp["high"], ns) if cp else ns
            cutoff = high - self.policy.replay_window_seconds * 10**9
            if ns < cutoff:
                raise BridgeError("RECORD_OUTSIDE_REPLAY_WINDOW")
            observed = Counter({k: v for k, v in observed.items()
                                if int(k.split(":", 1)[0]) >= cutoff})
            counts = cp["counts"] if cp else {}
            if observed[key] <= counts.get(key, 0):
                stats.duplicates += 1
                continue
            counts = {k: v for k, v in counts.items() if int(k.split(":", 1)[0]) >= cutoff}
            counts[key] = observed[key]
            if len(counts) > self.policy.max_window_keys or len(observed) > self.policy.max_window_keys:
                raise BridgeError("REPLAY_WINDOW_LIMIT")
            checkpoint = dict(service=container.labels["com.docker.compose.service"], high=high, counts=counts)
            self.spool.append(container, data, checkpoint)
            stats.accepted += 1


@dataclass(frozen=True)
class PollSettings:
    interval_seconds: float = 5.0
    max_polls: int = 1
    max_failed_polls: int = 3
    error_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 30.0

    def __post_init__(self):
        if (type(self.max_polls) is not int or self.max_polls < 0
                or type(self.max_failed_polls) is not int or not 1 <= self.max_failed_polls <= 10):
            raise BridgeError("RUN_CONFIG_INVALID")
        values = (self.interval_seconds, self.error_backoff_seconds, self.max_backoff_seconds)
        if any(type(v) not in (float, int) or not math.isfinite(v) or not 0 < v <= 60 for v in values):
            raise BridgeError("RUN_CONFIG_INVALID")
        if self.interval_seconds < 1 or self.error_backoff_seconds > self.max_backoff_seconds:
            raise BridgeError("RUN_CONFIG_INVALID")


def polling_loop(bridge, settings, stop, report=lambda *_: None):
    polls, failed = 0, 0
    try:
        while not stop.is_set() and (settings.max_polls == 0 or polls < settings.max_polls):
            polls += 1
            try:
                result = bridge.run_once()
                failed = 0
                report("POLL_OK", result)
            except BridgeError as exc:
                if str(exc) != "ADAPTER_RETRIES_EXHAUSTED":
                    raise
                failed += 1
                report("POLL_RETRY", None)
                if failed >= settings.max_failed_polls:
                    raise BridgeError("POLL_RETRIES_EXHAUSTED") from None
            if settings.max_polls and polls >= settings.max_polls:
                break
            delay = (min(settings.max_backoff_seconds,
                         settings.error_backoff_seconds * 2**(failed - 1))
                     if failed else settings.interval_seconds)
            if stop.wait(delay):
                break
        if failed and not stop.is_set():
            raise BridgeError("POLL_RETRIES_EXHAUSTED")
    except ShutdownRequested:
        return polls
    return polls


class _SafeParser(argparse.ArgumentParser):
    def error(self, _message):
        # argparse's normal error includes raw argument values/paths.
        print("FAIL CLI_ARGUMENT_INVALID", file=sys.stderr)
        raise SystemExit(2)


def _live_environment(args):
    if not sys.platform.startswith("linux"):
        raise BridgeError("LIVE_PLATFORM_REQUIRED")
    if os.geteuid() == 473:
        raise BridgeError("LIVE_IDENTITY_INVALID")
    # Never let a typo such as --spool / chmod an existing system directory.
    if args.spool.as_posix() != "/var/lib/beautyglow-logs":
        raise BridgeError("SPOOL_PATH_UNSAFE")
    if args.socket.as_posix() != "/run/docker.sock":
        raise BridgeError("LIVE_SOCKET_INVALID")
    try:
        info = os.stat(args.socket)
    except OSError:
        raise BridgeError("LIVE_SOCKET_INVALID") from None
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o002:
        raise BridgeError("LIVE_SOCKET_INVALID")
    if args.read_gid in (0, info.st_gid) or (os.geteuid() != 0 and args.read_gid not in os.getgroups() + [os.getegid()]):
        raise BridgeError("LIVE_GROUP_INVALID")


def _run_live(args, policy, settings):
    _live_environment(args)
    stop = threading.Event()
    previous = {}

    def handler(_signum, _frame):
        stop.set()  # No filesystem/network I/O in a signal handler.

    def wait(delay):
        if stop.wait(delay):
            raise ShutdownRequested()

    def report(code, result):
        if result is None:
            print(code, flush=True)
        else:
            print(json.dumps({"code": code, "accepted": result.accepted,
                              "duplicates": result.duplicates, "retries": result.retries,
                              "rejected_containers": result.rejected_containers,
                              "catchup_pending": result.catchup_pending,
                              "max_lag_seconds": result.max_lag_seconds}), flush=True)

    transport = UnixHTTPTransport(str(args.socket), stop=stop.is_set)
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, handler)
        with Spool(args.spool, policy, reader_gid=args.read_gid,
                   stop_spool_bytes=getattr(args, "stop_spool_bytes", None)) as spool:
            bridge = Bridge(DockerAPIAdapter(transport, policy, api_version=args.api_version),
                            spool, policy, sleep=wait, bootstrap_since=args.bootstrap_since,
                            batch_seconds=args.batch_seconds, stop_requested=stop.is_set,
                            allow_replacement=args.allow_source_replacement)
            polling_loop(bridge, settings, stop, report)
        print("BRIDGE_STOPPED" if stop.is_set() else "BRIDGE_POLL_LIMIT_REACHED", flush=True)
        return 0
    finally:
        transport.close()
        for sig, old in previous.items():
            signal.signal(sig, old)


def main(argv=None):
    parser = _SafeParser(description="BeautyGlow bridge: dry-run is offline; --live opens the approved Unix socket.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-policy", type=Path)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--live", action="store_true")
    parser.add_argument("--policy", type=Path, default=Path(__file__).resolve().parents[1]/"config/alloy/bridge-policy.json")
    parser.add_argument("--socket", type=Path, default=Path("/run/docker.sock"))
    parser.add_argument("--spool", type=Path)
    parser.add_argument("--read-gid", type=int)
    parser.add_argument("--bootstrap-since")
    parser.add_argument("--batch-seconds", type=int, default=30)
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--max-polls", type=int, default=1, help="0 means continuous polling until signal/error")
    parser.add_argument("--stop-spool-bytes", type=int, default=None,
                        help="Optional logical-file-byte stop budget, including lock/state/temp copies; not disk blocks")
    parser.add_argument("--api-version", default="1.51")
    parser.add_argument("--allow-source-replacement", action="store_true",
                        help="explicitly accept replacement IDs; cannot recover unread removed-source logs")
    args = parser.parse_args(argv)
    try:
        policy = Policy.load(args.check_policy or args.policy)
        if args.check_policy:
            print("PASS policy; no Docker connection or spool created.")
            return 0
        settings = PollSettings(interval_seconds=args.poll_interval, max_polls=args.max_polls)
        if args.stop_spool_bytes is not None and not 0 < args.stop_spool_bytes <= policy.max_spool_bytes:
            raise BridgeError("SPOOL_STOP_CONFIG_INVALID")
        if not 1 <= args.batch_seconds <= policy.replay_window_seconds or not re.fullmatch(r"1\.[0-9]{1,2}", args.api_version):
            raise BridgeError("RUN_CONFIG_INVALID")
        if args.bootstrap_since is not None:
            ns = timestamp(args.bootstrap_since)[1]
            if ns < 0 or ns > time.time_ns():
                raise BridgeError("BOOTSTRAP_INVALID")
        if args.dry_run:
            print("PASS DRY_RUN; project=beautyglow-website; services=nginx,wordpress,mysql; Docker=disabled; spool=not-created")
            return 0
        if args.spool is None or not args.spool.is_absolute() or args.read_gid is None or not 0 < args.read_gid < 2**31 or args.bootstrap_since is None:
            raise BridgeError("CLI_ARGUMENT_INVALID")
        return _run_live(args, policy, settings)
    except ShutdownRequested:
        print("BRIDGE_STOPPED")
        return 0
    except BridgeError as exc:
        if exc.http_diagnostic is not None:
            print(json.dumps(dict(exc.http_diagnostic), sort_keys=True))
        else:
            print("FAIL " + str(exc))
        return 3 if str(exc) == "SPOOL_STOP_LIMIT" else 2
    except Exception:
        print("FAIL BRIDGE_OPERATION_FAILED")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
