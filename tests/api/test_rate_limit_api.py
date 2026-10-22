"""接口测试：创建短链限流 —— 测试设计文档 §3.4

覆盖：连续请求超过阈值返回 429、窗口滑动后恢复可访问。
"""

from freezegun import freeze_time

from app.core.config import settings


def test_over_threshold_returns_429(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_max", 3)
    payload = {"origin_url": "https://example.com/rl"}

    for _ in range(3):
        resp = client.post("/api/v1/links", json=payload, headers=auth_headers)
        assert resp.status_code == 201

    resp = client.post("/api/v1/links", json=payload, headers=auth_headers)
    assert resp.status_code == 429
    assert resp.json()["code"] == 429
    assert resp.headers.get("Retry-After") == str(settings.rate_limit_window)


def test_window_slide_recovers(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_max", 3)
    payload = {"origin_url": "https://example.com/win"}

    # 从当前真实时间开始冻结（保证 auth_headers 签发的 Token 有效），
    # 用 tick 推进窗口，而不是把时钟拨到过去
    with freeze_time() as freezer:
        for _ in range(3):
            assert client.post("/api/v1/links", json=payload, headers=auth_headers).status_code == 201

        # 窗口内第 4 次：拒绝
        assert client.post("/api/v1/links", json=payload, headers=auth_headers).status_code == 429

        # 61 秒后窗口滑出：恢复可访问
        freezer.tick(61)
        resp = client.post("/api/v1/links", json=payload, headers=auth_headers)
        assert resp.status_code == 201


def test_rate_limit_only_applies_to_create(client, auth_headers, monkeypatch):
    """限流只作用于创建接口：其他接口不受影响"""
    monkeypatch.setattr(settings, "rate_limit_max", 1)
    assert (
        client.post(
            "/api/v1/links",
            json={"origin_url": "https://example.com/only"},
            headers=auth_headers,
        ).status_code
        == 201
    )
    # 创建已达阈值，但列表接口仍然可用
    resp = client.get("/api/v1/links", headers=auth_headers)
    assert resp.status_code == 200
