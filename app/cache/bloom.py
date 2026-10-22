"""布隆过滤器：基于 Redis 位图，拦截不存在的短码，防缓存穿透。

原理：k 个哈希函数把短码映射到位图上的 k 个位置，全部为 1 才"可能存在"。
删除短链后位图不收缩，只会产生"可能存在的误判"（多查一次 MySQL），
不会漏判已存在的短码，安全性由唯一索引兜底。
"""

import hashlib


class BloomFilter:
    def __init__(
        self,
        redis,
        key: str = "bloom:links",
        size: int = 1_000_000,
        hash_count: int = 3,
    ):
        self.redis = redis
        self.key = key
        self.size = size
        self.hash_count = hash_count

    def _positions(self, value: str) -> list[int]:
        positions = []
        for seed in range(self.hash_count):
            digest = hashlib.md5(f"{seed}:{value}".encode()).hexdigest()
            positions.append(int(digest, 16) % self.size)
        return positions

    async def add(self, value: str) -> None:
        pipe = self.redis.pipeline()
        for pos in self._positions(value):
            pipe.setbit(self.key, pos, 1)
        await pipe.execute()

    async def exists(self, value: str) -> bool:
        pipe = self.redis.pipeline()
        for pos in self._positions(value):
            pipe.getbit(self.key, pos)
        results = await pipe.execute()
        return all(results)
