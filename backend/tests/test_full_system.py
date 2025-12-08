import io
import pickle
import uuid
from datetime import timedelta

import numpy as np
import pytest
from fastapi import Depends

from app import models
from app.core.database import SessionLocal
from app.core.security import create_access_token
import app.routers.videos as videos_router
from app.routers import auth as auth_router

_protected_route_registered = False


def _ensure_protected_route(client):
    global _protected_route_registered
    if _protected_route_registered:
        return

    app = client.app

    @app.get("/auth/protected")
    def _protected(current_user=Depends(auth_router.get_current_user)):
        return {"id": current_user.id, "email": current_user.email}

    _protected_route_registered = True


def _insert_video(**overrides):
    defaults = {
        "title": f"Test Video {uuid.uuid4().hex}",
        "description": "integration fixture",
        "url": f"videos/{uuid.uuid4().hex}.mp4",
        "path": f"videos/{uuid.uuid4().hex}.mp4",
        "owner_id": None,
        "status": "ready",
    }
    defaults.update(overrides)
    db = SessionLocal()
    try:
        video = models.Video(**defaults)
        db.add(video)
        db.commit()
        db.refresh(video)
        return video
    finally:
        db.close()


def _register_user(client, email: str | None = None, password: str = "secret123"):
    payload = {
        "name": "Test User",
        "email": email or f"user-{uuid.uuid4().hex}@example.com",
        "password": password,
    }
    return client.post("/auth/register", json=payload)


# ==============================
# AUTH TESTS
# ==============================

def test_register_success(client):
    resp = _register_user(client)
    assert resp.status_code == 200
    data = resp.json()
    assert data["token_type"] == "bearer"
    assert "access_token" in data
    assert data["user"]["email"].endswith("@example.com")


def test_register_duplicate_email(client):
    payload = {"name": "Dup", "email": "dup@example.com", "password": "secret"}
    first = client.post("/auth/register", json=payload)
    assert first.status_code == 200
    second = client.post("/auth/register", json=payload)
    assert second.status_code == 400


def test_register_long_password_returns_400(client):
    payload = {
        "name": "Long",
        "email": "long@example.com",
        "password": "x" * 600,
    }
    resp = client.post("/auth/register", json=payload)
    assert resp.status_code == 400


def test_login_success(client):
    email = "login-success@example.com"
    password = "super-secret"
    reg = client.post("/auth/register", json={"name": "Login", "email": email, "password": password})
    assert reg.status_code == 200
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    data = resp.json()
    assert data["user"]["email"] == email


def test_login_wrong_password(client):
    email = "wrong-pass@example.com"
    client.post("/auth/register", json={"name": "Wrong", "email": email, "password": "abc123"})
    resp = client.post("/auth/login", json={"email": email, "password": "not-the-same"})
    assert resp.status_code == 401


def test_login_unknown_email(client):
    resp = client.post("/auth/login", json={"email": "missing@example.com", "password": "secret"})
    assert resp.status_code == 401


