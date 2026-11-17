"""No external services: account fixtures, memory transactions and mocked GPT only."""
import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.testclient import TestClient
from auth import AuthConfig, SecurityService
from auth_store import MemorySecurityStore, FirestoreSecurityStore
from security_helpers import TEST_CONFIG, TEST_USER, TEST_PASSWORD
from test_data_integration import main, Database


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.now = [1800000000.0]
        self.config = replace(TEST_CONFIG, ai_session=3, ai_minute=5, ai_day=100)
        self.store = MemorySecurityStore()
        self.security = SecurityService(self.config, self.store, lambda: self.now[0])
        self.row = {"date": "2026-09-08", "value": 100, "project_type": "기존 유형", "duration_days": 5, "memo": "원본"}
        self.db = Database({"project_001": self.row})
        self.db.collection("conversations").items["private-original"] = {"title": "원본 대화", "messages": []}
        self.ai = Mock()
        self.ai.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="모의 GPT 답변"))])
        for p in [patch.object(main.app.state, "security", self.security, create=True), patch.object(main, "db", self.db),
                  patch.object(main, "client", self.ai), patch.dict(os.environ, {"OPENAI_MODEL": "test-model"})]:
            p.start(); self.addCleanup(p.stop)
        self.client = TestClient(main.app)

    def login(self, client=None):
        client = client or self.client
        result = client.post("/api/auth/login", json={"username": TEST_USER, "password": TEST_PASSWORD}, headers={"Origin": "http://testserver"})
        self.assertEqual(result.status_code, 200)
        client.headers.update({"Origin": "http://testserver", "X-CSRF-Token": result.json()["csrf_token"]})
        return result

    def test_all_business_routes_and_docs_require_auth_even_direct_host(self):
        paths = ["/api/data", "/api/data/summary", "/api/data/yearly-summary", "/api/data/monthly-summary",
                 "/api/data/project-type-summary", "/api/data/csv-test", "/api/conversations", "/api/conversations/old",
                 "/api/auth/me", "/docs", "/openapi.json", "/redoc", "/api/future-route"]
        for path in paths:
            self.assertEqual(self.client.get(path, headers={"Host": "backend.onrender.com"}).status_code, 401)
        for method, path in [("post", "/api/data"), ("put", "/api/data/project_001"), ("delete", "/api/data/project_001"),
                             ("post", "/api/conversations"), ("put", "/api/conversations/old"), ("delete", "/api/conversations/old"),
                             ("post", "/api/chat"), ("post", "/api/auth/logout")]:
            self.assertEqual(getattr(self.client, method)(path).status_code, 401)
        self.ai.chat.completions.create.assert_not_called()

    def test_login_failure_is_generic_no_password_echo(self):
        responses = []
        for user, pw in [("unknown", TEST_PASSWORD), (TEST_USER, "wrong-test-password")]:
            response = self.client.post("/api/auth/login", json={"username": user, "password": pw}, headers={"Origin": "http://testserver"})
            self.assertEqual(response.status_code, 401)
            self.assertNotIn(pw, response.text)
            responses.append(response.json())
        self.assertEqual(responses[0], responses[1])
        response = self.client.post("/api/auth/login", json={"username": TEST_USER, "password": "secret-test-marker" * 40}, headers={"Origin": "http://testserver"})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("secret-test-marker", response.text)

    def test_login_origin_and_json_and_size(self):
        body = {"username": TEST_USER, "password": TEST_PASSWORD}
        for headers in [{}, {"Origin": "https://attacker.invalid"}]:
            self.assertEqual(self.client.post("/api/auth/login", json=body, headers=headers).status_code, 403)
        self.assertEqual(self.client.post("/api/auth/login", content="x", headers={"Origin": "http://testserver", "Content-Type": "text/plain"}).status_code, 415)
        response = self.client.post("/api/auth/login", content='"' + 'x' * 5000 + '"', headers={"Origin": "http://testserver", "Content-Type": "application/json"})
        self.assertEqual(response.status_code, 413)

    def test_cookie_flags_hashed_server_state_and_csrf(self):
        result = self.login()
        header = result.headers["set-cookie"]
        self.assertIn("HttpOnly", header); self.assertIn("SameSite=lax", header); self.assertIn("Path=/", header)
        token = self.client.cookies.get(self.config.cookie)
        self.assertTrue(all(token != key[1] for key in self.store.items))
        self.assertNotIn(token, repr(self.store.items))
        self.assertNotIn(TEST_PASSWORD, repr(self.store.items))
        self.assertNotIn(token, result.text)
        me = self.client.get("/api/auth/me")
        self.assertEqual(me.json()["csrf_token"], result.json()["csrf_token"])
        self.assertEqual(me.headers["cache-control"], "no-store")

    def test_production_cookie_is_secure_host_only(self):
        self.security.config = replace(self.config, production=True, origins=("https://frontend.vercel.app",))
        client = TestClient(main.app, base_url="https://frontend.vercel.app")
        result = client.post("/api/auth/login", json={"username": TEST_USER, "password": TEST_PASSWORD}, headers={"Origin": "https://frontend.vercel.app"})
        self.assertEqual(result.status_code, 200)
        cookie = result.headers["set-cookie"]
        self.assertTrue(cookie.startswith("__Host-m1_session="))
        self.assertIn("Secure", cookie); self.assertNotIn("Domain=", cookie)

    def test_csrf_blocks_every_write_and_cannot_extend_idle_session(self):
        self.login()
        before = copy.deepcopy(self.db.collections["data"].items)
        self.now[0] += 30
        for token in ["", "invalid", "a" * 64, b"\xff"]:
            response = self.client.post("/api/data", json=self.row, headers={"X-CSRF-Token": token})
            self.assertEqual(response.status_code, 403)
        self.assertEqual(self.db.collections["data"].items, before)
        self.assertEqual(self.db.collections["evaluation_data"].items, {})
        self.assertEqual(self.client.post("/api/data", json=self.row, headers={"Origin": "https://attacker.invalid"}).status_code, 403)
        self.assertEqual(self.client.post("/api/auth/logout", json={}, headers={"X-CSRF-Token": ""}).status_code, 403)

    def test_logout_invalidates_copied_cookie_and_rotates_login(self):
        self.login()
        old = self.client.cookies.get(self.config.cookie)
        copied = TestClient(main.app, cookies={self.config.cookie: old})
        self.login()
        self.assertNotEqual(old, self.client.cookies.get(self.config.cookie))
        self.assertEqual(copied.get("/api/data").status_code, 401)
        current = self.client.cookies.get(self.config.cookie)
        self.assertEqual(self.client.post("/api/auth/logout", json={}).status_code, 200)
        self.assertEqual(self.client.get("/api/data").status_code, 401)
        replay = TestClient(main.app, cookies={self.config.cookie: current})
        self.assertEqual(replay.get("/api/data").status_code, 401)

    def test_idle_absolute_expiration_and_me_does_not_extend(self):
        self.login(); self.now[0] += self.config.idle - 1
        self.assertEqual(self.client.get("/api/auth/me").status_code, 200)
        self.now[0] += 1
        self.assertEqual(self.client.get("/api/data").status_code, 401)
        self.login()
        for _ in range(9):
            self.now[0] += 3500
            result = self.client.get("/api/data")
        self.assertEqual(result.status_code, 401)

    def test_version_and_secret_invalidate_sessions(self):
        self.login()
        self.security.config = replace(self.config, version="changed")
        self.assertEqual(self.client.get("/api/data").status_code, 401)
        self.security.config = self.config
        self.login()
        self.security.config = replace(self.config, secret=b"different-test-secret-00000000000000000")
        self.assertEqual(self.client.get("/api/data").status_code, 401)

    def test_login_throttle_persistent_across_service_instances(self):
        self.security.config = replace(self.config, login_ip=2, login_global=3)
        for _ in range(2):
            result = self.client.post("/api/auth/login", json={"username": "wrong", "password": "wrong"}, headers={"Origin": "http://testserver"})
            self.assertEqual(result.status_code, 401)
        result = self.client.post("/api/auth/login", json={"username": TEST_USER, "password": TEST_PASSWORD}, headers={"Origin": "http://testserver", "X-Forwarded-For": "spoofed"})
        self.assertEqual(result.status_code, 429); self.assertIn("Retry-After", result.headers)
        replacement = SecurityService(self.security.config, self.store, lambda: self.now[0])
        with self.assertRaises(HTTPException) as caught:
            replacement.login(TEST_USER, TEST_PASSWORD, "testclient")
        self.assertEqual(caught.exception.status_code, 429)
        self.now[0] += 600
        self.login()

    def test_original_data_and_conversations_never_written_or_exposed(self):
        self.login()
        baseline = copy.deepcopy(self.db.collection("data").items)
        original_talks = copy.deepcopy(self.db.collection("conversations").items)
        for method, kwargs in [("put", {"json": self.row}), ("delete", {})]:
            self.assertEqual(getattr(self.client, method)("/api/data/project_001", **kwargs).status_code, 403)
        result = self.client.post("/api/data", json=self.row)
        id = result.json()["id"]
        self.assertTrue(id.startswith("eval_"))
        self.assertEqual(self.client.get("/api/data").json()["count"], 2)
        rows = self.client.get("/api/data").json()["data"]
        self.assertTrue(next(row for row in rows if row["id"] == "project_001")["read_only"])
        self.assertFalse(next(row for row in rows if row["id"] == id)["read_only"])
        self.assertEqual(self.client.put("/api/data/" + id, json=dict(self.row, value=200)).status_code, 200)
        self.assertEqual(self.client.get("/api/data/summary").json()["total_value"], 300)
        self.assertEqual(self.client.delete("/api/data/" + id).status_code, 200)
        self.assertEqual(self.client.get("/api/conversations").json()["count"], 0)
        self.assertEqual(self.client.get("/api/conversations/private-original").status_code, 404)
        self.assertEqual(self.client.delete("/api/conversations/private-original").status_code, 404)
        self.assertEqual(self.db.collection("data").items, baseline)
        self.assertEqual(self.db.collection("conversations").items, original_talks)
        with self.assertRaises(HTTPException):
            main.document("data", "project_001")

    def test_ai_session_global_day_limits_and_lease(self):
        self.login()
        token = self.client.cookies.get(self.config.cookie)
        session = self.security.authenticate(token)
        lease = self.security.reserve_ai(session)
        self.assertEqual(self.client.post("/api/chat", json={"message": "질문"}).status_code, 429)
        self.ai.chat.completions.create.assert_not_called()
        self.security.release_ai("wrong-owner")
        self.assertEqual(self.store.items[("auth_limits", "ai_global")]["lease_owner"], lease)
        self.security.release_ai(lease)
        for _ in range(2):
            self.assertEqual(self.client.post("/api/chat", json={"message": "질문"}).status_code, 200)
        self.assertEqual(self.client.post("/api/chat", json={"message": "질문"}).status_code, 429)
        self.assertEqual(self.ai.chat.completions.create.call_count, 2)
        # Logging in again cannot bypass the global day count.
        self.now[0] += 60
        self.security.config = replace(self.config, ai_day=3)
        self.login()
        self.assertEqual(self.client.post("/api/chat", json={"message": "질문"}).status_code, 429)
        self.now[0] += 86400
        self.login()
        self.assertEqual(self.client.post("/api/chat", json={"message": "질문"}).status_code, 200)

    def test_atomic_concurrent_reservations_and_stale_lease_recovery(self):
        self.login()
        session = self.security.authenticate(self.client.cookies.get(self.config.cookie))
        def reserve(_):
            try: return self.security.reserve_ai(session)
            except HTTPException: return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(reserve, range(8)))
        self.assertEqual(sum(result is not None for result in results), 1)
        first = next(result for result in results if result)
        self.now[0] += 121
        second = self.security.reserve_ai(session)
        self.security.release_ai(first)
        self.assertEqual(self.store.items[("auth_limits", "ai_global")]["lease_owner"], second)

    def test_new_session_cannot_bypass_global_minute_quota(self):
        self.login()
        for _ in range(3):
            self.assertEqual(self.client.post("/api/chat", json={"message": "질문"}).status_code, 200)
        other = TestClient(main.app)
        self.login(other)
        for _ in range(2):
            self.assertEqual(other.post("/api/chat", json={"message": "질문"}).status_code, 200)
        fresh = TestClient(main.app)
        self.login(fresh)
        result = fresh.post("/api/chat", json={"message": "질문"})
        self.assertEqual(result.status_code, 429)
        self.assertEqual(self.ai.chat.completions.create.call_count, 5)

    def test_activity_updates_cannot_resurrect_logged_out_session(self):
        self.login()
        token = self.client.cookies.get(self.config.cookie)
        def update(_):
            try: self.security.authenticate(token, touch=True)
            except HTTPException: pass
        with ThreadPoolExecutor(max_workers=8) as pool:
            jobs = [pool.submit(update, i) for i in range(16)]
            self.security.logout(token)
            for job in jobs: job.result()
        with self.assertRaises(HTTPException) as caught:
            self.security.authenticate(token)
        self.assertEqual(caught.exception.status_code, 401)

    def test_revoked_session_cannot_reserve_paid_call(self):
        self.login()
        token = self.client.cookies.get(self.config.cookie)
        session = self.security.authenticate(token)
        self.security.logout(token)
        with self.assertRaises(HTTPException) as caught:
            main.request_ai_answer([{"role": "user", "content": "질문"}], security=self.security, session=session)
        self.assertEqual(caught.exception.status_code, 401)
        self.ai.chat.completions.create.assert_not_called()

    def test_failed_gpt_keeps_attempt_budget_and_releases_lease(self):
        self.login()
        from openai import APIConnectionError
        import httpx
        self.ai.chat.completions.create.side_effect = APIConnectionError(request=httpx.Request("POST", "https://example.invalid"))
        self.assertEqual(self.client.post("/api/chat", json={"message": "질문"}).status_code, 502)
        state = self.store.items[("auth_limits", "ai_global")]
        self.assertEqual(state["day_count"], 1); self.assertEqual(state["lease_until"], 0)
        self.assertEqual(self.db.collection("evaluation_conversations").items, {})

    def test_store_failure_blocks_login_and_sdk(self):
        self.login()
        with patch.object(self.store, "atomic", side_effect=HTTPException(503, "저장소 연결 실패")):
            self.assertEqual(self.client.post("/api/chat", json={"message": "질문"}).status_code, 503)
            self.assertEqual(self.client.post("/api/auth/login", json={"username": TEST_USER, "password": TEST_PASSWORD}).status_code, 503)
        self.ai.chat.completions.create.assert_not_called()

    def test_missing_auth_settings_are_fail_closed_and_paid_get_disabled(self):
        with patch.object(main.app.state, "security", None), patch.dict(os.environ, {"EVAL_USERNAME": "", "EVAL_PASSWORD_HASH": "", "AUTH_SECRET": ""}):
            self.assertEqual(self.client.get("/api/data").status_code, 503)
            self.assertEqual(self.client.post("/api/auth/login", json={"username": TEST_USER, "password": TEST_PASSWORD}).status_code, 503)
        self.assertEqual(self.client.get("/api/chat/test").status_code, 404)
        self.ai.chat.completions.create.assert_not_called()

    def test_config_validation_and_no_development_fallback(self):
        settings = {"APP_ENV": "production", "EVAL_USERNAME": TEST_USER, "EVAL_PASSWORD_HASH": TEST_CONFIG.verifier,
                    "AUTH_SECRET": TEST_CONFIG.secret.decode(), "AUTH_ALLOWED_ORIGINS": "https://frontend.vercel.app"}
        with patch.dict(os.environ, settings, clear=True):
            self.assertTrue(AuthConfig.from_env().production)
            for key, value in [("APP_ENV", "typo"), ("AUTH_SECRET", "short"), ("AUTH_ALLOWED_ORIGINS", "*"),
                               ("AUTH_ALLOWED_ORIGINS", "http://frontend.vercel.app"), ("EVAL_PASSWORD_HASH", "secret-test-marker"),
                               ("AI_GLOBAL_DAY_LIMIT", "0")]:
                with patch.dict(os.environ, {key: value}), self.assertRaises(HTTPException) as caught:
                    AuthConfig.from_env()
                self.assertEqual(caught.exception.status_code, 503)
                self.assertNotIn("secret-test-marker", caught.exception.detail)


