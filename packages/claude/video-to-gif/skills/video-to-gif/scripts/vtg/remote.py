"""Opt-in remote source acquisition (spec FR-018..023, SEC-012..017).

This is the ONLY network-capable module in the engine. Everything else stays
network-isolated under SEC-010. A remote source is downloaded to a secure
temporary directory and then handed to the existing local pipeline as untrusted
local media (SEC-012); the download-only guarantee (section 18) means the engine
never uploads anything.

Layering of guarantees, in the order they are enforced for a direct URL:

1. Enablement gate (FR-018): a URL supplied while remote sources are disabled is
   rejected with REMOTE_DISABLED (exit 8) BEFORE any network access. "ask" is a
   skill-layer value; the engine treats it as disabled unless --allow-remote.
2. Scheme allowlist (SEC-013): https always; http only with explicit
   acknowledgment; every other scheme (including file) -> UNSUPPORTED_URL_SCHEME
   (exit 5), never fetched or opened. Re-enforced on every redirect target.
3. DRM/access-control integrity (SEC-017): sources signalling DRM are rejected
   with DRM_PROTECTED (exit 5); no circumvention is attempted.
4. SSRF guard (SEC-014): the hostname is resolved, every resolved address is
   validated against the loopback/private/link-local/multicast/metadata block
   list, and the connection is pinned to the validated address (DNS-rebinding
   resistant) while SNI/Host carry the original hostname. Re-validated per hop.
5. Download hardening (FR-021/SEC-016): size ceiling enforced on bytes actually
   received, wall-clock timeout, and a free-disk pre-check. Partial downloads are
   removed on any failure or cancellation (section 16).

Every URL echoed anywhere (errors, warnings, progress, results) is redacted by
:func:`redact_url` (SEC-015): scheme, host and path only -- userinfo and the
entire query string are stripped.
"""

from __future__ import annotations

import contextlib
import http.client
import ipaddress
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from . import cleanup, dependencies, errors
from .progress import NULL_REPORTER, ProgressReporter

# --- Tunables ---------------------------------------------------------------
_MAX_REDIRECTS = 5
_CHUNK_SIZE = 65536
_PROGRESS_MIN_INTERVAL = 0.25  # seconds between download progress events
_CONNECT_TIMEOUT_CAP = 30.0  # per-socket-op ceiling; wall-clock is authoritative
_USER_AGENT = "video-to-gif/0.2 (+https://github.com/Krishna2709/giffify)"
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

# Cloud instance-metadata endpoints (SEC-014). Link-local/private ranges below
# already cover most; these are listed explicitly as defense in depth.
_METADATA_ADDRESSES = frozenset(
    {
        "169.254.169.254",  # AWS / GCP / Azure IMDS
        "100.100.100.200",  # Alibaba Cloud
        "fd00:ec2::254",  # AWS IMDS over IPv6
    }
)

# Conservative DRM signatures scanned in the first response chunk (SEC-017). Kept
# to strong, unambiguous Common-Encryption / DRM markers to avoid false rejection
# of ordinary media. ffprobe + the reference-following-container rejection remain
# the authoritative media gate.
_DRM_BYTE_MARKERS: tuple[bytes, ...] = (
    b"pssh",  # ISO-BMFF Protection System Specific Header box
    b"tenc",  # Track Encryption box (CENC)
    b"\xed\xef\x8b\xa9\x79\xd6\x4a\xce\xa3\xc8\x27\xdc\xd5\x1d\x21\xed",  # Widevine UUID
    b"\x9a\x04\xf0\x79\x98\x40\x42\x86\xab\x92\xe6\x5b\xe0\x88\x5f\x95",  # PlayReady UUID
)
_DRM_TEXT_MARKERS: tuple[bytes, ...] = (
    b"contentprotection",  # DASH MPD ContentProtection element
    b"urn:mpeg:cenc",
    b"cenc:default_kid",
    b"#ext-x-key:method=sample-aes",
    b"com.apple.streamingkeydelivery",
    b"com.widevine",
    b"com.microsoft.playready",
    b"edef8ba9-79d6-4ace-a3c8-27dcd51d21ed",  # Widevine (hex text form)
    b"9a04f079-9840-4286-ab92-e65be0885f95",  # PlayReady (hex text form)
)
_DRM_URL_SUFFIXES = (".ism", ".isml", ".ismv")  # Smooth Streaming (PlayReady)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------
@dataclass
class RemoteResult:
    """A completed acquisition. ``local_path`` is untrusted local media."""

    local_path: str
    temp_dir: str
    redacted_url: str
    adapter: str  # "direct" | "ytdlp"
    bytes_downloaded: int
    retained: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_public(self) -> dict[str, Any]:
        """Additive structured-result block (FR-023). Contains only a redacted URL."""
        return {
            "url": self.redacted_url,
            "adapter": self.adapter,
            "bytesDownloaded": self.bytes_downloaded,
            "retained": self.retained,
            "path": self.local_path if self.retained else None,
        }


