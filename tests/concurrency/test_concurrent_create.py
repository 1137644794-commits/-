"""并发与一致性测试 —— 测试设计文档 §5.1

asyncio.gather 模拟 100 个并发创建同一别名短链：
断言只有 1 个成功（201），其余 99 个返回 409，
验证唯一索引兜底生效（面试亮点）。
"""

import asyncio
from collections import Counter

import httpx
import pytest

from app.core.config import settings
from app.main import app as fastapi_app


@pytest.mark.asyncio
async def test_100_concurrent_same_alias_only_one_succeeds(client, monkeypatch):
    # 本用例聚焦唯一性约束，测试期间放开限流上限，避免 429 混入结果
    monkeypatch.setattr(settings, "rate_limit_max", 10000)

    # 准备一个已登录用户
    assert (
        client.post(
            "/api/v1/auth/register",
            json={"username": "racer", "password": "secret123"},
        ).status_code
        == 201
    )
    token = client.post(
        "/api/v1/auth/login",
        json={"username": "racer", "password": "secret123"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    async def create_one():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fastapi_app),
            base_url="http://test",
        ) as ac:
            return await ac.post(
                "/api/v1/links",
                json={
                    "origin_url": "https://example.com/race",
                    "custom_alias": "race001",
                },
                headers=headers,
            )

    results = await asyncio.gather(*[create_one() for _ in range(100)])

    counter = Counter(r.status_code for r in results)
    assert counter[201] == 1, f"成功数应为 1，实际分布 {counter}"
    assert counter[409] == 99, f"冲突数应为 99，实际分布 {counter}"

    # 并发风暴后短链仍可正常跳转
    resp = client.get("/s/race001")
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://example.com/race"
