import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from main import app
from database.session import get_session
from main import app, limiter

TEST_DATABASE_URL = "sqlite:///./test.db"


@pytest.fixture
def client():
    """Create a test client for the FastAPI app, backed by a throwaway SQLite DB."""
    test_engine = create_engine(
        TEST_DATABASE_URL, connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(test_engine)

    def get_test_session():
        with Session(test_engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_test_session
    yield TestClient(app)

    app.dependency_overrides.clear()
    SQLModel.metadata.drop_all(test_engine)  # clean slate for the next test


@pytest.fixture
def test_user():
    """A staff-role test user."""
    return {
        "username": "testuser",
        "email": "test@example.com",
        "password": "testpass123",
        "full_name": "Test User",
        "role": "staff",
    }


@pytest.fixture
def test_admin():
    """An admin-role test user, for admin-only endpoint tests."""
    return {
        "username": "testadmin",
        "email": "admin@example.com",
        "password": "adminpass123",
        "full_name": "Test Admin",
        "role": "admin",
    }


@pytest.fixture
def auth_headers(client, test_user):
    """Register + log in a staff user, return an Authorization header dict."""
    client.post("/register", json=test_user)
    response = client.post(
        "/login",
        data={"username": test_user["username"], "password": test_user["password"]},
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers(client, test_admin):
    """Register + log in an admin user, return an Authorization header dict."""
    client.post("/register", json=test_admin)
    response = client.post(
        "/login",
        data={"username": test_admin["username"], "password": test_admin["password"]},
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Clear slowapi's rate-limit storage before every test so tests don't
    interfere with each other's request counts."""
    limiter.reset()
