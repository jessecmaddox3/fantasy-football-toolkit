import json
import os
import time

import httpx
import pytest

from ff import cache


def counting_transport(payload, calls):
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


def test_fetch_json_returns_parsed_body_and_writes_cache_file(tmp_cache_dir):
    calls = []
    client = httpx.Client(transport=counting_transport({"a": 1}, calls))

    got = cache.fetch_json("https://x.test/thing", key="thing", ttl=60, client=client)

    assert got == {"a": 1}
    assert json.loads((tmp_cache_dir / "thing.json").read_text()) == {"a": 1}


def test_second_call_inside_ttl_does_not_hit_network(tmp_cache_dir):
    calls = []
    client = httpx.Client(transport=counting_transport({"a": 1}, calls))

    cache.fetch_json("https://x.test/thing", key="thing", ttl=60, client=client)
    got = cache.fetch_json("https://x.test/thing", key="thing", ttl=60, client=client)

    assert got == {"a": 1}
    assert len(calls) == 1


def test_expired_cache_is_refetched(tmp_cache_dir):
    calls = []
    client = httpx.Client(transport=counting_transport({"a": 1}, calls))

    cache.fetch_json("https://x.test/thing", key="thing", ttl=60, client=client)
    stale = time.time() - 3600
    os.utime(tmp_cache_dir / "thing.json", (stale, stale))
    cache.fetch_json("https://x.test/thing", key="thing", ttl=60, client=client)

    assert len(calls) == 2


def test_ttl_zero_never_caches(tmp_cache_dir):
    calls = []
    client = httpx.Client(transport=counting_transport({"a": 1}, calls))

    cache.fetch_json("https://x.test/live", key="live", ttl=0, client=client)
    cache.fetch_json("https://x.test/live", key="live", ttl=0, client=client)

    assert len(calls) == 2
    assert not (tmp_cache_dir / "live.json").exists()


def test_fetch_text_caches_raw_body(tmp_cache_dir):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text="a,b\n1,2\n")

    client = httpx.Client(transport=httpx.MockTransport(handler))

    first = cache.fetch_text("https://x.test/f.csv", key="f", ttl=60, client=client)
    second = cache.fetch_text("https://x.test/f.csv", key="f", ttl=60, client=client)

    assert first == second == "a,b\n1,2\n"
    assert len(calls) == 1
    assert (tmp_cache_dir / "f.txt").read_text() == "a,b\n1,2\n"


def test_http_error_raises(tmp_cache_dir):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))

    with pytest.raises(httpx.HTTPStatusError):
        cache.fetch_json("https://x.test/missing", key="missing", ttl=60, client=client)


def test_cache_replacement_leaves_no_temporary_files_and_is_private(tmp_path):
    import os
    import stat
    from ff.cache import _write
    target = tmp_path / 'cache.json'
    _write(target, '{"value":1}')
    _write(target, '{"value":2}')
    assert target.read_text() == '{"value":2}'
    assert list(tmp_path.iterdir()) == [target]
    if os.name == 'posix':
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
