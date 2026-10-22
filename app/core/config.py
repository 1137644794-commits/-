from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置：从 .env 读取，可被环境变量覆盖"""

    app_name: str = "短链接服务平台"
    app_env: str = "dev"
    debug: bool = True

    # ============ MySQL ============
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "shortlink"
    mysql_password: str = "shortlink123"
    mysql_db: str = "shortlink"

    # ============ Redis ============
    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_db: int = 0

    # ============ JWT ============
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # ============ 限流（创建短链接口） ============
    rate_limit_max: int = 10
    rate_limit_window: int = 60

    # ============ Celery ============
    celery_broker_url: str = "redis://127.0.0.1:6379/1"
    celery_result_backend: str = "redis://127.0.0.1:6379/2"
    # 本地无 Worker 时置 true：任务在进程内同步执行，保证功能可用
    celery_task_always_eager: bool = True

    # ============ 缓存策略 ============
    cache_empty_ttl: int = 60  # 空值缓存 TTL（防穿透）
    cache_logical_ttl: int = 3600  # 缓存逻辑过期时间（配合锁防击穿）
    cache_jitter_seconds: int = 300  # TTL 随机抖动上限（防雪崩）
    cache_lock_ttl: int = 10  # 缓存重建分布式锁 TTL

    # ============ 访问日志批量落库 ============
    log_flush_batch: int = 100  # 缓冲满 100 条触发一次批量插入
    log_flush_interval: float = 5.0  # 或每 5 秒定时刷新

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def database_url(self) -> str:
        return (
            f"mysql+asyncmy://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_db}?charset=utf8mb4"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