# ---------------------------------------------------------------------------
# Redaction (SEC-015)
# ---------------------------------------------------------------------------
def redact_url(url: str) -> str:
    """Strip userinfo and the entire query/fragment, keeping scheme/host/path.

    The single redaction rule applied to any source URL echoed anywhere. A
    signed-URL token (in the query) and any embedded ``user:pass@`` credentials
    never survive.
    """
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return "<redacted-url>"
    if not parts.scheme:
        return url
    host = parts.hostname or ""
    try:
        port = parts.port
    except ValueError:
        port = None
    port_str = f":{port}" if port else ""
    return f"{parts.scheme.lower()}://{host}{port_str}{parts.path}"


# ---------------------------------------------------------------------------
# Enablement gate (FR-018)
# ---------------------------------------------------------------------------
def remote_permitted(remote_sources: str, allow_remote: bool) -> bool:
    """Return whether remote acquisition is permitted for this invocation.

    ``--allow-remote`` overrides a disabled or ask configuration. "ask" is a
    skill-layer value; the engine treats it as disabled unless the agent supplied
    --allow-remote after obtaining approval (section 19.6).
    """
    if allow_remote:
        return True
    return remote_sources == "enabled"


def ensure_remote_permitted(url: str, remote_sources: str, allow_remote: bool) -> None:
    """Raise REMOTE_DISABLED (exit 8) if remote acquisition is not permitted.

    Performs NO network access -- this is the outermost gate (spec section 12.8).
    """
    if not remote_permitted(remote_sources, allow_remote):
        raise errors.EngineError(
            errors.REMOTE_DISABLED,
            "A remote URL was supplied but remote source acquisition is disabled. "
            f"Source: {redact_url(url)}",
            exit_code=errors.EXIT_PERMISSION,
            status=errors.STATUS_REMOTE_DISABLED,
            stage="remote",
            remediation=(
                "Set remoteSources to 'enabled' in .video-to-gif.json, or pass "
                "--allow-remote for a single run after confirming a lawful basis."
            ),
        )


# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------
def _unsupported_scheme(
    scheme: str, redacted: str, *, http_hint: bool = False
) -> errors.EngineError:
    remediation = (
        "Pass --allow-insecure-http to permit an unencrypted http download, or use https."
        if http_hint
        else "Only https (and, with acknowledgment, http) URLs are supported."
    )
    return errors.EngineError(
        errors.UNSUPPORTED_URL_SCHEME,
        f"Unsupported URL scheme {scheme!r} for a remote source: {redacted}",
        exit_code=errors.EXIT_INVALID_MEDIA,
        status=errors.STATUS_FAILED,
        stage="remote",
        remediation=remediation,
    )


def _drm_error(redacted: str) -> errors.EngineError:
    return errors.EngineError(
        errors.DRM_PROTECTED,
        f"The remote source appears to be DRM-protected or access-controlled: {redacted}",
        exit_code=errors.EXIT_INVALID_MEDIA,
        status=errors.STATUS_FAILED,
        stage="remote",
        remediation="The engine does not bypass DRM or access controls; use a source you may copy.",
    )


def _private_blocked(redacted: str, address: str) -> errors.EngineError:
    return errors.EngineError(
        errors.PRIVATE_NETWORK_BLOCKED,
        f"The remote source resolved to a blocked private/loopback/metadata address: {redacted}",
        exit_code=errors.EXIT_PERMISSION,
        status=errors.STATUS_FAILED,
        stage="remote",
        remediation="Refusing to fetch private-network addresses (SSRF protection, SEC-014).",
        details={"blockedAddress": address},
    )


