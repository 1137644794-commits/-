"""测试全局配置：真正的"测试框架"核心。

环境隔离策略（对应《测试设计文档》§3 环境隔离）：
1. MySQL  -> SQLite(aiosqlite) 文件库：会话级替换 database 模块的 engine/factory，
   用例间清空 user / short_link / access_log 三张表，保证互不污染；
2. Redis  -> fakeredis（内存假 Redis）：替换 redis_client._pool，celery 缓冲队列
   用同步 FakeStrictRedis，用例间 flushall；
3. Celery -> 强制 eager 模式：任务进程内同步执行，验证消息后 access_log 最终落库。
"""

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

# 必须在导入任何 app 模块前设置：环境变量优先级高于 .env 文件
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# 保证从任何目录运行都能 import app
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app.cache.redis_client as redis_client_mod
import app.dao.database as database_mod
import app.dao.models as models_mod
import app.services.link_service as link_service_mod
import app.tasks.access_log as access_log_mod
import app.tasks.cleanup as cleanup_mod
from app.dao.database import Base
from app.main import app

_TMP_DIR = tempfile.mkdtemp(prefix="shortlink_pytest_")


async def _create_tables(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.fixture(scope="session", autouse=True)
def _patch_runtime():
    """会话级替换运行环境：SQLite 引擎 + fakeredis，退出时还原"""
    # SQLite 文件库 + 30s busy timeout；不用 WAL：SELECT 后升级写锁的场景
    # 在 rollback journal 模式下会等待而不是直接 SQLITE_BUSY_SNAPSHOT。
    # NullPool：每会话独立连接，规避 Celery 落库子线程新事件循环复用连接
    # 导致的跨循环问题（aiosqlite 连接绑定创建它的循环）。
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{_TMP_DIR}/test.db",
        connect_args={"timeout": 30},
        poolclass=NullPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    factory = async_sessionmaker(engine, expire_on_commit=False)

    old_engine = database_mod.engine
    old_factory = {
        database_mod: database_mod.async_session_factory,
        # 下面三个模块是「按值导入」的，必须一并替换，否则 Celery 落库、
        # 缓存重建、过期清理会打进真实 MySQL
        access_log_mod: access_log_mod.async_session_factory,
        link_service_mod: link_service_mod.async_session_factory,
        cleanup_mod: cleanup_mod.async_session_factory,
    }
    database_mod.engine = engine
    database_mod.async_session_factory = factory
    access_log_mod.async_session_factory = factory
    link_service_mod.async_session_factory = factory
    cleanup_mod.async_session_factory = factory

    # 异步 Redis -> fakeredis（lifespan 里的 close_redis 会清掉 _pool，测试期间禁用）
    old_pool = redis_client_mod._pool
    redis_client_mod._pool = fakeredis.aioredis.FakeRedis(decode_responses=True)

    # Celery 缓冲队列用的同步 Redis -> 单个持久 FakeStrictRedis 实例
    sync_fake = fakeredis.FakeStrictRedis(decode_responses=True)
    old_get_sync = access_log_mod._get_redis
    access_log_mod._get_redis = lambda: sync_fake

    import app.main as main_mod

    async def _noop():
        return None

    old_close = main_mod.close_redis
    main_mod.close_redis = _noop

    asyncio.run(_create_tables(engine))

    yield

    asyncio.run(engine.dispose())
    database_mod.engine = old_engine
    for module, factory_ref in old_factory.items():
        module.async_session_factory = factory_ref
    redis_client_mod._pool = old_pool
    access_log_mod._get_redis = old_get_sync
    main_mod.close_redis = old_close
    shutil.rmtree(_TMP_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
async def _clean_state():
    """用例间隔离：清空三张表 + 清空 fakeredis（等价于事务回滚策略）"""
    async with database_mod.async_session_factory() as session:
        for model in (models_mod.AccessLog, models_mod.ShortLink, models_mod.User):
            await session.execute(delete(model))
        await session.commit()
    await redis_client_mod._pool.flushall()
    access_log_mod._get_redis().flushall()  # 同步缓冲队列
    yield


@pytest.fixture
def fake_redis():
    return redis_client_mod._pool


@pytest.fixture
async def db_session():
    """单元测试用的独立数据库会话"""
    async with database_mod.async_session_factory() as session:
        yield session


@pytest.fixture
def client():
    """真实 FastAPI 应用 + TestClient（lifespan 会建表/预热布隆）"""
    with TestClient(app, follow_redirects=False) as c:
        yield c


@pytest.fixture
def auth_headers(client):
    """预创建用户并登录，返回带 Authorization 的请求头"""
    assert (
        client.post(
            "/api/v1/auth/register",
            json={"username": "tester", "password": "secret123"},
        ).status_code
        == 201
    )
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "tester", "password": "secret123"},
    )
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
def create_link(client):
    """链路创建工厂：返回短码，避免每处重复断言"""

    def _create(
        headers,
        origin_url="https://example.com/page",
        custom_alias=None,
        expire_in_seconds=None,
    ) -> str:
        payload = {"origin_url": origin_url}
        if custom_alias is not None:
            payload["custom_alias"] = custom_alias
        if expire_in_seconds is not None:
            payload["expire_in_seconds"] = expire_in_seconds
        resp = client.post("/api/v1/links", json=payload, headers=headers)
        assert resp.status_code == 201, resp.text
        return resp.json()["short_code"]

    return _create


@pytest.fixture
def register_login(client):
    """注册 + 登录工厂：返回 {access_token, refresh_token}"""

    def _do(username="user1", password="secret123") -> dict:
        assert (
            client.post(
                "/api/v1/auth/register",
                json={"username": username, "password": password},
            ).status_code
            == 201
        )
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        assert resp.status_code == 200
        return resp.json()

    return _do
