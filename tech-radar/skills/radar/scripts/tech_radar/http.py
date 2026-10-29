"""Egress-allowlisted HTTPS client with peer-IP checks, ETag caching, and replay/recording."""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import socket
import sqlite3
import ssl
import urllib.parse
import zlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from . import db
from .errors import EgressDenied, FetchError

VISIBILITY_HOSTS: frozenset[str] = frozenset(
    {
        "pypi.org",
        "registry.npmjs.org",
        "crates.io",
    }
)

EGRESS_ALLOWLIST: frozenset[str] = frozenset(
    {
        "ampcode.com",
        "api.deps.dev",
        "api.github.com",
        "api.npmjs.org",
        "api.osv.dev",
        "api.producthunt.com",
        "awesomeclaudeplugins.com",
        "blog.google",
        "blog.pypi.org",
        "changelog.com",
        "console.dev",
        "crates.io",
        "cursor.com",
        "dev.to",
        "devhunt.org",
        "docs.anthropic.com",
        "endoflife.date",
        "github.blog",
        "github.com",
        "hn.algolia.com",
        "javascriptweekly.com",
        "latent.space",
        "lobste.rs",
        "models.dev",
        "nodeweekly.com",
        "openai.com",
        "openrouter.ai",
        "packages.ecosyste.ms",
        "pycoders.com",
        "pypi.org",
        "pypistats.org",
        "registry.modelcontextprotocol.io",
        "registry.npmjs.org",
        "securitylabs.datadoghq.com",
        "simonwillison.net",
        "skills.sh",
        "snyk.io",
        "socket.dev",
        "terminaltrove.com",
        "this-week-in-rust.org",
        "tldr.tech",
        "www.anthropic.com",
        "www.cisa.gov",
        "www.claudepluginhub.com",
        "www.latent.space",
        "www.skills.sh",
        "www.stepsecurity.io",
        "www.wiz.io",
    }
)

POST_ALLOWED: frozenset[str] = frozenset({"api.osv.dev", "api.producthunt.com"})

MAX_BODY_BYTES = 20 * 1024 * 1024
MAX_GZIP_RATIO = 100
CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 30.0
MAX_REDIRECTS = 5

_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")
_SENSITIVE_HEADER_SUBSTRINGS = ("authorization", "cookie", "token", "secret", "api-key", "apikey")


@dataclass(frozen=True)
class HttpResponse:
    """Validated HTTP response."""

    status: int
    headers: dict[str, str]
    body: bytes
    url: str
    from_cache: bool = False


class Transport(Protocol):
    """Pluggable HTTP transport interface."""

    def request(
        self,
        method: str,
        url: str,
        body: bytes | None,
        headers: Mapping[str, str],
    ) -> tuple[int, dict[str, str], bytes]:
        """Execute a single HTTP hop without following redirects."""
        ...


def replay_key(method: str, url: str, body: bytes | None = None) -> str:
    """Compute the deterministic Spec 11 §1 replay/cache SHA-1 key."""
    norm_method = method.upper()
    body_part = (" " + hashlib.sha1(body).hexdigest()) if body else ""
    raw = f"{norm_method} {url}{body_part}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def validate_ip(ip_str: str) -> None:
    """Reject private, loopback, link-local, CGNAT, multicast, unspecified, or reserved IPs."""
    cleaned = ip_str.split("%", 1)[0].strip("[]")
    try:
        addr = ipaddress.ip_address(cleaned)
    except ValueError as exc:
        raise EgressDenied(f"invalid IP address {ip_str!r}") from exc

    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped

    is_cgnat = isinstance(addr, ipaddress.IPv4Address) and addr in _CGNAT_NET
    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_unspecified
        or addr.is_reserved
        or is_cgnat
    ):
        raise EgressDenied(f"disallowed peer IP address: {ip_str}")