def _download_failed(redacted: str, detail: str) -> errors.EngineError:
    return errors.EngineError(
        errors.REMOTE_DOWNLOAD_FAILED,
        f"Remote download failed ({detail}): {redacted}",
        exit_code=errors.EXIT_REMOTE_FAILURE,
        status=errors.STATUS_FAILED,
        stage="download",
        remediation="Check the URL is reachable and returns a direct media file.",
    )


def _too_large(redacted: str, limit: int) -> errors.EngineError:
    return errors.EngineError(
        errors.REMOTE_TOO_LARGE,
        f"Remote download exceeded the {limit}-byte size ceiling: {redacted}",
        exit_code=errors.EXIT_RESOURCE_LIMIT,
        status=errors.STATUS_FAILED,
        stage="download",
        remediation="Increase limits.maxDownloadBytes or use a smaller source.",
    )


def _insufficient_disk(redacted: str, need: int, free: int) -> errors.EngineError:
    return errors.EngineError(
        errors.RESOURCE_LIMIT_EXCEEDED,
        f"Insufficient free disk for the projected download ({need} bytes needed, "
        f"{free} free): {redacted}",
        exit_code=errors.EXIT_RESOURCE_LIMIT,
        status=errors.STATUS_FAILED,
        stage="download",
        remediation="Free up temporary disk space or use a smaller source.",
    )


# ---------------------------------------------------------------------------
# Scheme allowlist (SEC-013)
# ---------------------------------------------------------------------------
def _validate_scheme(scheme: str, allow_insecure_http: bool, redacted: str) -> str | None:
    """Validate a URL scheme. Returns an optional warning (for http), else None.

    Raises UNSUPPORTED_URL_SCHEME for anything but https, and for http without an
    explicit acknowledgment.
    """
    if scheme == "https":
        return None
    if scheme == "http":
        if allow_insecure_http:
            return (
                f"Downloading over unencrypted http ({redacted}); the transfer is "
                "not confidential and is vulnerable to tampering."
            )
        raise _unsupported_scheme(scheme, redacted, http_hint=True)
    raise _unsupported_scheme(scheme, redacted)


# ---------------------------------------------------------------------------
# DRM detection (SEC-017, conservative)
# ---------------------------------------------------------------------------
def detect_drm_bytes(data: bytes) -> bool:
    """Return True if a response prefix carries an unambiguous DRM marker."""
    for marker in _DRM_BYTE_MARKERS:
        if marker in data:
            return True
    lowered = data.lower()
    return any(marker in lowered for marker in _DRM_TEXT_MARKERS)


def url_has_drm_marker(url: str) -> bool:
    """Return True if the URL path names a known DRM manifest type."""
    path = urllib.parse.urlsplit(url).path.lower()
    return path.endswith(_DRM_URL_SUFFIXES)


