"""Celery 应用与 Beat 定时任务配置"""

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "shortlink",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks.access_log"],
)

celery_app.conf.update(
    # 本地无 Worker 时同步执行（保证功能可用）；docker-compose 环境覆盖为 false
    task_always_eager=settings.celery_task_always_eager,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    beat_schedule={
        "flush-access-logs": {
            "task": "app.tasks.access_log.flush_access_logs",
            "schedule": settings.log_flush_interval,
        },
    },
)
