from fastapi.testclient import TestClient


def test_startup_migrates_and_reports_ready(db):
    from app.main import app

    with TestClient(app) as client:          # runs the lifespan: migrations applied
        response = client.get("/health/ready")
        assert response.status_code == 200
        assert response.json() == {"ready": True, "pending": [], "failed": []}
