"""FastAPI 应用入口：注册路由、中间件、全局异常处理"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import auth, link, redirect, stats
from app.cache.bloom import BloomFilter
from app.cache.redis_client import close_redis, get_redis
from app.core.config import settings
from app.dao import crud, database
from app.middleware.request_log import request_log_middleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("shortlink")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 本地开发自动建表（带重试，等待 MySQL 就绪）
    await database.create_all()
    # 预热布隆过滤器：启动时把库里已有短码全部加入位图
    async with database.async_session_factory() as session:
        codes = await crud.get_all_codes(session)
    bloom = BloomFilter(get_redis())
    for code in codes:
        await bloom.add(code)
    logger.info("启动完成，布隆过滤器已预热 %d 个短码", len(codes))
    yield
    await close_redis()


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)

# 本地前端调试用，生产按需收紧
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.middleware("http")(request_log_middleware)


# ---------- 全局异常处理：统一 JSON 结构 {"code", "message"} ----------
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.status_code, "message": exc.detail},
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "code": 422,
            "message": "参数校验失败",
            "errors": jsonable_encoder(exc.errors()),
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("未处理异常: %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"code": 500, "message": "服务器内部错误"},
    )


# ---------- 路由注册 ----------
app.include_router(auth.router, prefix="/api/v1")
app.include_router(link.router, prefix="/api/v1")
app.include_router(stats.router, prefix="/api/v1")
app.include_router(redirect.router)


@app.get("/healthz", tags=["系统"])
async def healthz():
    return {"status": "ok"}