class TransactionAdapterTests(unittest.TestCase):
    def test_firestore_adapter_defers_writes_until_reads_finish(self):
        operations = []
        class Ref:
            def get(self, **kwargs):
                operations.append("read")
                return SimpleNamespace(exists=False)
        tx = SimpleNamespace(set=lambda *args: operations.append("write"), delete=lambda *args: operations.append("delete"))
        db = Mock(); db.transaction.return_value = tx; db.collection.return_value.document.return_value = Ref()
        # Simulate SDK decorator execution without an RPC/real client.
        with patch("auth_store.firestore.transactional", lambda fn: lambda transaction: fn(transaction)):
            def work(access):
                access.set("auth_limits", "one", {"count": 1})
                access.get("auth_limits", "two")
                access.delete("auth_sessions", "old")
            FirestoreSecurityStore(db).atomic(work)
        self.assertEqual(operations, ["read", "write", "delete"])

    def test_security_store_does_not_allow_original_collection_or_leak_errors(self):
        db = Mock(); db.transaction.side_effect = RuntimeError("secret-test-marker")
        with self.assertRaises(HTTPException) as caught:
            FirestoreSecurityStore(db).atomic(lambda access: None)
        self.assertEqual(caught.exception.status_code, 503)
        self.assertNotIn("secret-test-marker", caught.exception.detail)
        memory = MemorySecurityStore()
        with self.assertRaises(AssertionError):
            memory.atomic(lambda access: access.set("data", "original", {}))
        self.assertEqual(memory.items, {})


if __name__ == "__main__":
    unittest.main()
