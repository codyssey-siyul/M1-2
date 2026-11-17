"""Manual UI preview: python tests/mock_ui_server.py (loopback port 8765).

Uses only in-memory records and mocked GPT. Never initializes Firebase credentials.
Stopping the process discards all preview edits. Do not use for production.
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from test_data_integration import main, Database
from auth import SecurityService
from auth_store import MemorySecurityStore
from security_helpers import TEST_CONFIG
from dataclasses import replace

main.app.state.security = SecurityService(
    replace(TEST_CONFIG, origins=("http://127.0.0.1:8765",)), MemorySecurityStore()
)

main.db = Database({
    "preview_001": {"date": "2026-09-08", "value": 180000000, "project_type": "로봇 용접 자동화", "duration_days": 90, "memo": "모의 프로젝트: 용접 공정 자동화 구축"},
    "preview_002": {"date": "2025-11-14", "value": 120000000, "project_type": "물류 이송 자동화", "duration_days": 60, "memo": "모의 프로젝트: 자율 이송 시스템 설치"},
    "preview_003": {"date": "2024-06-20", "value": 80000000, "project_type": "검사 자동화", "duration_days": 45, "memo": "모의 프로젝트: 비전 검사 공정 개선"},
})
main.client = Mock()
main.client.chat.completions.create.return_value = SimpleNamespace(
    choices=[SimpleNamespace(message=SimpleNamespace(content="모의 테스트 답변입니다. 실제 GPT는 호출하지 않았습니다."))]
)
os.environ["OPENAI_MODEL"] = "mock-ui-only"
main.db.collection("evaluation_conversations").items["preview_talk"] = {
    "id": "preview_talk", "title": "수주 실적 확인 (모의 대화)", "created_at": "2026-11-17T09:00:00",
    "messages": [{"role": "user", "content": "총 수주금액을 알려줘"}, {"role": "assistant", "content": "모의 데이터 3건의 총 수주금액은 380,000,000원입니다."}],
}
preview = FastAPI()


@preview.get("/app", response_class=HTMLResponse)
def preview_page():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    return html.replace("프로젝트 수주 실적을 AI에게 질문해 보세요.", "모의 테스트 전용 · 실제 Firestore와 GPT에 연결하지 않습니다.")


preview.mount("/", main.app)

if __name__ == "__main__":
    uvicorn.run(preview, host="127.0.0.1", port=8765, access_log=False)