# ---------------------------------------------------------------------------
# SSRF guard (SEC-014)
# ---------------------------------------------------------------------------
def address_is_disallowed(addr: str) -> bool:
    """Return True if a resolved IP is loopback/private/link-local/etc. or metadata."""
    try:
        ip: Any = ipaddress.ip_address(addr)
    except ValueError:
        return True  # unparseable -> refuse
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    if str(ip) in _METADATA_ADDRESSES:
        return True
    return bool(
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve_and_validate(host: str, port: int, redacted: str, approved: frozenset[str]) -> str:
    """Resolve ``host`` and validate every address (SEC-014). Return a pinned IP.

    Blocks if ANY resolved address is disallowed (unless explicitly approved),
    which also defeats a rebinding record that mixes a public and a private
    address. The returned address is what the caller MUST connect to.
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise _download_failed(redacted, "could not resolve host") from exc
    resolved: list[str] = []
    for info in infos:
        ip = str(info[4][0])
        if ip not in approved and address_is_disallowed(ip):
            raise _private_blocked(redacted, ip)
        resolved.append(ip)
    if not resolved:
        raise _download_failed(redacted, "host did not resolve to any address")
    return resolved[0]


# ---------------------------------------------------------------------------
# Pinned-address HTTP(S) connections (DNS-rebinding resistant)
# ---------------------------------------------------------------------------
class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, *, pinned_ip: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._pinned_ip = pinned_ip

    def connect(self) -> None:
        self.sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        host: str,
        port: int,
        *,
        pinned_ip: str,
        timeout: float,
        context: ssl.SSLContext,
    ) -> None:
        super().__init__(host, port, timeout=timeout, context=context)
        self._pinned_ip = pinned_ip
        self._pinned_context = context

    def connect(self) -> None:
        raw = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        # SNI + certificate validation use the original hostname (self.host),
        # while the socket is pinned to the validated IP (SEC-014).
        self.sock = self._pinned_context.wrap_socket(raw, server_hostname=self.host)


def _open_connection(
    scheme: str, host: str, port: int, pinned_ip: str, timeout: float
) -> http.client.HTTPConnection:
    if scheme == "https":
        return _PinnedHTTPSConnection(
            host,
            port,
            pinned_ip=pinned_ip,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
    return _PinnedHTTPConnection(host, port, pinned_ip=pinned_ip, timeout=timeout)


def _request_target(parts: urllib.parse.SplitResult) -> str:
    # The signed query MUST be sent to the origin even though it is redacted in
    # every echo (SEC-015): the redaction rule is for output, not the request.
    target = parts.path or "/"
    if parts.query:
        target += "?" + parts.query
    return target


# ---------------------------------------------------------------------------
# Free-disk pre-check (FR-021)
# ---------------------------------------------------------------------------
def _check_free_disk(
    temp_dir: str, content_length: int | None, max_bytes: int, redacted: str
) -> None:
    """Refuse the download when free disk is clearly insufficient (FR-021).

    When Content-Length is declared, the projected need is that (capped at the
    size ceiling). When the size is unknown, there is nothing to project against,
    so the streaming ceiling remains the enforced limit; a full-disk temp dir is
    still refused.
    """
    projected = min(content_length, max_bytes) if content_length is not None else None
    try:
        free = free_disk_bytes(temp_dir)
    except OSError:
        return
    if projected is not None:
        if free < projected:
            raise _insufficient_disk(redacted, projected, free)
    elif free <= 0:
        raise _insufficient_disk(redacted, 1, free)


def free_disk_bytes(path: str) -> int:
    """Free bytes on the filesystem holding ``path`` (isolated as a test seam)."""
    return shutil.disk_usage(path).free


# ---------------------------------------------------------------------------
# Filenames
# ---------------------------------------------------------------------------
def _download_filename(parts: urllib.parse.SplitResult) -> str:
    """A safe, fixed basename with a whitelisted extension derived from the URL."""
    ext = os.path.splitext(parts.path)[1].lower()
    if not (2 <= len(ext) <= 6 and ext[1:].isalnum()):
        ext = ""
    return "remote-source" + ext


# ---------------------------------------------------------------------------
# Streaming download (FR-020/FR-021/SEC-016)
# ---------------------------------------------------------------------------
def _content_length(resp: http.client.HTTPResponse) -> int | None:
    raw = resp.getheader("Content-Length")
    if raw is None:
        return None
    try:
        value = int(raw.strip())
    except (ValueError, AttributeError):
        return None
    return value if value >= 0 else None


def _stream_to_file(
    resp: http.client.HTTPResponse,
    dest: str,
    *,
    max_bytes: int,
    max_seconds: float,
    start: float,
    content_length: int | None,
    redacted: str,
    reporter: ProgressReporter,
    cancel_event: threading.Event | None,
) -> int:
    total = 0
    last_emit = 0.0
    checked_drm = False
    with open(dest, "wb") as fh:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise errors.CancelledError(stage="download")
            if time.monotonic() - start > max_seconds:
                raise _download_failed(redacted, "exceeded the download time limit")
            try:
                chunk = resp.read(_CHUNK_SIZE)
            except (TimeoutError, ssl.SSLError, OSError, http.client.HTTPException) as exc:
                raise _download_failed(redacted, "network error during transfer") from exc
            if not chunk:
                break
            if not checked_drm:
                checked_drm = True
                if detect_drm_bytes(chunk):
                    raise _drm_error(redacted)
            total += len(chunk)
            if total > max_bytes:
                raise _too_large(redacted, max_bytes)
            fh.write(chunk)
            now = time.monotonic()
            if now - last_emit >= _PROGRESS_MIN_INTERVAL:
                last_emit = now
                pct = (total / content_length * 100.0) if content_length else None
                reporter.download_progress(total, content_length, pct)
    pct = (total / content_length * 100.0) if content_length else None
    reporter.download_progress(total, content_length, pct)
    if content_length is not None and total < content_length:
        raise _download_failed(redacted, "truncated transfer")
    return total


def _download_direct(
    url: str,
    temp_dir: str,
    *,
    max_bytes: int,
    max_seconds: float,
    allow_insecure_http: bool,
    approved: frozenset[str],
    reporter: ProgressReporter,
    cancel_event: threading.Event | None,
) -> tuple[str, int, list[str]]:
    start = time.monotonic()
    current = url
    warnings: list[str] = []
    redirects = 0
    conn: http.client.HTTPConnection | None = None
    resp: http.client.HTTPResponse | None = None
    try:
        while True:
            parts = urllib.parse.urlsplit(current)
            scheme = (parts.scheme or "").lower()
            hop_redacted = redact_url(current)
            if redirects > 0:
                # Re-enforce scheme allowlist and DRM markers on every hop (SEC-013).
                warn = _validate_scheme(scheme, allow_insecure_http, hop_redacted)
                if warn:
                    warnings.append(warn)
                if url_has_drm_marker(current):
                    raise _drm_error(hop_redacted)
            host = parts.hostname
            if not host:
                raise _download_failed(hop_redacted, "URL has no host")
            port = parts.port or (443 if scheme == "https" else 80)
            pinned_ip = _resolve_and_validate(host, port, hop_redacted, approved)

            remaining = max_seconds - (time.monotonic() - start)
            if remaining <= 0:
                raise _download_failed(hop_redacted, "exceeded the download time limit")
            conn = _open_connection(
                scheme, host, port, pinned_ip, min(remaining, _CONNECT_TIMEOUT_CAP)
            )
            try:
                conn.request(
                    "GET",
                    _request_target(parts),
                    headers={"User-Agent": _USER_AGENT, "Accept": "*/*", "Connection": "close"},
                )
                resp = conn.getresponse()
            except (TimeoutError, ssl.SSLError, OSError, http.client.HTTPException) as exc:
                raise _download_failed(hop_redacted, "could not connect") from exc

            status = resp.status
            if status in _REDIRECT_STATUSES:
                location = resp.getheader("Location")
                with contextlib.suppress(Exception):
                    resp.read()
                resp.close()
                resp = None
                conn.close()
                conn = None
                if not location:
                    raise _download_failed(hop_redacted, "redirect without a Location")
                redirects += 1
                if redirects > _MAX_REDIRECTS:
                    raise _download_failed(hop_redacted, "too many redirects")
                current = urllib.parse.urljoin(current, location)
                continue
            if not (200 <= status < 300):
                raise _download_failed(hop_redacted, f"HTTP status {status}")

            final_redacted = hop_redacted
            content_length = _content_length(resp)
            if content_length is not None and content_length > max_bytes:
                raise _too_large(final_redacted, max_bytes)
            _check_free_disk(temp_dir, content_length, max_bytes, final_redacted)
            dest = os.path.join(temp_dir, _download_filename(parts))
            total = _stream_to_file(
                resp,
                dest,
                max_bytes=max_bytes,
                max_seconds=max_seconds,
                start=start,
                content_length=content_length,
                redacted=final_redacted,
                reporter=reporter,
                cancel_event=cancel_event,
            )
            return dest, total, warnings
    finally:
        if resp is not None:
            with contextlib.suppress(Exception):
                resp.close()
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()


# ---------------------------------------------------------------------------
# yt-dlp adapter (FR-022)
# ---------------------------------------------------------------------------
def _acquire_via_ytdlp(
    url: str,
    redacted: str,
    *,
    max_bytes: int,
    max_seconds: float,
) -> RemoteResult:
    # Detect (never install) the adapter first, so a missing yt-dlp never leaks a
    # temp dir and is reported as YTDLP_MISSING (exit 3) with no acquisition.
    ytdlp = dependencies.require_ytdlp()
    temp_dir = tempfile.mkdtemp(prefix="vtg-remote-")
    out_template = os.path.join(temp_dir, "vtg-remote.%(ext)s")
    # No shell; a fixed argument array. No credential/DRM-circumvention flags.
    cmd = [
        ytdlp,
        "--no-playlist",
        "--no-progress",
        "--no-warnings",
        "--no-continue",
        "--no-part",
        "--restrict-filenames",
        "--max-filesize",
        str(max_bytes),
        "-o",
        out_template,
        url,
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=max_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        cleanup.remove_paths([temp_dir])
        raise _download_failed(redacted, "yt-dlp exceeded the download time limit") from exc
    except OSError as exc:
        cleanup.remove_paths([temp_dir])
        raise _download_failed(redacted, "could not execute yt-dlp") from exc

    if proc.returncode != 0:
        cleanup.remove_paths([temp_dir])
        if _stderr_signals_drm(proc.stderr):
            raise _drm_error(redacted)
        raise _download_failed(redacted, "yt-dlp reported an error")

    produced = sorted(
        os.path.join(temp_dir, name)
        for name in os.listdir(temp_dir)
        if os.path.isfile(os.path.join(temp_dir, name))
    )
    if not produced:
        cleanup.remove_paths([temp_dir])
        raise _download_failed(redacted, "yt-dlp produced no output file")
    dest = produced[0]
    size = os.path.getsize(dest)
    if size > max_bytes:
        cleanup.remove_paths([temp_dir])
        raise _too_large(redacted, max_bytes)
    return RemoteResult(
        local_path=dest,
        temp_dir=temp_dir,
        redacted_url=redacted,
        adapter="ytdlp",
        bytes_downloaded=size,
    )


def _stderr_signals_drm(stderr: str | None) -> bool:
    if not stderr:
        return False
    lowered = stderr.lower()
    return "drm" in lowered or ("protected" in lowered and "content" in lowered)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def acquire_remote_source(
    url: str,
    *,
    adapter: str | None = None,
    allow_insecure_http: bool = False,
    max_download_bytes: int,
    max_download_seconds: int,
    reporter: ProgressReporter = NULL_REPORTER,
    cancel_event: threading.Event | None = None,
    approved_addresses: frozenset[str] = frozenset(),
) -> RemoteResult:
    """Acquire ``url`` to a secure temp dir and return a :class:`RemoteResult`.

    The caller MUST have already passed the enablement gate
    (:func:`ensure_remote_permitted`). On any failure or cancellation the partial
    download is removed (section 16); the returned temp dir is the caller's to
    clean up after the job unless retention was requested.
    """
    redacted = redact_url(url)
    if adapter == "ytdlp":
        return _acquire_via_ytdlp(
            url,
            redacted,
            max_bytes=max_download_bytes,
            max_seconds=float(max_download_seconds),
        )

    # Direct URL. Validate the scheme and URL-level DRM markers BEFORE creating a
    # temp dir so a bad scheme (e.g. file://) never touches the filesystem.
    scheme = (urllib.parse.urlsplit(url).scheme or "").lower()
    warnings: list[str] = []
    warn = _validate_scheme(scheme, allow_insecure_http, redacted)
    if warn:
        warnings.append(warn)
    if url_has_drm_marker(url):
        raise _drm_error(redacted)

    temp_dir = tempfile.mkdtemp(prefix="vtg-remote-")
    try:
        dest, total, dl_warnings = _download_direct(
            url,
            temp_dir,
            max_bytes=max_download_bytes,
            max_seconds=float(max_download_seconds),
            allow_insecure_http=allow_insecure_http,
            approved=approved_addresses,
            reporter=reporter,
            cancel_event=cancel_event,
        )
    except BaseException:
        # Any failure or cancellation removes the partial download (section 16).
        cleanup.remove_paths([temp_dir])
        raise
    warnings.extend(dl_warnings)
    return RemoteResult(
        local_path=dest,
        temp_dir=temp_dir,
        redacted_url=redacted,
        adapter="direct",
        bytes_downloaded=total,
        warnings=list(dict.fromkeys(warnings)),
    )
