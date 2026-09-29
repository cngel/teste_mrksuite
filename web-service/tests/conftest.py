import os
from types import SimpleNamespace

os.environ.setdefault("JWT_SECRET", "test-secret")

import httpx
import pytest
from fastapi.testclient import TestClient

import app as app_module


class _Recorder:
    """Substitui httpx.AsyncClient para os testes controlarem a resposta do
    serviço upstream sem contactar nenhum serviço real."""

    def __init__(self):
        self.calls = []
        self.response = SimpleNamespace(
            status_code=200,
            content=b'{"ok":true}',
            headers={"content-type": "application/json"},
        )
        self.raise_connect_error = False

    async def request(self, method, url, params=None, content=None, headers=None):
        self.calls.append({"method": method, "url": url, "params": params, "content": content, "headers": headers})
        if self.raise_connect_error:
            raise httpx.ConnectError("upstream indisponível")
        return self.response


@pytest.fixture
def upstream(monkeypatch):
    recorder = _Recorder()

    class FakeAsyncClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, *a, **kw):
            return await recorder.request(*a, **kw)

    monkeypatch.setattr(app_module.httpx, "AsyncClient", FakeAsyncClient)
    return recorder


@pytest.fixture
def client(upstream):
    return TestClient(app_module.app)
