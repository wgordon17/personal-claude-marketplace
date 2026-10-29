"""Tests for egress-controlled HTTP client, replay/recording, and caching (Task 3 Step 4)."""

from __future__ import annotations

import gzip
import json
from collections.abc import Mapping
from pathlib import Path

import pytest
from tech_radar import db, http
from tech_radar.errors import EgressDenied, FetchError


class _FakeTransport:
    """In-memory fake transport for testing redirect chains, size caps, and caching."""

    def __init__(
        self,
        responses: list[tuple[int, dict[str, str], bytes]],
    ) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str, bytes | None, dict[str, str]]] = []

    def request(
        self,
        method: str,
        url: str,
        body: bytes | None,
        headers: Mapping[str, str],
    ) -> tuple[int, dict[str, str], bytes]:
        self.calls.append((method, url, body, dict(headers)))
        if not self._responses:
            raise AssertionError("No fake responses left")
        return self._responses.pop(0)


def test_non_allowlisted_host_and_schemes_denied() -> None:
    """Non-allowlisted host, http://, file://, userinfo, and POST raise EgressDenied."""
    fake = _FakeTransport([(200, {}, b"ok")])

    with pytest.raises(EgressDenied):
        http.fetch("https://evil.example.com/feed.xml", transport=fake)

    with pytest.raises(EgressDenied):
        http.fetch("http://pypi.org/pypi/litellm/json", transport=fake)

    with pytest.raises(EgressDenied):
        http.fetch("file:///etc/passwd", transport=fake)

    with pytest.raises(EgressDenied):
        http.fetch("https://user:pass@pypi.org/pypi/litellm/json", transport=fake)

    with pytest.raises(EgressDenied):
        http.fetch("https://pypi.org/pypi/litellm/json", method="POST", body=b"{}", transport=fake)

    assert fake.calls == []


@pytest.mark.parametrize(
    "bad_ip",
    [
        "169.254.169.254",
        "127.0.0.1",
        "10.0.0.1",
        "192.168.1.1",
        "100.64.0.1",
        "::1",
        "fe80::1",
        "::ffff:169.254.169.254",
    ],
)
def test_live_transport_rejects_private_loopback_link_local_cgnat_ips(bad_ip: str) -> None:
    """LiveTransport with fake resolver returning non-public IPs raises EgressDenied."""
    live = http.LiveTransport(resolver=lambda _host, _port: [bad_ip])
    with pytest.raises(EgressDenied):
        http.fetch("https://pypi.org/pypi/litellm/json", transport=live)


def test_live_transport_peer_ip_recheck_prevents_dns_rebinding() -> None:
    """LiveTransport checks sock.getpeername() even if DNS resolver returned a public IP."""

    class _FakeReboundSocket:
        def getpeername(self) -> tuple[str, int]:
            return ("169.254.169.254", 443)

        def settimeout(self, _t: float) -> None:
            pass

        def close(self) -> None:
            pass

    live = http.LiveTransport(
        resolver=lambda _host, _port: ["151.101.0.223"],
        connect=lambda _addr, _timeout: _FakeReboundSocket(),  # type: ignore[arg-type,return-value]
    )
    with pytest.raises(EgressDenied):
        http.fetch("https://pypi.org/pypi/litellm/json", transport=live)


def test_redirect_chain_revalidates_each_hop_and_caps_hops() -> None:
    """Redirect chain to bad host/scheme raises EgressDenied; >5 hops raises FetchError."""
    redirect_to_evil = _FakeTransport(
        [
            (302, {"Location": "https://pypi.org/simple/litellm/"}, b""),
            (302, {"Location": "https://169.254.169.254/latest/meta-data/"}, b""),
        ]
    )
    with pytest.raises(EgressDenied):
        http.fetch("https://pypi.org/pypi/litellm/json", transport=redirect_to_evil)

    redirect_to_http = _FakeTransport(
        [
            (301, {"Location": "http://pypi.org/pypi/litellm/json"}, b""),
        ]
    )
    with pytest.raises(EgressDenied):
        http.fetch("https://pypi.org/pypi/litellm/json", transport=redirect_to_http)

    too_many = _FakeTransport(
        [(302, {"Location": f"https://pypi.org/pypi/litellm/json?hop={i}"}, b"") for i in range(7)]
    )
    with pytest.raises(FetchError):
        http.fetch("https://pypi.org/pypi/litellm/json", transport=too_many)


