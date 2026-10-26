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


class TestRedirectRateLimit:
    """跳转接口限流：防爬虫刷量/短码枚举 —— 与创建限流独立计数"""

    def test_redirect_over_threshold_returns_429(self, client, create_link, auth_headers, monkeypatch):
        monkeypatch.setattr(settings, "redirect_rate_limit_max", 3)
        code = create_link(auth_headers)

        for _ in range(3):
            assert client.get(f"/s/{code}").status_code == 302

        resp = client.get(f"/s/{code}")
        assert resp.status_code == 429
        assert resp.headers.get("Retry-After") == str(settings.redirect_rate_limit_window)

    def test_redirect_window_slide_recovers(self, client, create_link, auth_headers, monkeypatch):
        """跳转限流窗口滑动后恢复可访问"""
        monkeypatch.setattr(settings, "redirect_rate_limit_max", 2)
        code = create_link(auth_headers)

        with freeze_time() as freezer:
            assert client.get(f"/s/{code}").status_code == 302
            assert client.get(f"/s/{code}").status_code == 302
            assert client.get(f"/s/{code}").status_code == 429

            freezer.tick(61)
            assert client.get(f"/s/{code}").status_code == 302

    def test_redirect_limit_independent_from_create(self, client, create_link, auth_headers, monkeypatch):
        """跳转限流与创建限流互不影响（不同 scope 计数）"""
        monkeypatch.setattr(settings, "redirect_rate_limit_max", 2)
        code = create_link(auth_headers)

        client.get(f"/s/{code}")
        client.get(f"/s/{code}")
        assert client.get(f"/s/{code}").status_code == 429

        # 跳转已被限流，但创建接口不受影响
        resp = client.post(
            "/api/v1/links",
            json={"origin_url": "https://example.com/still-ok"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
