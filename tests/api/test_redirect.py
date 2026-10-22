"""接口测试：短链跳转 —— 测试设计文档 §3.3

覆盖：302+Location、过期 410、禁用 403、不存在 404、
空值缓存防穿透（第二次请求不再查库）、缓存命中路径、
跳转接口发消息字段完整（§4.2）。
"""

from types import SimpleNamespace

import pytest
from freezegun import freeze_time

from app.cache.bloom import BloomFilter
from app.cache.redis_client import get_redis
from app.dao import crud


class TestRedirect:
    def test_valid_link_302_with_location(self, client, create_link, auth_headers):
        code = create_link(auth_headers, origin_url="https://example.com/target")
        resp = client.get(f"/s/{code}")
        assert resp.status_code == 302
        assert resp.headers["location"] == "https://example.com/target"

    def test_expired_link_returns_410(self, client, auth_headers):
        # 从当前真实时间冻结（Token 保持有效），创建 60 秒过期的短链后
        # tick 到过期之后，跳转必须返回 410
        with freeze_time() as freezer:
            resp = client.post(
                "/api/v1/links",
                json={
                    "origin_url": "https://example.com/expired",
                    "expire_in_seconds": 60,
                },
                headers=auth_headers,
            )
            assert resp.status_code == 201
            code = resp.json()["short_code"]

            freezer.tick(61)
            resp = client.get(f"/s/{code}")
            assert resp.status_code == 410

    def test_disabled_link_returns_403(self, client, create_link, auth_headers):
        code = create_link(auth_headers)
        resp = client.patch(
            f"/api/v1/links/{code}",
            json={"is_disabled": True},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        resp = client.get(f"/s/{code}")
        assert resp.status_code == 403

    def test_unknown_code_returns_404(self, client):
        resp = client.get("/s/neverexist99")
        assert resp.status_code == 404


class TestCachePenetration:
    """空值缓存防穿透：第二次请求直接命中，不再查库 —— 文档 §3.3.4"""

    @pytest.mark.asyncio
    async def test_404_second_request_hits_empty_cache(self, client, monkeypatch):
        # 布隆过滤器里残留的旧短码（历史存在但库里已删），第一击才会到 DB
        await BloomFilter(get_redis()).add("NOPE01")

        calls = {"n": 0}
        original = crud.get_link_by_code

        async def counting(db, code):
            calls["n"] += 1
            return await original(db, code)

        monkeypatch.setattr("app.dao.crud.get_link_by_code", counting)

        assert client.get("/s/NOPE01").status_code == 404
        assert client.get("/s/NOPE01").status_code == 404

        # 只查了一次 DB：第二次命中空值缓存
        assert calls["n"] == 1
        assert await get_redis().get("link:NOPE01") == "__EMPTY__"

    @pytest.mark.asyncio
    async def test_cache_hit_skips_db(self, client, create_link, auth_headers, monkeypatch):
        code = create_link(auth_headers)
        # 清掉缓存，模拟缓存失效：第一次回源，第二次命中
        await get_redis().delete(f"link:{code}")

        calls = {"n": 0}
        original = crud.get_link_by_code

        async def counting(db, c):
            calls["n"] += 1
            return await original(db, c)

        monkeypatch.setattr("app.dao.crud.get_link_by_code", counting)

        assert client.get(f"/s/{code}").status_code == 302
        assert client.get(f"/s/{code}").status_code == 302
        assert calls["n"] == 1  # 第二次命中缓存，不再查库


class TestAccessLogMessage:
    """跳转接口发出的消息字段完整 —— 文档 §4.2"""

    def test_redirect_pushes_complete_message(self, client, create_link, auth_headers, monkeypatch):
        from unittest.mock import MagicMock

        mock_delay = MagicMock()
        monkeypatch.setattr(
            "app.tasks.access_log.push_access_log",
            SimpleNamespace(delay=mock_delay),
        )
        code = create_link(auth_headers)

        resp = client.get(
            f"/s/{code}",
            headers={"User-Agent": "pytest-ua", "Referer": "https://ref.example.com"},
        )
        assert resp.status_code == 302

        mock_delay.assert_called_once()
        kwargs = mock_delay.call_args.kwargs
        assert kwargs["short_code"] == code
        assert kwargs["ip"] == "testclient"
        assert kwargs["user_agent"] == "pytest-ua"
        assert kwargs["referer"] == "https://ref.example.com"
        assert kwargs["accessed_at"]
