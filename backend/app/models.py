from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
import threading
import os
import pickle

_embedding_model = None
_embedding_lock = threading.Lock()

# Derive defaults from MEDIA_ROOT if specific FAISS paths aren't provided
_MEDIA_ROOT = os.getenv("MEDIA_ROOT") or "media"
FAISS_INDEX_PATH = os.getenv("FAISS_INDEX_PATH") or os.path.join(_MEDIA_ROOT, "faiss_index.faiss")
FAISS_MAPPING_PATH = os.getenv("FAISS_MAPPING_PATH") or os.path.join(_MEDIA_ROOT, "faiss_mapping.pkl")

def get_sentence_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        with _embedding_lock:
            if _embedding_model is None:
                from sentence_transformers import SentenceTransformer  # lazy import
                _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
    return _embedding_model

def generate_embedding(text: str):
    model = get_sentence_embedding_model()
    return model.encode([text])[0]

def load_faiss_index(dimension: int):
    import faiss  # type: ignore
    if os.path.exists(FAISS_INDEX_PATH):
        return faiss.read_index(FAISS_INDEX_PATH)
    index = faiss.IndexFlatL2(dimension)
    return index

def save_faiss_index(index):
    import faiss  # type: ignore
    os.makedirs(os.path.dirname(FAISS_INDEX_PATH), exist_ok=True)
    faiss.write_index(index, FAISS_INDEX_PATH)

def load_faiss_mapping():
    if os.path.exists(FAISS_MAPPING_PATH):
        with open(FAISS_MAPPING_PATH, "rb") as f:
            return pickle.load(f)
    return {}

def save_faiss_mapping(mapping: dict):
    os.makedirs(os.path.dirname(FAISS_MAPPING_PATH), exist_ok=True)
    with open(FAISS_MAPPING_PATH, "wb") as f:
        pickle.dump(mapping, f)
from sqlalchemy.sql import func
from .database import Base

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    email = Column(String(100), unique=True, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class Video(Base):
    __tablename__ = "videos"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    url = Column(String(500), nullable=False)  # legacy / general URL field
    path = Column(String(500), nullable=True, index=True)  # filesystem storage path
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    status = Column(String(50), nullable=True)  # e.g., processing, ready, failed
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # One-to-many transcript segments
    transcripts = relationship("Transcript", back_populates="video", cascade="all,delete-orphan")


class Transcript(Base):
    __tablename__ = "transcripts"

    id = Column(Integer, primary_key=True, index=True)
    video_id = Column(Integer, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True)
    segment_index = Column(Integer, nullable=False, index=True)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("video_id", "segment_index", name="uq_transcript_video_segment"),
    )

    video = relationship("Video", back_populates="transcripts")
