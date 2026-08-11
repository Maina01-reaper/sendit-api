from datetime import datetime

from sqlmodel import Field, SQLModel


class Webhook(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    url: str
    event_type: str  # "document.uploaded", "document.enriched", "document.failed"
    secret: str | None = None
    is_active: bool = Field(default=True)
    created_by: int = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WebhookCreate(SQLModel):
    url: str
    event_type: str
    secret: str | None = None
