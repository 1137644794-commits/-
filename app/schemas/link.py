import ipaddress
from datetime import datetime

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator

# 短链目标禁止指向的主机名（SSRF 防护：防把短链服务当成内网跳板）
_BLOCKED_HOSTS = {"localhost", "localhost.localdomain", "ip6-localhost"}


class LinkCreateIn(BaseModel):
    origin_url: AnyHttpUrl
    custom_alias: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{4,16}$", description="自定义短码（可选）")
    expire_in_seconds: int | None = Field(default=None, ge=60, le=31536000, description="有效期（秒），不传则永久有效")

    @field_validator("origin_url")
    @classmethod
    def reject_internal_address(cls, url: AnyHttpUrl) -> AnyHttpUrl:
        """拒绝指向内网/环回/保留地址的目标 URL（防 SSRF，同类短链服务标配）"""
        host = (url.host or "").lower().rstrip(".")
        if host in _BLOCKED_HOSTS:
            raise ValueError("目标地址不允许指向内网")
        try:
            # IPv6 字面量在 URL 里带方括号（如 [::1]），解析前去掉
            ip = ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            return url  # 普通域名放行（域名解析层面的防护由网关/WAF 负责）
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError("目标地址不允许指向内网")
        return url


class LinkPatchIn(BaseModel):
    is_disabled: bool


class LinkOut(BaseModel):
    id: int
    short_code: str
    origin_url: str
    expire_at: datetime | None
    is_disabled: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LinkListOut(BaseModel):
    total: int
    page: int
    size: int
    items: list[LinkOut]
