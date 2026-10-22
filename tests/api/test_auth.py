"""接口测试：注册/登录 —— 测试设计文档 §3.1（正常流 + 边界 + 异常）"""

from freezegun import freeze_time


def test_register_and_login_flow(client):
    resp = client.post(
        "/api/v1/auth/register",
        json={"username": "alice", "password": "secret123"},
    )
    assert resp.status_code == 201
    assert resp.json()["message"] == "注册成功"

    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "secret123"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["token_type"] == "bearer"
    assert data["access_token"]
    assert data["refresh_token"]


def test_login_wrong_password_returns_401(client):
    client.post(
        "/api/v1/auth/register",
        json={"username": "alice", "password": "secret123"},
    )
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "wrong-pass"},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == 401


def test_register_duplicate_username_returns_409(client):
    payload = {"username": "bob", "password": "secret123"}
    assert client.post("/api/v1/auth/register", json=payload).status_code == 201
    resp = client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 409
    assert resp.json()["code"] == 409


def test_expired_access_token_returns_401(client):
    with freeze_time("2026-01-01 00:00:00") as freezer:
        client.post(
            "/api/v1/auth/register",
            json={"username": "carol", "password": "secret123"},
        )
        token = client.post(
            "/api/v1/auth/login",
            json={"username": "carol", "password": "secret123"},
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 有效期内可访问
        assert client.get("/api/v1/links", headers=headers).status_code == 200

        # 30 分钟过期后：拒绝
        freezer.move_to("2026-01-01 00:31:00")
        resp = client.get("/api/v1/links", headers=headers)
        assert resp.status_code == 401


def test_logout_blacklists_refresh_token(client):
    client.post(
        "/api/v1/auth/register",
        json={"username": "dave", "password": "secret123"},
    )
    tokens = client.post(
        "/api/v1/auth/login",
        json={"username": "dave", "password": "secret123"},
    ).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    resp = client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": tokens["refresh_token"]},
        headers=headers,
    )
    assert resp.status_code == 200

    # 登出后 Refresh Token 立即失效（黑名单生效）
    resp = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert resp.status_code == 401


def test_refresh_token_rotation(client):
    client.post(
        "/api/v1/auth/register",
        json={"username": "eve", "password": "secret123"},
    )
    tokens = client.post(
        "/api/v1/auth/login",
        json={"username": "eve", "password": "secret123"},
    ).json()

    resp = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert resp.status_code == 200
    new_tokens = resp.json()
    assert new_tokens["access_token"]
    assert new_tokens["refresh_token"] != tokens["refresh_token"]

    # 旧 Refresh Token 被轮换作废
    resp = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert resp.status_code == 401

    # 新 Refresh Token 可用
    resp = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": new_tokens["refresh_token"]},
    )
    assert resp.status_code == 200


def test_unauthorized_without_token(client):
    resp = client.get("/api/v1/links")
    assert resp.status_code == 401