def _validate_url_and_method(url: str, method: str) -> tuple[urllib.parse.SplitResult, str]:
    norm_method = method.upper()
    if norm_method not in ("GET", "POST"):
        raise EgressDenied(f"disallowed HTTP method: {norm_method}")

    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() != "https":
        raise EgressDenied(f"disallowed URL scheme {parsed.scheme!r}; only https is permitted")
    if parsed.username is not None or parsed.password is not None:
        raise EgressDenied("URL userinfo is not permitted")

    host = (parsed.hostname or "").lower()
    if not host or host not in EGRESS_ALLOWLIST:
        raise EgressDenied(f"host {host!r} is not in EGRESS_ALLOWLIST")

    if norm_method == "POST" and host not in POST_ALLOWED:
        raise EgressDenied(f"POST is not permitted for host {host!r}")

    return parsed, host


def _decompress_gzip_bounded(data: bytes) -> bytes:
    if not data:
        return b""
    compressed_len = max(len(data), 1)
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    chunks: list[bytes] = []
    total = 0
    max_allowed = min(MAX_BODY_BYTES, compressed_len * MAX_GZIP_RATIO)

    offset = 0
    step = 65536
    try:
        while offset < len(data):
            piece = data[offset : offset + step]
            offset += step
            out = decompressor.decompress(piece, max_allowed + 1 - total)
            total += len(out)
            if total > MAX_BODY_BYTES:
                raise FetchError("decompressed response exceeds 20 MB cap")
            if total > compressed_len * MAX_GZIP_RATIO:
                raise FetchError("gzip decompression ratio exceeds 100x cap")
            chunks.append(out)
            if decompressor.unconsumed_tail:
                raise FetchError("decompressed response exceeds size/ratio cap")
        tail = decompressor.flush()
        total += len(tail)
        if total > MAX_BODY_BYTES or total > compressed_len * MAX_GZIP_RATIO:
            raise FetchError("decompressed response exceeds size/ratio cap")
        chunks.append(tail)
    except zlib.error as exc:
        raise FetchError(f"invalid gzip response body: {exc}") from exc

    return b"".join(chunks)


def _enforce_body_caps(body: bytes, headers: Mapping[str, str]) -> bytes:
    if len(body) > MAX_BODY_BYTES:
        raise FetchError(f"response body size {len(body)} exceeds 20 MB cap")
    encoding = ""
    for k, v in headers.items():
        if k.lower() == "content-encoding":
            encoding = v.lower().strip()
            break
    if encoding in ("gzip", "x-gzip"):
        return _decompress_gzip_bounded(body)
    return body


