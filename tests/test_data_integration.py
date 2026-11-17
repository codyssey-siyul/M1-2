"""Offline integration tests: no real credentials, Firestore writes or GPT calls."""

import copy
import csv
import importlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from security_helpers import authenticated_client
from google.api_core.exceptions import ServiceUnavailable

import firebase_config

with patch.object(firebase_config, "create_firestore_client", return_value=None):
    main = importlib.import_module("main")


class Snapshot:
    def __init__(self, id, data):
        self.id = id
        self.data = copy.deepcopy(data)
        self.exists = data is not None

    def to_dict(self):
        return copy.deepcopy(self.data)


class Document:
    def __init__(self, collection, id):
        self.collection = collection
        self.id = id

    def get(self, **kwargs):
        return Snapshot(self.id, self.collection.items.get(self.id))

    def set(self, data, **kwargs):
        self.collection.items[self.id] = copy.deepcopy(data)

    def update(self, data, **kwargs):
        self.collection.items[self.id].update(copy.deepcopy(data))

    def delete(self, **kwargs):
        del self.collection.items[self.id]


class Collection:
    def __init__(self, items=None):
        self.items = copy.deepcopy(items or {})
        self.reads = 0

    def stream(self, **kwargs):
        self.reads += 1
        return [Snapshot(id, data) for id, data in self.items.items()]

    def document(self, id=None):
        return Document(self, id or f"test_{len(self.items) + 1}")


class Database:
    def __init__(self, items):
        self.collections = {"data": Collection(items), "conversations": Collection(), "evaluation_data": Collection(), "evaluation_conversations": Collection()}

    def collection(self, name):
        return self.collections[name]


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "robot_si_synthetic_2022_2026_revised.csv"
        with path.open(encoding="utf-8-sig", newline="") as file:
            self.rows = [main.ProjectData.model_validate(row).model_dump() for row in csv.DictReader(file)]
        self.db = Database({f"project_{i:03d}": row for i, row in enumerate(self.rows, 1)})
        self.db_patch = patch.object(main, "db", self.db)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.client = authenticated_client(self, main)
        model_patch = patch.dict(os.environ, {"OPENAI_MODEL": "test-model"})
        model_patch.start()
        self.addCleanup(model_patch.stop)

    def test_csv_baseline_and_existing_chart_contracts(self):
        response = self.client.get("/api/data")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 120)
        summary = self.client.get("/api/data/summary").json()
        self.assertEqual(summary["total_value"], 13408000000)
        self.assertEqual(summary["period"], {"start": "2022-02-03", "end": "2026-09-08"})
        self.assertEqual(summary["max_value"], 472200000)
        self.assertEqual(summary["min_value"], 18700000)
        yearly = self.client.get("/api/data/yearly-summary").json()
        monthly = self.client.get("/api/data/monthly-summary").json()
        self.assertEqual(len(monthly), 60)
        self.assertEqual(monthly["2023-07"], {"count": 0, "total_value": 0, "average_value": "해당 없음"})
        for year in range(2022, 2027):
            filtered = [row for row in self.rows if row["date"].startswith(str(year))]
            total = sum(row["value"] for row in filtered)
            self.assertEqual(yearly[str(year)], {"count": len(filtered), "total_value": total, "average_value": total // len(filtered)})
            types = self.client.get(f"/api/data/project-type-summary?year={year}").json()
            self.assertEqual(sum(row["total_value"] for row in types.values()), total)
            self.assertEqual(sum(row["count"] for row in types.values()), len(filtered))
        self.assertEqual(sum(row["total_value"] for row in monthly.values()), summary["total_value"])
        diagnostic = self.client.get("/api/data/csv-test").json()
        self.assertEqual(diagnostic["total_count"], 120)
        self.assertEqual(set(diagnostic["sample"][0]), set(self.rows[0]))

    def test_crud_immediately_updates_all_analytics(self):
        row = dict(self.rows[0], date="2027-02-01", value=100)
        result = self.client.post("/api/data", json=row)
        self.assertEqual(result.status_code, 200)
        id = result.json()["id"]
        self.assertEqual(self.client.get("/api/data/summary").json()["count"], 121)
        self.assertEqual(self.client.get("/api/data/yearly-summary").json()["2027"]["total_value"], 100)
        self.assertEqual(self.client.get("/api/data/monthly-summary").json()["2027-02"]["total_value"], 100)
        self.assertEqual(self.client.get("/api/data/project-type-summary?year=2027").json()[row["project_type"]]["count"], 1)
        row["value"] = 200
        self.assertEqual(self.client.put(f"/api/data/{id}", json=row).status_code, 200)
        self.assertEqual(self.client.get("/api/data/summary").json()["total_value"], 13408000200)
        self.assertEqual(self.client.delete(f"/api/data/{id}").status_code, 200)
        self.assertEqual(self.client.get("/api/data/summary").json()["total_value"], 13408000000)

    def test_ai_uses_one_firestore_read_and_saves_conversation(self):
        fake_ai = Mock()
        fake_ai.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="검증용 답변"))]
        )
        with patch.object(main, "client", fake_ai):
            result = self.client.post("/api/chat", json={"message": "총액은?"})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(self.db.collection("data").reads, 1)
            prompt = fake_ai.chat.completions.create.call_args.kwargs["messages"][0]["content"]
            self.assertIn(json.dumps(main.build_data_summary(self.rows), ensure_ascii=False), prompt)
            self.assertIn("Firestore", prompt)
            self.assertNotIn("CSV", prompt)
            id = result.json()["conversation_id"]
            self.assertEqual(self.client.get(f"/api/conversations/{id}").json()["messages"][-1]["content"], "검증용 답변")
            self.assertEqual(self.client.post("/api/chat", json={"message": "이어서", "conversation_id": id}).status_code, 200)
            self.assertEqual(len(self.client.get(f"/api/conversations/{id}").json()["messages"]), 4)
        self.assertEqual(self.client.get("/api/conversations").json()["count"], 1)
        self.assertEqual(self.client.delete(f"/api/conversations/{id}").status_code, 200)
        self.assertEqual(self.client.get(f"/api/conversations/{id}").status_code, 404)

    def test_no_db_returns_503_without_csv_fallback(self):
        with patch.object(main, "db", None):
            for route in ["/api/data", "/api/data/summary", "/api/data/yearly-summary", "/api/data/monthly-summary", "/api/data/project-type-summary"]:
                self.assertEqual(self.client.get(route).status_code, 503)
            self.assertEqual(self.client.post("/api/chat", json={"message": "질문"}).status_code, 503)

    def test_invalid_stored_data_is_not_silently_skipped(self):
        self.db.collection("data").items["project_001"]["date"] = "invalid"
        response = self.client.get("/api/data/summary")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("invalid", response.text)

    def test_missing_openai_key_does_not_block_dashboard(self):
        with patch.object(main, "client", None), patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            self.assertEqual(self.client.get("/api/data/yearly-summary").status_code, 200)
            self.assertEqual(self.client.post("/api/chat", json={"message": "질문"}).status_code, 503)

    def test_explicit_conversation_create_and_update(self):
        data = {"title": "대화", "messages": [{"role": "user", "content": "질문"}]}
        result = self.client.post("/api/conversations", json=data)
        self.assertEqual(result.status_code, 200)
        id = result.json()["id"]
        data["title"] = "변경"
        self.assertEqual(self.client.put(f"/api/conversations/{id}", json=data).status_code, 200)
        self.assertEqual(self.client.get(f"/api/conversations/{id}").json()["title"], "변경")

    def test_empty_data_and_firestore_error(self):
        self.db.collection("data").items.clear()
        self.assertEqual(self.client.get("/api/data/summary").json()["count"], 0)
        self.assertEqual(self.client.get("/api/data/yearly-summary").json(), {})
        self.assertEqual(len(self.client.get("/api/data/monthly-summary").json()), 60)
        with patch.object(self.db.collection("data"), "stream", side_effect=ServiceUnavailable("secret-placeholder")):
            response = self.client.get("/api/data/summary")
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("secret-placeholder", response.text)

    def test_ui_and_docs_routes(self):
        for route in ["/app", "/style.css", "/script.js", "/data-manager.js", "/docs", "/openapi.json"]:
            self.assertEqual(self.client.get(route).status_code, 200)


