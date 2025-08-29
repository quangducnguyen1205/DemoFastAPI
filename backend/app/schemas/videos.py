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
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class VideoUploadResponse(BaseModel):
    id: int
    title: str
    path: str
    owner_id: Optional[int] = None
    created_at: datetime
    transcript_segments: list[str] = []

    class Config:
        from_attributes = True


class VideoSearchResult(BaseModel):
    video_id: int
    title: str
    path: Optional[str] = None
    similarity_score: float

    class Config:
        from_attributes = True
