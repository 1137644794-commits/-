"""并发与一致性测试 —— 测试设计文档 §5.2

禁用短链后并发跳转：断言不会出现「已禁用但仍命中旧缓存 302」。
先更 DB 再删缓存的一致性保证，所有并发请求要么 403、要么 404，绝不 302。
"""

import asyncio

import httpx
import pytest

from app.main import app as fastapi_app


@pytest.mark.asyncio
async def test_disabled_link_never_302_under_concurrency(client, create_link, auth_headers):
    code = create_link(auth_headers, origin_url="https://example.com/hot", custom_alias="hot001")

    # 预热缓存：正常访问一次，确认缓存已写入
    first = client.get(f"/s/{code}")
    assert first.status_code == 302

    # 禁用：先更 DB 再删缓存
    resp = client.patch(
        f"/api/v1/links/{code}",
        json={"is_disabled": True},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    async def hit():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fastapi_app),
            base_url="http://test",
        ) as ac:
            return await ac.get(f"/s/{code}")

    results = await asyncio.gather(*[hit() for _ in range(50)])

    statuses = {r.status_code for r in results}
    assert 302 not in statuses, "出现了已禁用但仍命中旧缓存 302 的情况"
    assert statuses == {403}

    # 清理：恢复启用，确认缓存恢复后再次可用（一致性闭环）
    resp = client.patch(
        f"/api/v1/links/{code}",
        json={"is_disabled": False},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert client.get(f"/s/{code}").status_code == 302
