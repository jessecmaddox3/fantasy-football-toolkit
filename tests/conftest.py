import os
import pytest


@pytest.fixture(autouse=True)
def tmp_cache_dir(tmp_path, monkeypatch):
    """Every test gets its own on-disk cache so runs never share state."""
    d = tmp_path / "cache"
    monkeypatch.setenv("FF_CACHE_DIR", str(d))
    yield d


from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
DATA = REPO_ROOT / "data"


def fixture_text(name):
    return (FIXTURES / name).read_text()


def fixture_json(name):
    import json

    return json.loads(fixture_text(name))


class RecordingRoutes:
    """Serves synthetic fixtures by URL substring and records every request."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def handler(self, request):
        self.requests.append(request)
        for needle, payload in self.routes.items():
            if needle in str(request.url):
                if isinstance(payload, str):
                    return httpx.Response(200, text=payload)
                return httpx.Response(200, json=payload)
        raise AssertionError(f"no fixture route for {request.url}")

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handler))


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """Tests must use synthetic HTTP transports, never live provider services."""
    import socket
    def blocked(*args, **kwargs):
        raise AssertionError("Live network access is forbidden in tests")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


@pytest.fixture(autouse=True)
def clean_user_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in ("FF_CONFIG", "FF_ESPN_ENV", "ESPN_S2", "SWID", "FF_OUTPUT_DIR"):
        monkeypatch.delenv(key, raising=False)
