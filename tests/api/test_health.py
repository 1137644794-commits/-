"""健康检查接口测试：真实探测 MySQL / Redis 组件状态"""

import pytest

import app.main as main_mod


def test_healthz_ok_when_components_up(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["components"] == {"mysql": "ok", "redis": "ok"}


def test_healthz_503_when_redis_down(client, monkeypatch):
    class _BrokenRedis:
        async def ping(self):
            raise ConnectionError("redis down")

    monkeypatch.setattr(main_mod, "get_redis", lambda: _BrokenRedis())

    resp = client.get("/healthz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["components"]["redis"] == "down"
    assert body["components"]["mysql"] == "ok"


def test_healthz_503_when_mysql_down(client, monkeypatch):
    class _BrokenFactory:
        async def __aenter__(self):
            raise ConnectionError("mysql down")

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(main_mod.database, "async_session_factory", lambda: _BrokenFactory())

    resp = client.get("/healthz")
    assert resp.status_code == 503
    assert resp.json()["components"]["mysql"] == "down"


@pytest.mark.asyncio
async def test_redirect_rate_limit_fail_open(monkeypatch):
    """限流器 Redis 故障时 fail-open：不阻断跳转（可用性优先）"""
    from types import SimpleNamespace

    import app.middleware.rate_limit as rl_mod

    def _broken_limiter():
        raise ConnectionError("redis down")

    monkeypatch.setattr(rl_mod, "_get_limiter", _broken_limiter)
    request = SimpleNamespace(client=SimpleNamespace(host="1.2.3.4"))

    # 不抛异常即为放行
    await rl_mod.check_redirect_rate(request)
    await rl_mod.check_create_link_rate(request)
