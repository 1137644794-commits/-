"""接口测试：短链创建与管理 —— 测试设计文档 §3.2

覆盖：正常创建、URL 非法 422、自定义别名重复 409、未登录 401、
列表归属隔离、越权操作 403、禁用与删除。
"""

import pytest


class TestCreate:
    def test_create_link_success(self, client, create_link, auth_headers):
        resp = client.post(
            "/api/v1/links",
            json={"origin_url": "https://example.com/page"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["short_code"]
        assert data["origin_url"] == "https://example.com/page"
        assert data["expire_at"] is None
        assert data["is_disabled"] is False
        assert isinstance(data["id"], int)

    def test_create_with_custom_alias_and_expiry(self, client, auth_headers):
        resp = client.post(
            "/api/v1/links",
            json={
                "origin_url": "https://example.com/exp",
                "custom_alias": "custom01",
                "expire_in_seconds": 3600,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["short_code"] == "custom01"
        assert data["expire_at"] is not None

    def test_invalid_url_returns_422(self, client, auth_headers):
        resp = client.post(
            "/api/v1/links",
            json={"origin_url": "not-a-valid-url"},
            headers=auth_headers,
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == 422
        assert body["message"] == "参数校验失败"
        assert body["errors"]

    @pytest.mark.parametrize("alias", ["ab", "a" * 17, "含中文", "with space"])
    def test_invalid_alias_pattern_returns_422(self, client, auth_headers, alias):
        resp = client.post(
            "/api/v1/links",
            json={"origin_url": "https://example.com/x", "custom_alias": alias},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_duplicate_alias_returns_409(self, client, create_link, auth_headers):
        create_link(auth_headers, custom_alias="dupdup1")
        resp = client.post(
            "/api/v1/links",
            json={
                "origin_url": "https://example.com/other",
                "custom_alias": "dupdup1",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == 409

    def test_create_requires_auth(self, client):
        resp = client.post("/api/v1/links", json={"origin_url": "https://example.com/noauth"})
        assert resp.status_code == 401


class TestManage:
    def test_list_only_my_links(self, client, create_link, register_login):
        mine = register_login("owner1")
        others = register_login("owner2")
        create_link({"Authorization": f"Bearer {mine['access_token']}"}, origin_url="https://example.com/mine")
        create_link({"Authorization": f"Bearer {others['access_token']}"}, origin_url="https://example.com/notmine")

        resp = client.get(
            "/api/v1/links",
            headers={"Authorization": f"Bearer {mine['access_token']}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["origin_url"] == "https://example.com/mine"

    def test_patch_other_users_link_returns_403(self, client, create_link, register_login):
        owner = register_login("owner_a")
        intruder = register_login("owner_b")
        code = create_link(
            {"Authorization": f"Bearer {owner['access_token']}"},
            custom_alias="owned01",
        )
        resp = client.patch(
            f"/api/v1/links/{code}",
            json={"is_disabled": True},
            headers={"Authorization": f"Bearer {intruder['access_token']}"},
        )
        assert resp.status_code == 403

    def test_patch_then_delete(self, client, create_link, auth_headers):
        code = create_link(auth_headers)
        resp = client.patch(
            f"/api/v1/links/{code}",
            json={"is_disabled": True},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["is_disabled"] is True

        resp = client.delete(f"/api/v1/links/{code}", headers=auth_headers)
        assert resp.status_code == 204

    def test_patch_unknown_code_returns_404(self, client, auth_headers):
        resp = client.patch(
            "/api/v1/links/unknown001",
            json={"is_disabled": True},
            headers=auth_headers,
        )
        assert resp.status_code == 404
