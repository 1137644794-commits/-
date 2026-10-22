"""滑动窗口限流：Redis ZSET + Lua 脚本保证原子性。

窗口内每次请求往 ZSET 写一个时间戳成员，先清理窗口外的成员再计数，
ZSET 大小即窗口内请求数。Lua 脚本在 Redis 服务端单线程原子执行，
避免"读-判断-写"的并发竞态。
"""

import time
import uuid

_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]

-- 删除窗口外的旧时间戳
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
    return -1
end
redis.call('ZADD', key, now, member)
redis.call('PEXPIRE', key, window * 1000)
return count + 1
"""


class RateLimiter:
    def __init__(self, redis):
        self.redis = redis
        self._script = None

    async def _get_script(self):
        if self._script is None:
            self._script = self.redis.register_script(_SLIDING_WINDOW_LUA)
        return self._script

    async def check(self, key: str, limit: int, window: int) -> tuple[bool, int]:
        """返回 (是否放行, 剩余配额)。首次并发调用时注册 Lua 脚本。"""
        script = await self._get_script()
        now = time.time()
        used = await script(
            keys=[key],
            args=[now, window, limit, f"{now}:{uuid.uuid4().hex}"],
        )
        used = int(used)
        if used < 0:
            return False, 0
        return True, max(limit - used, 0)
