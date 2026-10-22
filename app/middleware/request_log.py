"""请求日志中间件：记录方法、路径、状态码与耗时"""

import logging
import time

logger = logging.getLogger("shortlink.request")


async def request_log_middleware(request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration = (time.perf_counter() - start) * 1000
    logger.info(
        "%s %s -> %s (%.2fms)",
        request.method,
        request.url.path,
        response.status_code,
        duration,
    )
    return response