class AuthenticationTests(unittest.TestCase):
    def test_existing_local_key_fallback(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(Path, "is_file", return_value=True), patch.object(firebase_config.credentials, "Certificate") as certificate, patch.object(firebase_config.firebase_admin, "get_app", side_effect=ValueError), patch.object(firebase_config.firebase_admin, "initialize_app") as initialize, patch.object(firebase_config.firestore, "client"):
            firebase_config.create_firestore_client()
            certificate.assert_called_once_with(str(firebase_config.BASE_DIR / "firebase-service-account.json"))
            initialize.assert_called_once()

    def test_no_credentials_returns_none(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(Path, "is_file", return_value=False), patch.object(firebase_config.credentials, "Certificate") as certificate:
            self.assertIsNone(firebase_config.create_firestore_client())
            certificate.assert_not_called()

    def test_json_env_takes_precedence(self):
        with patch.dict(os.environ, {"FIREBASE_SERVICE_ACCOUNT_JSON": '{"test": "placeholder"}', "FIREBASE_SERVICE_ACCOUNT_PATH": "unused"}, clear=True), patch.object(firebase_config.credentials, "Certificate") as certificate, patch.object(firebase_config.firebase_admin, "get_app"), patch.object(firebase_config.firestore, "client"):
            firebase_config.create_firestore_client()
            certificate.assert_called_once_with({"test": "placeholder"})

    def test_path_env_and_database(self):
        for variable in ["FIREBASE_SERVICE_ACCOUNT_PATH", "GOOGLE_APPLICATION_CREDENTIALS"]:
            with self.subTest(variable=variable), patch.dict(os.environ, {variable: "test-key.json", "FIRESTORE_DATABASE_ID": "test-db"}, clear=True), patch.object(firebase_config.credentials, "Certificate") as certificate, patch.object(firebase_config.firebase_admin, "get_app"), patch.object(firebase_config.firestore, "client") as client:
                firebase_config.create_firestore_client()
                certificate.assert_called_once_with(str(firebase_config.BASE_DIR / "test-key.json"))
                self.assertEqual(client.call_args.kwargs["database_id"], "test-db")

    def test_bad_config_does_not_expose_input_or_fall_back(self):
        with patch.dict(os.environ, {"FIREBASE_SERVICE_ACCOUNT_JSON": "secret-placeholder"}, clear=True):
            with self.assertRaises(RuntimeError) as caught:
                firebase_config.create_firestore_client()
            self.assertNotIn("secret-placeholder", str(caught.exception))
            self.assertTrue(caught.exception.__suppress_context__)


if __name__ == "__main__":
    unittest.main()
