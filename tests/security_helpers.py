"""Fixed NON-PRODUCTION credentials for isolated tests, never application defaults."""
from unittest.mock import patch
from fastapi.testclient import TestClient
from auth import AuthConfig, SecurityService, password_hash
from auth_store import MemorySecurityStore

TEST_USER = "test-evaluator"
TEST_PASSWORD = "test-only-not-a-real-password"
TEST_CONFIG = AuthConfig(TEST_USER, password_hash(TEST_PASSWORD, b"0" * 16),
                         b"test-only-secret-not-for-production-0000", ("http://testserver",),
                         production=False, ai_session=1000, ai_minute=1000, ai_day=1000)


def authenticated_client(test, main):
    service = SecurityService(TEST_CONFIG, MemorySecurityStore())
    patcher = patch.object(main.app.state, "security", service, create=True)
    patcher.start()
    test.addCleanup(patcher.stop)
    client = TestClient(main.app)
    response = client.post("/api/auth/login", json={"username": TEST_USER, "password": TEST_PASSWORD},
                           headers={"Origin": "http://testserver"})
    test.assertEqual(response.status_code, 200)
    client.headers.update({"Origin": "http://testserver", "X-CSRF-Token": response.json()["csrf_token"]})
    return client
