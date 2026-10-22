"""接口测试：PV/UV 统计 —— 测试设计文档 §3.5

覆盖：跳转后 PV +1、同一 IP 重复访问 UV 不重复计数（HyperLogLog）、
不同 IP UV 计数、归属校验 403、按天聚合与 Top N。
"""

from fastapi.testclient import TestClient

from app.main import app as fastapi_app


def test_pv_uv_after_redirect(client, create_link, auth_headers):
    code = create_link(auth_headers)
    assert client.get(f"/s/{code}").status_code == 302

    resp = client.get(f"/api/v1/stats/{code}", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["short_code"] == code
    assert data["pv"] == 1
    assert data["uv"] == 1


def test_uv_dedup_same_ip(client, create_link, auth_headers):
    code = create_link(auth_headers)
    client.get(f"/s/{code}")
    client.get(f"/s/{code}")

    data = client.get(f"/api/v1/stats/{code}", headers=auth_headers).json()
    assert data["pv"] == 2
    assert data["uv"] == 1  # 同一 IP 重复访问，UV 不重复计数


def test_uv_counts_distinct_ips(client, create_link, auth_headers):
    code = create_link(auth_headers)
    client.get(f"/s/{code}")

    # 模拟另一个 IP 的访客（独立 TestClient + 自定义 client 地址）
    with TestClient(fastapi_app, follow_redirects=False, client=("8.8.8.8", 1234)) as other_ip_client:
        other_ip_client.get(f"/s/{code}")
        other_ip_client.get(f"/s/{code}")

    data = client.get(f"/api/v1/stats/{code}", headers=auth_headers).json()
    assert data["pv"] == 3
    assert data["uv"] == 2


def test_daily_series_after_redirect(client, create_link, auth_headers):
    code = create_link(auth_headers)
    client.get(f"/s/{code}")

    resp = client.get(f"/api/v1/stats/{code}/daily", headers=auth_headers)
    assert resp.status_code == 200
    series = resp.json()
    assert len(series) == 1
    assert series[0]["pv"] == 1
    assert series[0]["uv"] == 1


def test_top_returns_hot_links(client, create_link, auth_headers):
    code = create_link(auth_headers)
    client.get(f"/s/{code}")
    client.get(f"/s/{code}")

    resp = client.get("/api/v1/stats/top", headers=auth_headers)
    assert resp.status_code == 200
    top = resp.json()
    assert top
    assert top[0]["short_code"] == code
    assert top[0]["pv"] == 2


def test_stats_other_user_returns_403(client, create_link, register_login):
    owner = register_login("stat_owner")
    other = register_login("stat_other")
    code = create_link(
        {"Authorization": f"Bearer {owner['access_token']}"},
        origin_url="https://example.com/private",
    )

    resp = client.get(
        f"/api/v1/stats/{code}",
        headers={"Authorization": f"Bearer {other['access_token']}"},
    )
    assert resp.status_code == 403


def test_stats_unknown_code_returns_404(client, auth_headers):
    resp = client.get("/api/v1/stats/notexist01", headers=auth_headers)
    assert resp.status_code == 404
