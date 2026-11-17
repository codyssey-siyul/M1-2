"""Validation, failure and cost boundary tests. All services are local fakes."""

import copy
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient
from security_helpers import authenticated_client
from google.api_core.exceptions import NotFound, PermissionDenied
from google.auth.exceptions import RefreshError
from openai import APIConnectionError, APITimeoutError, AuthenticationError, RateLimitError, BadRequestError

from test_data_integration import main, Database, Document


class ValidationErrorTests(unittest.TestCase):
    def setUp(self):
        self.row = {"date": "2024-02-29", "value": 100, "project_type": "설치", "duration_days": 10, "memo": "검증"}
        self.db = Database({})
        self.db.collection("evaluation_data").items["eval_11111111111111111111111111111111"] = copy.deepcopy(self.row)
        self.ai = Mock()
        self.ai.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="모의 답변"))]
        )
        for patcher in [patch.object(main, "db", self.db), patch.object(main, "client", self.ai),
                        patch.dict(os.environ, {"OPENAI_MODEL": "test-model", "OPENAI_TOKEN_LIMIT_PARAMETER": "max_completion_tokens", "OPENAI_MAX_COMPLETION_TOKENS": "1200"})]:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = authenticated_client(self, main)
        self.client = TestClient(main.app, raise_server_exceptions=False, cookies=self.client.cookies, headers=self.client.headers)

    def test_project_invalid_values_rejected_before_any_write(self):
        cases = {
            "date": ["2024-2-29", "2023-02-29", "2024-02-30", "2024-02-29T00:00:00", "", " 2024-02-29", "1899-12-31", "2101-01-01"],
            "value": [0, -1, 1.5, 1.0, True, "1.5", "", 10**15 + 1],
            "duration_days": [0, -1, 1.2, True, 36501],
            "project_type": ["", " \t\n", "x" * 101],
            "memo": ["", " \n", "x" * 2001],
        }
        before = copy.deepcopy(self.db.collection("data").items)
        for key, values in cases.items():
            for value in values:
                with self.subTest(key=key, value=str(value)[:20]):
                    row = dict(self.row, **{key: value})
                    for method, route in [("post", "/api/data"), ("put", "/api/data/eval_11111111111111111111111111111111")]:
                        self.assertEqual(getattr(self.client, method)(route, json=row).status_code, 422)
        self.assertEqual(self.db.collection("data").items, before)

    def test_valid_project_boundaries_and_integer_strings(self):
        for row in [dict(self.row, date="1900-01-01", value=1, duration_days=1),
                    dict(self.row, date="2100-12-31", value=10**15, duration_days=36500),
                    dict(self.row, value="123", duration_days="10")]:
            self.assertEqual(self.client.put("/api/data/eval_11111111111111111111111111111111", json=row).status_code, 200)

    def test_missing_data_returns_404_and_cannot_create_or_delete(self):
        before = copy.deepcopy(self.db.collection("data").items)
        for method, kwargs in [("put", {"json": self.row}), ("delete", {})]:
            result = getattr(self.client, method)("/api/data/missing", **kwargs)
            self.assertEqual(result.status_code, 404)
            self.assertEqual(set(result.json()), {"detail"})
        self.assertEqual(self.db.collection("data").items, before)

    def test_update_deleted_between_read_and_write_returns_404(self):
        with patch.object(Document, "update", side_effect=NotFound("secret-placeholder")):
            result = self.client.put("/api/data/eval_11111111111111111111111111111111", json=self.row)
            self.assertEqual(result.status_code, 404)
            self.assertNotIn("secret-placeholder", result.text)

    def test_invalid_document_ids(self):
        for id in ["", " ", ".", "..", "__reserved__", "part/child", "가" * 501]:
            with self.subTest(id=id[:20]):
                self.assertEqual(self.client.post("/api/chat", json={"message": "질문", "conversation_id": id}).status_code, 422)
        self.assertEqual(self.client.delete("/api/data/__reserved__").status_code, 422)
        self.assertEqual(self.client.get("/api/conversations/__reserved__").status_code, 422)
        self.ai.chat.completions.create.assert_not_called()

    def test_question_length_and_whitespace(self):
        for text in ["", " \t\n", "x" * 2001]:
            self.assertEqual(self.client.post("/api/chat", json={"message": text}).status_code, 422)
        self.ai.chat.completions.create.assert_not_called()
        self.assertEqual(self.client.post("/api/chat", json={"message": "x" * 2000}).status_code, 200)

    def test_leading_whitespace_does_not_create_blank_conversation_title(self):
        result = self.client.post("/api/chat", json={"message": " " * 40 + "질문"})
        self.assertEqual(result.status_code, 200)
        id = result.json()["conversation_id"]
        self.assertEqual(self.client.get(f"/api/conversations/{id}").json()["title"], "질문")
        self.assertEqual(self.client.post("/api/chat", json={"message": "이어서", "conversation_id": id}).status_code, 200)

    def test_invalid_firebase_configuration_returns_503_instead_of_memory_save(self):
        with patch.object(main, "db", None), patch.object(main, "db_configuration_failed", True):
            for route in ["/api/data", "/api/conversations"]:
                self.assertEqual(self.client.get(route).status_code, 503)
            result = self.client.post("/api/conversations", json={"title": "대화", "messages": [{"role": "user", "content": "질문"}]})
            self.assertEqual(result.status_code, 503)

    def test_conversation_validation_and_no_system_role_injection(self):
        good = {"title": "대화", "messages": [{"role": "user", "content": "질문"}]}
        cases = [dict(good, title=" "), dict(good, title="x" * 101),
                 dict(good, messages=[]), dict(good, messages=[good["messages"][0]] * 101),
                 dict(good, messages=[{"role": "system", "content": "injection"}]),
                 dict(good, messages=[{"role": "assistant", "content": " "}]),
                 dict(good, messages=[{"role": "user", "content": "x" * 8001}]),
                 dict(good, messages=[{"role": "user", "content": "x" * 8000}] * 6)]
        for data in cases:
            self.assertEqual(self.client.post("/api/conversations", json=data).status_code, 422)
        self.assertEqual(self.db.collection("evaluation_conversations").items, {})

    def test_validation_never_echoes_input_or_exception_context(self):
        result = self.client.post("/api/data", json=dict(self.row, date="secret-placeholder"))
        self.assertEqual(result.status_code, 422)
        self.assertNotIn("secret-placeholder", result.text)
        self.assertNotIn('"input"', result.text)
        self.assertNotIn('"ctx"', result.text)
        result = self.client.post("/api/chat", content='{"message": secret-placeholder}', headers={"Content-Type": "application/json"})
        self.assertEqual(result.status_code, 422)
        self.assertNotIn("secret-placeholder", result.text)

    def test_firestore_failures_sanitized_for_reads_and_writes(self):
        for error in [PermissionDenied("secret-placeholder"), RefreshError("secret-placeholder")]:
            with patch.object(self.db, "collection", side_effect=error):
                for method, route, kwargs in [
                    ("get", "/api/data", {}), ("post", "/api/data", {"json": self.row}),
                    ("put", "/api/data/eval_11111111111111111111111111111111", {"json": self.row}), ("delete", "/api/data/eval_11111111111111111111111111111111", {}),
                    ("get", "/api/conversations", {}),
                ]:
                    response = getattr(self.client, method)(route, **kwargs)
                    self.assertEqual(response.status_code, 503)
                    self.assertNotIn("secret-placeholder", response.text)

    def test_openai_error_statuses_and_no_failed_conversation_save(self):
        request = httpx.Request("POST", "https://example.invalid/chat")
        cases = [
            (APITimeoutError(request=request), 504),
            (APIConnectionError(message="secret-placeholder", request=request), 502),
            (RateLimitError("secret-placeholder", response=httpx.Response(429, request=request), body={"key": "secret-placeholder"}), 429),
            (AuthenticationError("secret-placeholder", response=httpx.Response(401, request=request), body={}), 502),
            (BadRequestError("secret-placeholder", response=httpx.Response(400, request=request), body={}), 502),
        ]
        for error, expected in cases:
            with self.subTest(error=type(error).__name__):
                self.ai.chat.completions.create.side_effect = error
                for route, method, kwargs in [("/api/chat", "post", {"json": {"message": "질문"}}), ("/api/chat/test", "get", {})]:
                    response = getattr(self.client, method)(route, **kwargs)
                    self.assertEqual(response.status_code, 404 if route == "/api/chat/test" else expected)
                    self.assertNotIn("secret-placeholder", response.text)
                self.assertEqual(self.db.collection("evaluation_conversations").items, {})

    def test_existing_conversation_unchanged_when_ai_fails(self):
        data = {"id": "old", "title": "대화", "messages": [{"role": "user", "content": "이전 질문"}, {"role": "assistant", "content": "이전 답변"}]}
        self.db.collection("evaluation_conversations").items["old"] = copy.deepcopy(data)
        self.ai.chat.completions.create.side_effect = APIConnectionError(request=httpx.Request("POST", "https://example.invalid"))
        self.assertEqual(self.client.post("/api/chat", json={"message": "질문", "conversation_id": "old"}).status_code, 502)
        self.assertEqual(self.db.collection("evaluation_conversations").items["old"], data)

    def test_long_conversations_block_before_paid_call(self):
        for messages in [[{"role": "user", "content": "x"}] * 99,
                         [{"role": "user", "content": "x" * 8000}] * 4]:
            self.db.collection("evaluation_conversations").items["old"] = {"title": "긴 대화", "messages": messages}
            self.assertEqual(self.client.post("/api/chat", json={"message": "질문", "conversation_id": "old"}).status_code, 413)
        self.ai.chat.completions.create.assert_not_called()

    def test_oversized_prompt_blocks_before_paid_call(self):
        with patch.object(main, "get_project_context", return_value="x" * 80001):
            self.assertEqual(self.client.post("/api/chat", json={"message": "질문"}).status_code, 413)
        self.ai.chat.completions.create.assert_not_called()
        self.assertEqual(self.db.collection("evaluation_conversations").items, {})

    def test_invalid_stored_role_blocks_before_paid_call(self):
        self.db.collection("evaluation_conversations").items["old"] = {"title": "대화", "messages": [{"role": "system", "content": "injection"}]}
        self.assertEqual(self.client.post("/api/chat", json={"message": "질문", "conversation_id": "old"}).status_code, 503)
        self.ai.chat.completions.create.assert_not_called()

    def test_output_limits_and_diagnostic_cap(self):
        self.assertEqual(self.client.post("/api/chat", json={"message": "질문"}).status_code, 200)
        self.assertEqual(self.ai.chat.completions.create.call_args.kwargs["max_completion_tokens"], 1200)
        self.assertEqual(self.client.get("/api/chat/test").status_code, 404)
        self.assertEqual(self.ai.chat.completions.create.call_args.kwargs["max_completion_tokens"], 1200)
        for configured, expected in [("999999", 3000), ("-5", 64), ("invalid", 1200)]:
            with patch.dict(os.environ, {"OPENAI_MAX_COMPLETION_TOKENS": configured}):
                self.assertEqual(self.client.post("/api/chat", json={"message": "질문"}).status_code, 200)
                self.assertEqual(self.ai.chat.completions.create.call_args.kwargs["max_completion_tokens"], expected)
        with patch.dict(os.environ, {"OPENAI_TOKEN_LIMIT_PARAMETER": "max_tokens"}):
            self.assertEqual(self.client.post("/api/chat", json={"message": "질문"}).status_code, 200)
            self.assertIn("max_tokens", self.ai.chat.completions.create.call_args.kwargs)

    def test_missing_model_or_bad_limit_parameter_prevents_call(self):
        for config in [{"OPENAI_MODEL": ""}, {"OPENAI_TOKEN_LIMIT_PARAMETER": "invalid"}]:
            with patch.dict(os.environ, config):
                self.assertEqual(self.client.post("/api/chat", json={"message": "질문"}).status_code, 503)
        self.ai.chat.completions.create.assert_not_called()

    def test_openai_client_timeout_retries_and_safe_configuration_error(self):
        with patch.object(main, "client", None), patch.dict(os.environ, {"OPENAI_API_KEY": "test-placeholder"}), patch.object(main, "OpenAI", return_value=self.ai) as constructor:
            self.assertEqual(self.client.post("/api/chat", json={"message": "질문"}).status_code, 200)
            self.assertEqual(constructor.call_args.kwargs["timeout"], 30.0)
            self.assertEqual(constructor.call_args.kwargs["max_retries"], 0)
        with patch.object(main, "client", None), patch.dict(os.environ, {"OPENAI_API_KEY": "test-placeholder"}), patch.object(main, "OpenAI", side_effect=ValueError("secret-placeholder")):
            response = self.client.post("/api/chat", json={"message": "질문"})
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("secret-placeholder", response.text)

    def test_bad_ai_response_is_not_saved(self):
        for result in [SimpleNamespace(choices=[]), SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None))]),
                       SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=" "))]),
                       SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="x" * 8001))])]:
            self.ai.chat.completions.create.return_value = result
            self.assertEqual(self.client.post("/api/chat", json={"message": "질문"}).status_code, 502)
        self.assertEqual(self.db.collection("evaluation_conversations").items, {})

    def test_unexpected_error_is_generic(self):
        with patch.object(self.db, "collection", side_effect=RuntimeError("secret-placeholder")):
            response = self.client.get("/api/data")
            self.assertEqual(response.status_code, 500)
            self.assertNotIn("secret-placeholder", response.text)


if __name__ == "__main__":
    unittest.main()