def _default_resolver(host: str, port: int) -> Sequence[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchError(f"DNS resolution failed for {host}: {exc}") from exc
    ips: list[str] = []
    for info in infos:
        sockaddr = info[4]
        if sockaddr and isinstance(sockaddr[0], str):
            ips.append(sockaddr[0])
    if not ips:
        raise FetchError(f"DNS resolution returned no addresses for {host}")
    return ips


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection that validates connected socket peer IP to prevent DNS rebinding."""

    def __init__(
        self,
        host: str,
        port: int = 443,
        *,
        connect_timeout: float = CONNECT_TIMEOUT,
        read_timeout: float = READ_TIMEOUT,
        connect_factory: Callable[[tuple[str, int], float], socket.socket] | None = None,
    ) -> None:
        ssl_ctx = ssl.create_default_context()
        super().__init__(
            host,
            port=port,
            timeout=connect_timeout,
            context=ssl_ctx,
        )
        self._ssl_context = ssl_ctx
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._connect_factory = connect_factory

    def connect(self) -> None:
        if self._connect_factory is not None:
            raw_sock = self._connect_factory((self.host, self.port), self._connect_timeout)
        else:
            raw_sock = socket.create_connection((self.host, self.port), self._connect_timeout)
        try:
            peer = raw_sock.getpeername()
            peer_ip = peer[0] if isinstance(peer, tuple) and peer else ""
            validate_ip(str(peer_ip))
            raw_sock.settimeout(self._read_timeout)
            self.sock = self._ssl_context.wrap_socket(raw_sock, server_hostname=self.host)
            wrapped_peer = self.sock.getpeername()
            if isinstance(wrapped_peer, tuple) and wrapped_peer:
                validate_ip(str(wrapped_peer[0]))
        except Exception:
            raw_sock.close()
            raise


class LiveTransport:
    """Live HTTPS transport with DNS and connected-peer IP validation."""

    def __init__(
        self,
        *,
        resolver: Callable[[str, int], Sequence[str]] | None = None,
        connect: Callable[[tuple[str, int], float], socket.socket] | None = None,
        connect_timeout: float = CONNECT_TIMEOUT,
        read_timeout: float = READ_TIMEOUT,
    ) -> None:
        self._resolver = resolver or _default_resolver
        self._connect = connect
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout

    def request(
        self,
        method: str,
        url: str,
        body: bytes | None,
        headers: Mapping[str, str],
    ) -> tuple[int, dict[str, str], bytes]:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").lower()
        port = parsed.port or 443
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        resolved_ips = self._resolver(host, port)
        for ip_str in resolved_ips:
            validate_ip(ip_str)

        conn = _PinnedHTTPSConnection(
            host,
            port=port,
            connect_timeout=self._connect_timeout,
            read_timeout=self._read_timeout,
            connect_factory=self._connect,
        )
        req_headers = {"User-Agent": "tech-radar/0.1.0", **dict(headers)}
        try:
            conn.request(method, path, body=body, headers=req_headers)
            resp = conn.getresponse()
            resp_headers = {k.lower(): v for k, v in resp.getheaders()}
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BODY_BYTES:
                    raise FetchError(f"response body exceeds {MAX_BODY_BYTES} byte cap")
                chunks.append(chunk)
            return int(resp.status), resp_headers, b"".join(chunks)
        except (EgressDenied, FetchError):
            raise
        except TimeoutError as exc:
            raise FetchError(f"HTTP request timed out for {url}") from exc
        except OSError as exc:
            raise FetchError(f"HTTP transport error for {url}: {exc}") from exc
        finally:
            conn.close()


def _extract_osv_tuples(raw_queries: Any) -> list[tuple[str, str, str]] | None:
    if not isinstance(raw_queries, list):
        return None
    tuples: list[tuple[str, str, str]] = []
    for item in raw_queries:
        if (
            isinstance(item, list | tuple)
            and len(item) == 3
            and all(isinstance(x, str) for x in item)
        ):
            tuples.append((item[0], item[1], item[2]))
        elif isinstance(item, dict):
            pkg = item.get("package")
            if not isinstance(pkg, dict):
                return None
            eco = str(pkg.get("ecosystem") or "")
            name = str(pkg.get("name") or "")
            ver = str(item.get("version") or "")
            if not eco or not name:
                return None
            tuples.append((eco, name, ver))
        else:
            return None
    return tuples


@dataclass
class ReplayHttp:
    """Offline replay HTTP transport with OSV querybatch semantic subset matching."""

    roots: tuple[Path, ...]
    calls: list[tuple[str, str, str | None]] = field(default_factory=list)

    def __init__(self, roots: Sequence[Path | str]) -> None:
        self.roots = tuple(Path(r) for r in roots)
        self.calls = []

    def _find_fixture_by_key(self, key: str) -> Path | None:
        target_name = f"{key}.json"
        for root in self.roots:
            if not root.exists():
                continue
            direct = root / target_name
            if direct.is_file():
                return direct
            for candidate in sorted(root.rglob(target_name)):
                if candidate.is_file():
                    return candidate
        return None

    def _match_osv_querybatch(self, body: bytes | None) -> tuple[int, dict[str, str], bytes] | None:
        if not body:
            return None
        try:
            req_obj = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(req_obj, dict):
            return None
        req_tuples = _extract_osv_tuples(req_obj.get("queries"))
        if not req_tuples:
            return None
        req_set = set(req_tuples)

        best_match: tuple[int, Path, dict[str, Any], list[tuple[str, str, str]]] | None = None
        for root in self.roots:
            if not root.exists():
                continue
            for meta_path in sorted(root.rglob("*.json")):
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(meta, dict):
                    continue
                declared = _extract_osv_tuples(meta.get("queries") or meta.get("osv_queries"))
                if not declared:
                    continue
                decl_set = set(declared)
                if req_set <= decl_set:
                    cand = (len(decl_set), meta_path, meta, declared)
                    if best_match is None or cand[0] < best_match[0]:
                        best_match = cand

        if best_match is None:
            return None

        _size, meta_path, meta, declared = best_match
        status, headers, raw_body = self._load_meta_response(meta_path, meta)
        try:
            body_obj = json.loads(raw_body.decode("utf-8"))
            if (
                isinstance(body_obj, dict)
                and isinstance(body_obj.get("results"), list)
                and len(body_obj["results"]) == len(declared)
                and len(req_tuples) != len(declared)
            ):
                by_tuple = dict(zip(declared, body_obj["results"], strict=False))
                sliced = {"results": [by_tuple[t] for t in req_tuples]}
                raw_body = json.dumps(sliced).encode("utf-8")
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError):
            pass
        return status, headers, raw_body

    def _load_meta_response(
        self, meta_path: Path, meta: dict[str, Any]
    ) -> tuple[int, dict[str, str], bytes]:
        if "error" in meta and meta["error"]:
            raise FetchError(f"replay error fixture: {meta['error']}")
        status = int(meta.get("status", 200))
        raw_headers = meta.get("headers") or {}
        headers = {str(k).lower(): str(v) for k, v in raw_headers.items()}
        body_file = meta.get("body_file")
        if body_file:
            body_path = meta_path.parent / str(body_file)
            body = body_path.read_bytes() if body_path.is_file() else b""
        elif "body" in meta:
            val = meta["body"]
            body = val.encode("utf-8") if isinstance(val, str) else json.dumps(val).encode("utf-8")
        else:
            body = b""
        return status, headers, body

    def request(
        self,
        method: str,
        url: str,
        body: bytes | None,
        headers: Mapping[str, str],
    ) -> tuple[int, dict[str, str], bytes]:
        _ = headers
        norm_method = method.upper()
        body_sha = hashlib.sha1(body).hexdigest() if body else None
        self.calls.append((norm_method, url, body_sha))

        key = replay_key(norm_method, url, body)
        meta_path = self._find_fixture_by_key(key)
        if meta_path is not None:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            return self._load_meta_response(meta_path, meta)

        if norm_method == "POST" and url == "https://api.osv.dev/v1/querybatch":
            matched = self._match_osv_querybatch(body)
            if matched is not None:
                return matched
            raise FetchError("no matching OSV querybatch replay fixture")

        raise FetchError(f"missing HTTP replay fixture {key} for {norm_method} {url}")


class RecordingHttp:
    """Wrapper transport that records responses into a fixture directory with secrets stripped."""

    def __init__(self, target_dir: Path | str, inner: Transport | None = None) -> None:
        self.target_dir = Path(target_dir)
        self.inner = inner or LiveTransport()

    @staticmethod
    def _sanitize_headers(headers: Mapping[str, str]) -> dict[str, str]:
        clean_headers: dict[str, str] = {}
        for k, v in headers.items():
            k_low = k.lower()
            if any(sub in k_low for sub in _SENSITIVE_HEADER_SUBSTRINGS):
                continue
            clean_headers[k_low] = v
        return clean_headers

    def request(
        self,
        method: str,
        url: str,
        body: bytes | None,
        headers: Mapping[str, str],
    ) -> tuple[int, dict[str, str], bytes]:
        status, resp_headers, resp_body = self.inner.request(method, url, body, headers)
        self.target_dir.mkdir(parents=True, exist_ok=True)
        key = replay_key(method, url, body)
        body_filename = f"{key}.body"
        (self.target_dir / body_filename).write_bytes(resp_body)
        meta: dict[str, Any] = {
            "status": status,
            "headers": self._sanitize_headers(resp_headers),
            "body_file": body_filename,
        }
        (self.target_dir / f"{key}.json").write_text(
            json.dumps(meta, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return status, resp_headers, resp_body


def _parse_iso(ts: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    except ValueError:
        return None


def fetch(
    spec_or_url: str | Any,
    *,
    method: str = "GET",
    body: bytes | str | None = None,
    headers: Mapping[str, str] | None = None,
    transport: Transport | None = None,
    cache_conn: sqlite3.Connection | None = None,
    ttl_hours: int = 24,
    now: datetime | str | None = None,
) -> HttpResponse:
    """Validate URL/method/redirects/size and fetch over the given or default transport."""
    url = spec_or_url if isinstance(spec_or_url, str) else str(getattr(spec_or_url, "url", ""))
    if isinstance(body, str):
        req_body: bytes | None = body.encode("utf-8")
    elif body is not None:
        req_body = bytes(body)
    else:
        req_body = None

    active_transport: Transport = transport if transport is not None else LiveTransport()
    req_headers: dict[str, str] = dict(headers) if headers else {}

    if now is None:
        now_dt = datetime.now(UTC)
    elif isinstance(now, str):
        now_dt = _parse_iso(now) or datetime.now(UTC)
    else:
        now_dt = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    now_iso = now_dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    current_url = url
    current_method = method.upper()
    current_body = req_body

    _validate_url_and_method(current_url, current_method)

    cache_key = replay_key(current_method, current_url, current_body)
    cached_row: dict[str, Any] | None = None
    if current_method == "GET" and cache_conn is not None:
        cached_row = db.cache_get(cache_conn, cache_key)
        if cached_row is not None and ttl_hours > 0:
            fetched_dt = _parse_iso(cached_row["fetched_at"])
            if fetched_dt is not None and (now_dt - fetched_dt) < timedelta(hours=ttl_hours):
                return HttpResponse(
                    status=cached_row["status"],
                    headers={
                        k: v
                        for k, v in (
                            ("etag", cached_row.get("etag")),
                            ("last-modified", cached_row.get("last_modified")),
                        )
                        if v
                    },
                    body=cached_row["body"],
                    url=current_url,
                    from_cache=True,
                )
        if cached_row is not None:
            if cached_row.get("etag") and "If-None-Match" not in req_headers:
                req_headers["If-None-Match"] = str(cached_row["etag"])
            if cached_row.get("last_modified") and "If-Modified-Since" not in req_headers:
                req_headers["If-Modified-Since"] = str(cached_row["last_modified"])

    for hop in range(MAX_REDIRECTS + 1):
        _validate_url_and_method(current_url, current_method)
        status, resp_headers, raw_resp_body = active_transport.request(
            current_method,
            current_url,
            current_body,
            req_headers,
        )
        norm_headers = {k.lower(): v for k, v in resp_headers.items()}

        if status in (301, 302, 303, 307, 308):
            if hop >= MAX_REDIRECTS:
                raise FetchError(f"redirect limit ({MAX_REDIRECTS}) exceeded for {url}")
            location = norm_headers.get("location")
            if not location:
                raise FetchError(f"redirect status {status} missing Location header")
            next_url = urllib.parse.urljoin(current_url, location)
            if status in (301, 302, 303) and current_method == "POST":
                current_method = "GET"
                current_body = None
            _validate_url_and_method(next_url, current_method)
            current_url = next_url
            continue

        if status == 304 and cached_row is not None and cache_conn is not None:
            db.cache_put(
                cache_conn,
                key=cache_key,
                url=current_url,
                etag=cached_row.get("etag"),
                last_modified=cached_row.get("last_modified"),
                fetched_at=now_iso,
                status=cached_row["status"],
                body=cached_row["body"],
            )
            return HttpResponse(
                status=cached_row["status"],
                headers=norm_headers,
                body=cached_row["body"],
                url=current_url,
                from_cache=True,
            )

        decoded_body = _enforce_body_caps(raw_resp_body, norm_headers)

        if status >= 400:
            raise FetchError(f"HTTP {status} for {current_url}", status=status)

        if current_method == "GET" and cache_conn is not None and status == 200:
            db.cache_put(
                cache_conn,
                key=cache_key,
                url=current_url,
                etag=norm_headers.get("etag"),
                last_modified=norm_headers.get("last-modified"),
                fetched_at=now_iso,
                status=status,
                body=decoded_body,
            )

        return HttpResponse(
            status=status,
            headers=norm_headers,
            body=decoded_body,
            url=current_url,
            from_cache=False,
        )

    raise FetchError(f"redirect limit ({MAX_REDIRECTS}) exceeded for {url}")