def test_oversize_body_and_gzip_ratio_cap_raise_fetch_error() -> None:
    """25 MB raw body or >100x gzip ratio raises FetchError."""
    oversize_25mb = b"x" * (25 * 1024 * 1024)
    fake = _FakeTransport([(200, {}, oversize_25mb)])
    with pytest.raises(FetchError):
        http.fetch("https://pypi.org/pypi/litellm/json", transport=fake)

    # Highly compressible 500 KB of zeros -> >100x gzip ratio
    raw_zeros = b"\x00" * (500 * 1024)
    compressed = gzip.compress(raw_zeros, compresslevel=9)
    assert len(raw_zeros) > len(compressed) * http.MAX_GZIP_RATIO
    fake_gzip = _FakeTransport([(200, {"Content-Encoding": "gzip"}, compressed)])
    with pytest.raises(FetchError):
        http.fetch("https://pypi.org/pypi/litellm/json", transport=fake_gzip)


def test_replay_http_get_post_and_osv_querybatch_semantic_matching(tmp_path: Path) -> None:
    """ReplayHttp serves GET by key, POST with sha1(body), and OSV querybatch by subset."""
    fix_dir = tmp_path / "http_fixtures"
    fix_dir.mkdir(parents=True)

    # 1. GET fixture
    get_url = "https://pypi.org/pypi/litellm/json"
    get_key = http.replay_key("GET", get_url)
    (fix_dir / f"{get_key}.body").write_bytes(b'{"info":{"name":"litellm"}}')
    (fix_dir / f"{get_key}.json").write_text(
        json.dumps(
            {
                "status": 200,
                "headers": {"content-type": "application/json"},
                "body_file": f"{get_key}.body",
            }
        ),
        encoding="utf-8",
    )

    # 2. POST fixture (ProductHunt GraphQL) whose key includes sha1(body)
    post_url = "https://api.producthunt.com/v2/api/graphql"
    post_body = b'{"query":"{ posts(first: 1) { edges { node { id } } } }"}'
    post_key = http.replay_key("POST", post_url, post_body)
    (fix_dir / f"{post_key}.body").write_bytes(b'{"data":{"posts":{"edges":[]}}}')
    (fix_dir / f"{post_key}.json").write_text(
        json.dumps(
            {
                "status": 200,
                "headers": {"content-type": "application/json"},
                "body_file": f"{post_key}.body",
            }
        ),
        encoding="utf-8",
    )

    # 3. OSV querybatch semantic fixture declaring 2 tuples
    osv_dir = fix_dir / "osv_batch"
    osv_dir.mkdir()
    (osv_dir / "osv_fixture.body").write_bytes(
        json.dumps(
            {
                "results": [
                    {"vulns": []},
                    {"vulns": [{"id": "MAL-2026-9999", "modified": "2026-10-28T00:00:00Z"}]},
                ]
            }
        ).encode("utf-8")
    )
    (osv_dir / "osv_fixture.json").write_text(
        json.dumps(
            {
                "status": 200,
                "headers": {"content-type": "application/json"},
                "body_file": "osv_fixture.body",
                "queries": [
                    ["PyPI", "litellm", "1.106.2"],
                    ["PyPI", "litellm", "1.107.0"],
                ],
            }
        ),
        encoding="utf-8",
    )

    replay = http.ReplayHttp([fix_dir])

    # GET match
    r_get = http.fetch(get_url, transport=replay)
    assert r_get.status == 200
    assert json.loads(r_get.body)["info"]["name"] == "litellm"

    # POST match
    r_post = http.fetch(post_url, method="POST", body=post_body, transport=replay)
    assert r_post.status == 200
    assert json.loads(r_post.body) == {"data": {"posts": {"edges": []}}}

    # OSV subset match (requesting 1 of the 2 declared tuples)
    osv_url = "https://api.osv.dev/v1/querybatch"
    subset_req = json.dumps(
        {"queries": [{"package": {"ecosystem": "PyPI", "name": "litellm"}, "version": "1.107.0"}]}
    ).encode("utf-8")
    r_osv_sub = http.fetch(osv_url, method="POST", body=subset_req, transport=replay)
    assert r_osv_sub.status == 200
    assert json.loads(r_osv_sub.body) == {
        "results": [{"vulns": [{"id": "MAL-2026-9999", "modified": "2026-10-28T00:00:00Z"}]}]
    }

    # OSV superset (includes an undeclared package) is NOT matched
    superset_req = json.dumps(
        {
            "queries": [
                {"package": {"ecosystem": "PyPI", "name": "litellm"}, "version": "1.106.2"},
                {"package": {"ecosystem": "PyPI", "name": "requests"}, "version": "2.32.3"},
            ]
        }
    ).encode("utf-8")
    with pytest.raises(FetchError):
        http.fetch(osv_url, method="POST", body=superset_req, transport=replay)

    assert len(replay.calls) == 4
    assert replay.calls[0] == ("GET", get_url, None)


