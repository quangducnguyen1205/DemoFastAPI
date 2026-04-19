from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class VideoBase(BaseModel):
    title: str
    description: Optional[str] = None
    url: str
    path: Optional[str] = None
    owner_id: Optional[int] = None


class VideoCreate(VideoBase):
    pass


class VideoRead(VideoBase):
    id: int
    status: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
