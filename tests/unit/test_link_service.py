"""单元测试：短链 Service —— 测试设计文档 §2.2/§2.3/§2.4

- 短码生成唯一性：发号器 + Base62、自定义别名占用检测、碰撞重试
- 创建流程：唯一约束冲突 409、成功后写缓存
- 缓存降级：Redis 不可用时回源 MySQL 仍能 302
- 物理 TTL：逻辑 TTL + 随机抖动范围
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from freezegun import freeze_time

from app.cache import link_cache
from app.core.config import settings
from app.dao import crud
from app.services.link_service import LinkService


class FakeBloom:
    """模拟 Redis 故障时的布隆过滤器：exists 直接抛错"""

    def __init__(self, redis):
        pass

    async def add(self, value):
        pass

    async def exists(self, value):
        raise ConnectionError("redis down")


async def _prepare_user(db, username="unit_user") -> int:
    await crud.create_user(db, username, "x")
    await db.commit()
    user = await crud.get_user_by_username(db, username)
    return user.id


class TestGenerateCode:
    """短码生成唯一性逻辑（DAO 用 mock，不依赖真实 MySQL）"""

    @pytest.mark.asyncio
    async def test_no_alias_uses_counter(self, monkeypatch):
        async def fake_get_by_code(db, code):
            return None

        monkeypatch.setattr(crud, "get_link_by_code", fake_get_by_code)
        service = LinkService(db=None)
        assert await service._generate_code(None) == "1"  # seq=1 -> Base62 '1'

    @pytest.mark.asyncio
    async def test_collision_retries_next_sequence(self, monkeypatch):
        async def fake_get_by_code(db, code):
            # 发号器结果 '1' 撞上库里已有的自定义短码 -> 重试取下一个号
            return SimpleNamespace() if code == "1" else None

        monkeypatch.setattr(crud, "get_link_by_code", fake_get_by_code)
        service = LinkService(db=None)
        assert await service._generate_code(None) == "2"

    @pytest.mark.asyncio
    async def test_custom_alias_free_returns_alias(self, monkeypatch):
        seen = []

        async def fake_get_by_code(db, code):
            seen.append(code)
            return None

        monkeypatch.setattr(crud, "get_link_by_code", fake_get_by_code)
        service = LinkService(db=None)
        assert await service._generate_code("myalias") == "myalias"
        assert seen == ["myalias"]

    @pytest.mark.asyncio
    async def test_custom_alias_conflict_raises_409(self, monkeypatch):
        async def fake_get_by_code(db, code):
            return SimpleNamespace() if code == "taken1" else None

        monkeypatch.setattr(crud, "get_link_by_code", fake_get_by_code)
        service = LinkService(db=None)
        with pytest.raises(HTTPException) as exc:
            await service._generate_code("taken1")
        assert exc.value.status_code == 409


class TestCreate:
    """创建流程 + 唯一索引兜底"""

    @pytest.mark.asyncio
    async def test_create_persists_and_preheats_cache(self, db_session):
        user_id = await _prepare_user(db_session)
        service = LinkService(db_session)

        link = await service.create(user_id, "https://example.com/a", "alias01", None)

        assert link.short_code == "alias01"
        # 落库
        stored = await crud.get_link_by_code(db_session, "alias01")
        assert stored is not None and stored.origin_url == "https://example.com/a"
        # 预热缓存：首个跳转无需回源
        status, data = await link_cache.get_link("alias01")
        assert status == "hit"
        assert data["origin_url"] == "https://example.com/a"

    @pytest.mark.asyncio
    async def test_create_duplicate_alias_raises_409(self, db_session):
        user_id = await _prepare_user(db_session, "dup_user")
        service = LinkService(db_session)
        await service.create(user_id, "https://example.com/1", "dupdup1", None)

        with pytest.raises(HTTPException) as exc:
            await service.create(user_id, "https://example.com/2", "dupdup1", None)
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_sequence_guarantees_distinct_codes(self, db_session):
        """发号器 + Base62：连续创建得到互不相同的递增短码"""
        user_id = await _prepare_user(db_session, "seq_user")
        service = LinkService(db_session)
        code1 = (await service.create(user_id, "https://example.com/s1", None, None)).short_code
        code2 = (await service.create(user_id, "https://example.com/s2", None, None)).short_code
        assert code1 != code2


class TestDegradation:
    """缓存降级：Redis 不可用时回源 MySQL —— 文档 §2.3 的核心场景"""

    @pytest.mark.asyncio
    async def test_redirect_falls_back_to_db_when_redis_down(self, db_session, monkeypatch):
        user_id = await _prepare_user(db_session, "dg_user")
        service = LinkService(db_session)
        await service.create(user_id, "https://fallback.example.com", "fb001", None)

        # 模拟 Redis 整体故障：读缓存抛错、布隆抛错
        async def boom(code):
            raise ConnectionError("redis down")

        monkeypatch.setattr("app.services.link_service.link_cache.get_link", boom)
        monkeypatch.setattr("app.services.link_service.link_cache.set_empty", boom)
        monkeypatch.setattr("app.services.link_service.BloomFilter", FakeBloom)

        async def _no_record(self, short_code, request):
            return None

        monkeypatch.setattr(LinkService, "_record_access", _no_record)

        request = SimpleNamespace(client=SimpleNamespace(host="1.2.3.4"), headers={})
        resp = await service.redirect("fb001", request)

        # 降级成功：即使 Redis 挂了，跳转依然可用
        assert resp.status_code == 302
        assert resp.headers["location"] == "https://fallback.example.com"

    @pytest.mark.asyncio
    async def test_redirect_404_when_redis_down_and_code_missing(self, db_session, monkeypatch):
        async def boom(code):
            raise ConnectionError("redis down")

        monkeypatch.setattr("app.services.link_service.link_cache.get_link", boom)
        monkeypatch.setattr("app.services.link_service.link_cache.set_empty", boom)
        monkeypatch.setattr("app.services.link_service.BloomFilter", FakeBloom)
        service = LinkService(db_session)
        request = SimpleNamespace(client=SimpleNamespace(host="1.2.3.4"), headers={})
        with pytest.raises(HTTPException) as exc:
            await service.redirect("nothere", request)
        assert exc.value.status_code == 404


class TestPhysicalTtl:
    """物理 TTL = 逻辑 TTL + 随机抖动（防雪崩）—— 文档 §2.4"""

    def test_no_expire_ttl_range(self):
        service = LinkService(db=None)
        link = SimpleNamespace(expire_at=None)
        ttl = service._physical_ttl(link)
        assert settings.cache_logical_ttl + 1 <= ttl <= (settings.cache_logical_ttl + settings.cache_jitter_seconds)

    def test_expire_ttl_range(self):
        service = LinkService(db=None)
        with freeze_time("2026-01-01 00:00:00"):
            link = SimpleNamespace(expire_at=datetime.now() + timedelta(seconds=100))
            ttl = service._physical_ttl(link)
            # 剩余 100 秒 + jitter[1,300]
            assert 101 <= ttl <= 100 + settings.cache_jitter_seconds

    def test_already_expired_ttl_not_below_one(self):
        service = LinkService(db=None)
        with freeze_time("2026-01-01 00:00:00"):
            link = SimpleNamespace(expire_at=datetime.now() - timedelta(seconds=30))
            assert service._physical_ttl(link) >= 1

    def test_jitter_varies_over_samples(self):
        """抖动不能是固定值：多次采样应出现不同结果"""
        service = LinkService(db=None)
        link = SimpleNamespace(expire_at=None)
        values = {service._physical_ttl(link) for _ in range(50)}
        assert len(values) > 1
