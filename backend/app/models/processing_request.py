from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Index,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
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


class ProcessingOutboxEvent(Base):
    __tablename__ = "processing_outbox_events"

    id = Column(String(64), primary_key=True, index=True)
    event_type = Column(String(255), nullable=False, index=True)
    event_version = Column(Integer, nullable=False)
    aggregate_type = Column(String(64), nullable=False)
    aggregate_id = Column(String(64), nullable=False, index=True)
    event_key = Column(String(64), nullable=False, index=True)
    causation_event_id = Column(String(64), nullable=False, index=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    payload = Column(JSON, nullable=False)
    status = Column(String(50), nullable=False, default="pending", index=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    failure_disposition = Column(String(32), nullable=True, index=True)
    recovery_cycle_count = Column(Integer, nullable=False, default=0)
    next_recovery_at = Column(DateTime(timezone=True), nullable=True)
    last_failure_category = Column(String(128), nullable=True)
    recovery_exhausted_at = Column(DateTime(timezone=True), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "causation_event_id",
            "event_type",
            name="uq_processing_outbox_causation_event_type",
        ),
        CheckConstraint(
            "event_version > 0",
            name="ck_processing_outbox_event_version_positive",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_processing_outbox_attempt_count_nonnegative",
        ),
        CheckConstraint(
            "recovery_cycle_count >= 0",
            name="ck_processing_outbox_recovery_cycle_count_nonnegative",
        ),
        CheckConstraint(
            "failure_disposition IS NULL OR failure_disposition IN "
            "('transient', 'permanent', 'unknown', 'recovery_exhausted')",
            name="ck_processing_outbox_failure_disposition",
        ),
        Index(
            "idx_processing_outbox_recovery_eligibility",
            "status",
            "failure_disposition",
            "next_recovery_at",
            "recovery_cycle_count",
            "created_at",
        ),
    )
