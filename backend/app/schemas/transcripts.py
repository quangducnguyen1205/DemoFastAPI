from pydantic import BaseModel
from datetime import datetime


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
