from sqlmodel import SQLModel, Field
from datetime import datetime
from typing import Optional


class Webhook(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    url: str
    event_type: str  # "document.uploaded", "document.enriched", "document.failed"
    secret: Optional[str] = None
    is_active: bool = Field(default=True)
    created_by: int = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WebhookCreate(SQLModel):
    url: str
    event_type: str
    secret: Optional[str] = None
