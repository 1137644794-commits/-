"""短链服务：创建（发号器 + Base62）、跳转重定向（缓存三级防护）、管理"""

import asyncio
import logging
import random
import time
from datetime import datetime, timedelta

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import link_cache
from app.cache.bloom import BloomFilter
from app.cache.redis_client import get_redis
from app.core.config import settings
from app.dao import crud
from app.dao.database import async_session_factory
from app.utils.base62 import encode as base62_encode

logger = logging.getLogger("shortlink.link")


class LinkService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ==================== 创建 ====================
    async def create(
        self, user_id: int, origin_url: str, custom_alias: str | None, expire_in_seconds: int | None
    ) -> crud.ShortLink:
        expire_at = datetime.now() + timedelta(seconds=expire_in_seconds) if expire_in_seconds else None
        short_code = await self._generate_code(custom_alias)

        try:
            link = await crud.create_link(
                self.db,
                short_code=short_code,
                origin_url=origin_url,
                user_id=user_id,
                expire_at=expire_at,
            )
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise HTTPException(status_code=409, detail="短码已存在")

        # 预热布隆过滤器与缓存，避免首个跳转请求直接打 DB
        await BloomFilter(get_redis()).add(short_code)
        await self._write_cache(link)
        return link

    async def _generate_code(self, custom_alias: str | None) -> str:
        """发号器 + Base62：Redis INCR 拿全局唯一数字，编码成短码，避免哈希碰撞"""
        if custom_alias:
            if await crud.get_link_by_code(self.db, custom_alias):
                raise HTTPException(status_code=409, detail="自定义短码已存在")
            return custom_alias
        # 自定义别名理论上可能撞上发号器结果，撞了重试下一次号
        for _ in range(3):
            seq = await get_redis().incr("seq:link_id")
            candidate = base62_encode(seq)
            if not await crud.get_link_by_code(self.db, candidate):
                return candidate
        raise HTTPException(status_code=500, detail="短码生成失败，请重试")

    # ==================== 跳转（读路径） ====================
    async def redirect(self, short_code: str, request: Request) -> RedirectResponse:
        now = time.time()
        try:
            status, data = await link_cache.get_link(short_code)
        except Exception:
            # Redis 故障降级：视为缓存未命中，直接回源 MySQL，保证跳转可用
            logger.warning("缓存读取失败，降级回源 MySQL: %s", short_code, exc_info=True)
            status, data = "miss", None

        if status == "hit":
            # 逻辑过期：旧数据照常返回，后台持锁重建，防击穿
            if now >= (data.get("logical_expire_at") or 0):
                asyncio.create_task(self._rebuild_cache(short_code))
            response = self._build_redirect_response(data, now)
        elif status == "empty":
            # 命中的是空值缓存，说明之前确认过不存在，防穿透
            raise HTTPException(status_code=404, detail="短链接不存在")
        else:
            # 缓存未命中：布隆过滤器拦截确定不存在的短码，减少无效 DB 查询
            try:
                bloom_hit = await BloomFilter(get_redis()).exists(short_code)
            except Exception:
                # Redis 故障降级：跳过布隆拦截，宁可多查一次 DB，不可误拦
                logger.warning("布隆过滤器不可用，跳过拦截: %s", short_code, exc_info=True)
                bloom_hit = True
            if not bloom_hit:
                raise HTTPException(status_code=404, detail="短链接不存在")
            link = await crud.get_link_by_code(self.db, short_code)
            if link is None:
                try:
                    await link_cache.set_empty(short_code)
                except Exception:
                    logger.warning("空值缓存写入失败（可忽略）: %s", short_code, exc_info=True)
                raise HTTPException(status_code=404, detail="短链接不存在")
            try:
                await BloomFilter(get_redis()).add(short_code)
            except Exception:
                logger.warning("布隆过滤器写入失败（可忽略）: %s", short_code, exc_info=True)
            try:
                await self._write_cache(link)
            except Exception:
                logger.warning("缓存写入失败（可忽略，后续请求仍可回源）: %s", short_code, exc_info=True)
            response = self._build_redirect_response(self._build_payload(link), now)

        await self._record_access(short_code, request)
        return response

    def _build_redirect_response(self, data: dict, now: float) -> RedirectResponse:
        if data.get("is_disabled"):
            raise HTTPException(status_code=403, detail="短链接已被禁用")
        expire_at = data.get("expire_at")
        if expire_at and now > expire_at:
            raise HTTPException(status_code=410, detail="短链接已过期")
        response = RedirectResponse(data["origin_url"], status_code=302)
        # 5 秒短缓存：边缘/浏览器吸收热点尖峰（AWS 短链实践），
        # 同时把禁用/过期的生效延迟约束在 5 秒内，可控可接受
        response.headers["Cache-Control"] = "public, max-age=5"
        return response

    async def _rebuild_cache(self, short_code: str) -> None:
        """后台重建：独立会话 + 分布式锁，保证热点短链只有一个请求回源"""
        if not await link_cache.acquire_rebuild_lock(short_code):
            return
        try:
            async with async_session_factory() as session:
                link = await crud.get_link_by_code(session, short_code)
                if link is None:
                    await link_cache.set_empty(short_code)
                else:
                    await self._write_cache(link)
        except Exception:
            logger.exception("缓存重建失败: %s", short_code)
        finally:
            await link_cache.release_rebuild_lock(short_code)

    # ---------- 缓存写入 ----------
    def _build_payload(self, link) -> dict:
        return {
            "origin_url": link.origin_url,
            "is_disabled": link.is_disabled,
            "expire_at": link.expire_at.timestamp() if link.expire_at else None,
            "logical_expire_at": time.time() + settings.cache_logical_ttl,
        }

    async def _write_cache(self, link) -> None:
        payload = self._build_payload(link)
        await link_cache.set_link(link.short_code, payload, self._physical_ttl(link))

    def _physical_ttl(self, link) -> int:
        jitter = random.randint(1, settings.cache_jitter_seconds)
        if link.expire_at:
            remaining = int(link.expire_at.timestamp() - time.time())
            return max(remaining + jitter, 1)
        return settings.cache_logical_ttl + jitter

    # ---------- 访问记录：实时计数 + 异步日志削峰 ----------
    async def _record_access(self, short_code: str, request: Request) -> None:
        # 延迟导入避免应用启动时就加载 Celery
        from app.tasks.access_log import push_access_log

        try:
            date_str = datetime.now().strftime("%Y-%m-%d")
            ip = request.client.host if request.client else "unknown"
            redis = get_redis()
            pipe = redis.pipeline()
            pipe.incr(f"pv:{short_code}:{date_str}")  # PV 计数器
            pipe.pfadd(f"uv:{short_code}:{date_str}", ip)  # UV 用 HyperLogLog
            await pipe.execute()
            # access_log 不落库，只发消息，由 Worker 批量消费（削峰）
            push_access_log.delay(
                short_code=short_code,
                ip=ip,
                user_agent=request.headers.get("user-agent", ""),
                referer=request.headers.get("referer", ""),
                accessed_at=datetime.now().isoformat(),
            )
        except Exception:
            # 统计计数/日志推送失败不影响跳转主流程
            logger.warning("访问统计或日志推送失败（降级跳过）: %s", short_code, exc_info=True)

    # ==================== 管理 ====================
    async def list_my_links(self, user_id: int, page: int, size: int) -> dict:
        items, total = await crud.list_links_with_total(self.db, user_id, offset=(page - 1) * size, limit=size)
        return {"total": total, "page": page, "size": size, "items": items}

    async def patch(self, user_id: int, short_code: str, is_disabled: bool):
        link = await self._get_owned(user_id, short_code)
        await crud.update_link(self.db, link, is_disabled=is_disabled)
        await self.db.commit()
        # 先更 DB 再删缓存，保证后续读到的都是新状态
        await link_cache.delete_link(short_code)
        return link

    async def delete(self, user_id: int, short_code: str) -> None:
        link = await self._get_owned(user_id, short_code)
        await crud.delete_link(self.db, link)
        await self.db.commit()
        await link_cache.delete_link(short_code)

    async def _get_owned(self, user_id: int, short_code: str):
        link = await crud.get_link_by_code(self.db, short_code)
        if link is None:
            raise HTTPException(status_code=404, detail="短链接不存在")
        if link.user_id != user_id:
            raise HTTPException(status_code=403, detail="无权操作该短链接")
        return link
