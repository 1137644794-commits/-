from datetime import datetime

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field


class LinkCreateIn(BaseModel):
    origin_url: AnyHttpUrl
    custom_alias: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{4,16}$", description="自定义短码（可选）")
    expire_in_seconds: int | None = Field(default=None, ge=60, le=31536000, description="有效期（秒），不传则永久有效")


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