def test_recording_http_strips_authorization_and_cookies(tmp_path: Path) -> None:
    """RecordingHttp never writes Authorization, Cookie, or token headers into fixture metadata."""
    rec_dir = tmp_path / "recorded"
    fake_inner = _FakeTransport(
        [
            (
                200,
                {
                    "Content-Type": "application/json",
                    "Set-Cookie": "session=secretcookie",
                    "Authorization": "Bearer secret-tok",
                    "X-Api-Key": "secret-api-key",
                    "ETag": '"v1"',
                },
                b'{"ok":true}',
            )
        ]
    )
    recorder = http.RecordingHttp(rec_dir, inner=fake_inner)
    url = "https://pypi.org/pypi/litellm/json"
    http.fetch(
        url,
        headers={"Authorization": "Bearer req-secret", "Cookie": "c=1"},
        transport=recorder,
    )

    key = http.replay_key("GET", url)
    meta_file = rec_dir / f"{key}.json"
    assert meta_file.is_file()
    meta_text = meta_file.read_text(encoding="utf-8")
    meta = json.loads(meta_text)
    assert "authorization" not in meta_text.lower()
    assert "cookie" not in meta_text.lower()
    assert "secret" not in meta_text.lower()
    assert meta["headers"].get("etag") == '"v1"'


def test_second_fetch_with_fresh_etag_uses_cache() -> None:
    """Second fetch with fresh ETag uses http_cache and conditional 304 refreshes cache."""
    cache_conn = db.connect_cache()
    try:
        url = "https://pypi.org/pypi/litellm/json"
        fake = _FakeTransport(
            [
                (200, {"ETag": '"etag-123"', "Content-Type": "application/json"}, b'{"v":1}'),
                (304, {"ETag": '"etag-123"'}, b""),
            ]
        )

        # First fetch populates cache
        first = http.fetch(
            url,
            transport=fake,
            cache_conn=cache_conn,
            ttl_hours=24,
            now="2026-10-28T07:00:00Z",
        )
        assert first.from_cache is False
        assert first.body == b'{"v":1}'
        assert len(fake.calls) == 1

        # Second fetch 1 hour later (within TTL) is served directly from cache
        second = http.fetch(
            url,
            transport=fake,
            cache_conn=cache_conn,
            ttl_hours=24,
            now="2026-10-28T08:00:00Z",
        )
        assert second.from_cache is True
        assert second.body == b'{"v":1}'
        assert len(fake.calls) == 1

        # Third fetch 25 hours later (past TTL) sends If-None-Match and uses cache on 304
        third = http.fetch(
            url,
            transport=fake,
            cache_conn=cache_conn,
            ttl_hours=24,
            now="2026-10-29T09:00:00Z",
        )
        assert third.from_cache is True
        assert third.body == b'{"v":1}'
        assert len(fake.calls) == 2
        assert fake.calls[1][3].get("If-None-Match") == '"etag-123"'
    finally:
        cache_conn.close()


def test_replay_http_error_fixture_forms(tmp_path: Path) -> None:
    """ReplayHttp turns {'error': 'timeout'} and {'status': 503} into FetchError (Spec 11 §1)."""
    fix_dir = tmp_path / "err_fixtures"
    fix_dir.mkdir(parents=True)

    timeout_url = "https://pypi.org/pypi/litellm/json"
    timeout_key = http.replay_key("GET", timeout_url)
    (fix_dir / f"{timeout_key}.json").write_text(
        json.dumps({"error": "timeout"}),
        encoding="utf-8",
    )

    status_url = "https://crates.io/api/v1/summary"
    status_key = http.replay_key("GET", status_url)
    (fix_dir / f"{status_key}.json").write_text(
        json.dumps({"status": 503}),
        encoding="utf-8",
    )

    replay = http.ReplayHttp([fix_dir])
    with pytest.raises(FetchError, match="timeout"):
        http.fetch(timeout_url, transport=replay)

    with pytest.raises(FetchError, match="503"):
        http.fetch(status_url, transport=replay)
