from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime

class UserCreate(BaseModel):
    name: str
    email: str

class UserUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None

class UserRead(BaseModel):
    id: int
    name: str
    email: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


# -------------------- Video Schemas --------------------
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


# -------------------- Transcript Schemas --------------------
class TranscriptBase(BaseModel):
    text: str


class TranscriptCreate(TranscriptBase):
    video_id: int


class TranscriptRead(TranscriptBase):
    id: int
    video_id: int
    segment_index: int
    created_at: datetime

    class Config:
        from_attributes = True

