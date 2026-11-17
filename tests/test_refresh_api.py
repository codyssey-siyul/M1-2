"""Model finite transaction retries without contacting Firestore or OpenAI."""
import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, RLock
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from auth import SecurityService
from security_helpers import TEST_CONFIG, TEST_USER, TEST_PASSWORD
from test_data_integration import main, Database


class ContendingSessionStore:
    """Optimistic retry model, NOT a real Firestore RPC/concurrency-mode test.

    With 5 writers and 3 retries, allow one writer per retry wave. Read-only
    authentication needs no commit and cannot consume a retry wave.
    """
    def __init__(self):
        self.items = {}
        self.lock = RLock()
        self.version = 0
        self.burst = False
        self.barriers = [Barrier(size) for size in (5, 4, 3)]
        self.session_writes = 0

    def atomic(self, callback):
        for attempt in range(3):
            with self.lock:
                working, version = copy.deepcopy(self.items), self.version
            writes = []

            class Access:
                def get(self, collection, id):
                    return copy.deepcopy(working.get((collection, id)))

                def set(self, collection, id, data):
                    working[(collection, id)] = copy.deepcopy(data)
                    writes.append((collection, id))

                def delete(self, collection, id):
                    working.pop((collection, id), None)
                    writes.append((collection, id))

            result = callback(Access())
            if not writes:
                return result
            session_write = all(collection == "auth_sessions" for collection, _ in writes)
            if self.burst and session_write:
                self.barriers[attempt].wait(timeout=5)
            with self.lock:
                if version != self.version:
                    continue
                self.items = working
                self.version += 1
                self.session_writes += int(session_write)
                return result
        raise HTTPException(503, "인증 저장소에 연결할 수 없습니다.")


class RefreshApiTests(unittest.TestCase):
    def setUp(self):
        self.store = ContendingSessionStore()
        self.service = SecurityService(TEST_CONFIG, self.store, lambda: 1800000000.0)
        self.original = {"date": "2026-09-08", "value": 100, "project_type": "원본", "duration_days": 5, "memo": "변경 금지"}
        self.db = Database({"project_001": self.original})
        for item in [patch.object(main.app.state, "security", self.service, create=True), patch.object(main, "db", self.db)]:
            item.start(); self.addCleanup(item.stop)
        self.client = TestClient(main.app)
        response = self.client.post("/api/auth/login", json={"username": TEST_USER, "password": TEST_PASSWORD}, headers={"Origin": "http://testserver"})
        self.assertEqual(response.status_code, 200)
        self.client.headers.update({"Origin": "http://testserver", "X-CSRF-Token": response.json()["csrf_token"]})
        self.paths = ["/api/data", "/api/data/summary", "/api/data/yearly-summary", "/api/data/monthly-summary", "/api/data/project-type-summary"]

    def test_post_commit_parallel_refresh_can_fail_while_serial_refresh_succeeds(self):
        baseline = copy.deepcopy(self.db.collection("data").items)
        saved = self.client.post("/api/data", json=dict(self.original, value=200))
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(len(self.db.collection("evaluation_data").items), 1)
        self.store.burst = True
        with ThreadPoolExecutor(max_workers=5) as workers:
            statuses = list(workers.map(lambda path: self.client.get(path).status_code, self.paths))
        self.assertEqual(sorted(statuses), [200, 200, 200, 503, 503])
        self.store.burst = False
        before = self.store.session_writes
        results = [self.client.get(path) for path in self.paths]
        self.assertTrue(all(result.status_code == 200 for result in results))
        self.assertEqual(results[0].json()["count"], 2)
        self.assertEqual(results[1].json()["total_value"], 300)
        self.assertEqual(self.store.session_writes - before, 5)
        self.assertEqual(self.db.collection("data").items, baseline)

    def test_authenticated_get_contract_does_not_require_csrf_but_writes_do(self):
        self.client.headers.pop("X-CSRF-Token")
        for path in self.paths:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
        denied = self.client.post("/api/data", json=self.original)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(self.db.collection("evaluation_data").items, {})


if __name__ == "__main__":
    unittest.main()
