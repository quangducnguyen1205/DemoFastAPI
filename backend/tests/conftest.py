import os
import pytest
from fastapi.testclient import TestClient

# Force lightweight DB for tests (single shared in-memory) unless the user overrides.
os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("CELERY_BROKER_URL", "memory://")
os.environ.setdefault("CELERY_RESULT_BACKEND", "cache+memory://")

# Import app after env vars so settings picks them up.
from app.main import app  # noqa: E402
from app.core.database import Base, engine  # noqa: E402

@pytest.fixture(scope="session", autouse=True)
def create_test_db():
    # Create all tables at once for the shared in-memory database (StaticPool ensures persistence).
    Base.metadata.create_all(bind=engine)
    print("Tables in metadata:", Base.metadata.tables.keys())
    yield
    Base.metadata.drop_all(bind=engine)

@pytest.fixture()
def client():
    return TestClient(app)
