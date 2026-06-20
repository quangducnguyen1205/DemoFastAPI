from sqlalchemy import BigInteger, Column, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


class ProcessingRequest(Base):
    __tablename__ = "processing_requests"

    event_id = Column(String(64), primary_key=True, index=True)
    asset_id = Column(String(64), nullable=False, index=True)
    workspace_id = Column(String(64), nullable=True, index=True)
    owner_id = Column(String(255), nullable=True)
    storage_bucket = Column(String(255), nullable=False)
    object_key = Column(String(1024), nullable=False)
    original_filename = Column(String(500), nullable=True)
    content_type = Column(String(255), nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    celery_task_id = Column(String(255), nullable=True, index=True)
    status = Column(String(50), nullable=False, default="accepted", index=True)
    segment_count = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)
    occurred_at = Column(String(64), nullable=True)
    requested_at = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    transcripts = relationship(
        "ProcessingRequestTranscript",
        back_populates="processing_request",
        cascade="all,delete-orphan",
    )


class ProcessingRequestTranscript(Base):
    __tablename__ = "processing_request_transcripts"

    id = Column(Integer, primary_key=True, index=True)
    processing_request_event_id = Column(
        String(64),
        ForeignKey("processing_requests.event_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    segment_index = Column(Integer, nullable=False, index=True)
    text = Column(Text, nullable=False)
    start_seconds = Column(Float, nullable=True)
    end_seconds = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "processing_request_event_id",
            "segment_index",
            name="uq_processing_request_transcript_segment",
        ),
    )

    processing_request = relationship("ProcessingRequest", back_populates="transcripts")