def test_access_protected_route_with_valid_token(client):
    _ensure_protected_route(client)
    reg = _register_user(client)
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    resp = client.get("/auth/protected", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["email"] == reg.json()["user"]["email"]


def test_access_protected_route_with_invalid_token(client):
    _ensure_protected_route(client)
    headers = {"Authorization": "Bearer invalid"}
    resp = client.get("/auth/protected", headers=headers)
    assert resp.status_code == 401


# ==============================
# VIDEO TESTS
# ==============================

def _patch_celery(monkeypatch, task_id: str = "task-id"):
    class DummyAsyncResult:
        def __init__(self, identifier):
            self.id = identifier

    monkeypatch.setattr(
        videos_router.process_video_task,
        "delay",
        lambda *args, **kwargs: DummyAsyncResult(task_id),
    )


def test_upload_without_owner(client, monkeypatch):
    _patch_celery(monkeypatch)
    file_bytes = io.BytesIO(b"fake-video")
    resp = client.post(
        "/videos/upload",
        files={"file": ("video.mp4", file_bytes, "video/mp4")},
        data={"title": "Video without owner"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "processing"


def test_upload_with_owner_and_token(client, monkeypatch):
    _patch_celery(monkeypatch, task_id="with-owner")
    reg = _register_user(client)
    token = reg.json()["access_token"]
    file_bytes = io.BytesIO(b"owner video")
    resp = client.post(
        "/videos/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("video.mp4", file_bytes, "video/mp4")},
        data={"title": "Owned", "owner_id": reg.json()["user"]["id"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == "with-owner"


def test_list_videos_no_filter(client):
    video = _insert_video()
    resp = client.get("/videos/")
    assert resp.status_code == 200
    assert any(item["id"] == video.id for item in resp.json())


def test_list_videos_filter_by_owner(client):
    owner_video = _insert_video(owner_id=123)
    _insert_video(owner_id=456)
    resp = client.get(f"/videos/?owner_id={owner_video.owner_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert all(item["owner_id"] == owner_video.owner_id for item in data)


def test_video_detail(client):
    video = _insert_video()
    resp = client.get(f"/videos/{video.id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == video.id


def test_delete_video_success(client):
    video = _insert_video(path=None)
    resp = client.delete(f"/videos/{video.id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == video.id


def test_delete_video_invalid_id(client):
    resp = client.delete("/videos/999999")
    assert resp.status_code == 404


# ==============================
# SEARCH TESTS
# ==============================

def _mock_faiss(monkeypatch, tmp_path, mapping):
    mapping_path = tmp_path / "faiss_mapping.pkl"
    with mapping_path.open("wb") as fh:
        pickle.dump(mapping, fh)

    class FakeIndex:
        def __init__(self, ntotal: int):
            self.ntotal = ntotal

    monkeypatch.setattr(videos_router.settings, "FAISS_MAPPING_PATH", str(mapping_path))
    monkeypatch.setattr(videos_router, "load_index_if_exists", lambda dim: FakeIndex(len(mapping)))
    monkeypatch.setattr(
        videos_router,
        "generate_embedding",
        lambda q: np.array([0.0, 0.0, 0.0], dtype="float32"),
    )


def test_search_no_videos_returns_empty(client):
    resp = client.get("/videos/search", params={"q": "nothing"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_search_basic(client, monkeypatch, tmp_path):
    video = _insert_video(title="Search Basic")
    _mock_faiss(monkeypatch, tmp_path, {0: video.id})
    monkeypatch.setattr(
        videos_router,
        "search_vector",
        lambda vec, k: (np.array([0.1], dtype="float32"), np.array([0])),
    )
    resp = client.get("/videos/search", params={"q": "query", "k": 1})
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["video_id"] == video.id


def test_search_filter_by_owner(client, monkeypatch, tmp_path):
    allowed = _insert_video(owner_id=1)
    denied = _insert_video(owner_id=2)
    _mock_faiss(monkeypatch, tmp_path, {0: allowed.id, 1: denied.id})
    monkeypatch.setattr(
        videos_router,
        "search_vector",
        lambda vec, k: (np.array([0.1, 0.2], dtype="float32"), np.array([0, 1])),
    )
    resp = client.get("/videos/search", params={"q": "query", "owner_id": 1, "k": 5})
    assert resp.status_code == 200
    ids = [item["video_id"] for item in resp.json()]
    assert ids == [allowed.id]


def test_search_blank_query_returns_400(client):
    resp = client.get("/videos/search", params={"q": "   "})
    assert resp.status_code == 400


def test_search_k_overflow(client, monkeypatch, tmp_path):
    video = _insert_video()
    _mock_faiss(monkeypatch, tmp_path, {0: video.id})
    monkeypatch.setattr(
        videos_router,
        "search_vector",
        lambda vec, k: (np.array([0.2], dtype="float32"), np.array([0])),
    )
    resp = client.get("/videos/search", params={"q": "overflow", "k": 50})
    assert resp.status_code == 200
    assert len(resp.json()) == 1


# ==============================
# TASK STATUS TESTS
# ==============================

def test_task_status_pending(client, monkeypatch):
    class PendingResult:
        state = "PENDING"

    monkeypatch.setattr(videos_router.process_video_task, "AsyncResult", lambda *_: PendingResult())
    resp = client.get("/videos/tasks/pending-id")
    assert resp.status_code == 200
    assert resp.json()["status"] == "PENDING"


def test_task_status_mocked_failure(client, monkeypatch):
    class FailureResult:
        state = "FAILURE"
        result = RuntimeError("boom")

    monkeypatch.setattr(videos_router.process_video_task, "AsyncResult", lambda *_: FailureResult())
    resp = client.get("/videos/tasks/failure-id")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "FAILURE"
    assert "boom" in payload["error"]


# ==============================
# EDGE CASE TESTS
# ==============================

def test_upload_invalid_format(client, monkeypatch):
    _patch_celery(monkeypatch)
    file_bytes = io.BytesIO(b"not-a-video")
    resp = client.post(
        "/videos/upload",
        files={"file": ("file.txt", file_bytes, "text/plain")},
        data={"title": "Text"},
    )
    assert resp.status_code == 422


def test_jwt_expired_token(client):
    _ensure_protected_route(client)
    reg = _register_user(client)
    user_id = reg.json()["user"]["id"]
    expired_token = create_access_token({"sub": str(user_id)}, expires_delta=timedelta(seconds=-1))
    headers = {"Authorization": f"Bearer {expired_token}"}
    resp = client.get("/auth/protected", headers=headers)
    assert resp.status_code == 401


def test_token_tampered_signature(client):
    _ensure_protected_route(client)
    reg = _register_user(client)
    token = reg.json()["access_token"].split(".")
    token[1] = token[1][::-1]  # flip payload
    tampered = ".".join(token)
    resp = client.get("/auth/protected", headers={"Authorization": f"Bearer {tampered}"})
    assert resp.status_code == 401
