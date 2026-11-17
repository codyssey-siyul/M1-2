import os
from dotenv import load_dotenv
from openai import OpenAI, APIError, APITimeoutError, RateLimitError
import json
import re
from typing import Literal
from pathlib import Path
from datetime import datetime, date
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, field_validator, model_validator, ValidationError
from firebase_config import create_firestore_client
from google.api_core.exceptions import GoogleAPIError, NotFound
from google.auth.exceptions import GoogleAuthError
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

MAX_QUESTION_CHARS = 2000
MAX_MESSAGE_CHARS = 8000
MAX_CONVERSATION_MESSAGES = 100
MAX_CONVERSATION_CHARS = 40000
MAX_AI_INPUT_CHARS = 80000


def output_token_limit():
    try:
        return max(64, min(3000, int(os.getenv("OPENAI_MAX_COMPLETION_TOKENS", "1200"))))
    except ValueError:
        return 1200


def validate_document_id(value):
    if (not isinstance(value, str) or not value.strip()
            or len(value.encode("utf-8")) > 1500 or "/" in value
            or value in {".", ".."} or re.fullmatch(r"__.*__", value)):
        raise ValueError("유효한 문서 ID를 입력해 주세요.")
    return value


def document(collection, id):
    try:
        validate_document_id(id)
    except ValueError:
        raise HTTPException(status_code=422, detail="유효한 문서 ID를 입력해 주세요.") from None
    return require_db().collection(collection).document(id)


def require_db():
    if db is None:
        raise HTTPException(status_code=503, detail="Firebase 연결 설정이 필요합니다.")
    return db


def load_project_data():
    """Single source for list, summaries, charts and AI; never fall back to CSV."""
    docs = require_db().collection("data").stream(timeout=30, retry=None)
    projects = []
    for doc in docs:
        item = doc_to_dict(doc)
        try:
            project = ProjectData.model_validate(item).model_dump()
            datetime.strptime(project["date"], "%Y-%m-%d")
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=503, detail="저장된 실적 데이터 형식을 확인해 주세요."
            ) from None
        project["id"] = doc.id
        projects.append(project)
    return sorted(projects, key=lambda project: (project["date"], project["id"]))

app = FastAPI()

# 웹페이지 및 CSS, JavaScript 파일 제공
@app.get("/app")
def serve_frontend():
    return FileResponse(BASE_DIR / "index.html")

# CSS 파일 제공
@app.get("/style.css")
def serve_css():
    return FileResponse(BASE_DIR / "style.css")

# JavaScript 파일 제공
@app.get("/script.js")
def serve_javascript():
    return FileResponse(BASE_DIR / "script.js")


@app.get("/data-manager.js")
def serve_data_manager():
    return FileResponse(BASE_DIR / "data-manager.js")

# Create the OpenAI client only when chat is requested.
client = None
db_configuration_failed = False
try:
    db = create_firestore_client()
except RuntimeError:
    # Keep the UI and API available to report a safe 503 on DB requests.
    db = None
    db_configuration_failed = True


def get_ai_client():
    global client
    if client is None:
        if not os.getenv("OPENAI_API_KEY"):
            raise HTTPException(status_code=503, detail="OpenAI API 설정이 필요합니다.")
        try:
            client = OpenAI(
                api_key=os.getenv("OPENAI_API_KEY"),
                base_url=os.getenv("OPENAI_BASE_URL"),
                timeout=30.0,
                max_retries=0,
            )
        except Exception:
            raise HTTPException(status_code=503, detail="OpenAI API 설정을 확인해 주세요.") from None
    return client


@app.exception_handler(GoogleAPIError)
@app.exception_handler(GoogleAuthError)
async def firestore_error_handler(request, exc):
    return JSONResponse(
        status_code=503,
        content={"detail": "Firestore 요청을 처리하지 못했습니다. 연결과 권한을 확인해 주세요."},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request, exc):
    # Do not echo submitted content, Pydantic input values or exception context.
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": list(error["loc"]), "type": error["type"],
         "msg": "입력값의 형식 또는 허용 범위를 확인해 주세요."}
        for error in exc.errors()
    ]})


@app.exception_handler(Exception)
async def unexpected_error_handler(request, exc):
    return JSONResponse(status_code=500, content={"detail": "요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요."})


def request_ai_answer(messages, *, diagnostic=False):
    if sum(len(message["content"]) for message in messages) > MAX_AI_INPUT_CHARS:
        raise HTTPException(status_code=413, detail="AI 입력 데이터가 허용 길이를 초과했습니다.")
    model = os.getenv("OPENAI_MODEL")
    if not model or not model.strip():
        raise HTTPException(status_code=503, detail="OpenAI 모델 설정이 필요합니다.")
    parameter = os.getenv("OPENAI_TOKEN_LIMIT_PARAMETER", "max_completion_tokens")
    if parameter not in {"max_completion_tokens", "max_tokens"}:
        raise HTTPException(status_code=503, detail="OpenAI 출력 제한 설정을 확인해 주세요.")
    limit = min(output_token_limit(), 64) if diagnostic else output_token_limit()
    try:
        response = get_ai_client().chat.completions.create(
            model=model, messages=messages, **{parameter: limit},
        )
    except APITimeoutError:
        raise HTTPException(status_code=504, detail="AI 응답 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.") from None
    except RateLimitError:
        raise HTTPException(status_code=429, detail="AI 사용 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.") from None
    except APIError:
        raise HTTPException(status_code=502, detail="AI 서비스 요청을 처리하지 못했습니다.") from None
    try:
        answer = response.choices[0].message.content
        if not isinstance(answer, str) or not answer.strip() or len(answer) > MAX_MESSAGE_CHARS:
            raise ValueError
    except (AttributeError, IndexError, TypeError, ValueError):
        raise HTTPException(status_code=502, detail="AI 서비스에서 유효한 답변을 받지 못했습니다.") from None
    return answer


def doc_to_dict(doc):
    data = doc.to_dict()

    if data is None:
        return {}

    return data

class ProjectData(BaseModel):
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="실제 존재하는 YYYY-MM-DD 날짜, 1900~2100년")
    value: int = Field(gt=0, le=10**15)
    project_type: str = Field(min_length=1, max_length=100)
    duration_days: int = Field(ge=1, le=36500)
    memo: str = Field(min_length=1, max_length=2000)

    @field_validator("date")
    @classmethod
    def valid_date(cls, value):
        parsed = date.fromisoformat(value)
        if not 1900 <= parsed.year <= 2100:
            raise ValueError("날짜는 1900~2100년 범위여야 합니다.")
        return value

    @field_validator("value", "duration_days", mode="before")
    @classmethod
    def integer_only(cls, value):
        # Keep integer strings compatible with CSV fixtures and existing clients.
        if isinstance(value, bool) or not (isinstance(value, int) or
                isinstance(value, str) and re.fullmatch(r"[0-9]+", value)):
            raise ValueError("양의 정수를 입력해 주세요.")
        return value

    @field_validator("project_type", "memo")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("공백만 입력할 수 없습니다.")
        return value

class ConversationMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)

    @field_validator("content")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("빈 메시지는 저장할 수 없습니다.")
        return value


class ConversationCreate(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    messages: list[ConversationMessage] = Field(min_length=1, max_length=MAX_CONVERSATION_MESSAGES)

    @field_validator("title")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("대화 제목을 입력해 주세요.")
        return value

    @model_validator(mode="after")
    def total_length(self):
        if sum(len(message.content) for message in self.messages) > MAX_CONVERSATION_CHARS:
            raise ValueError("대화 전체 길이가 한도를 초과했습니다.")
        return self

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    conversation_id: str | None = None

    @field_validator("message")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("질문을 입력해 주세요.")
        return value

    @field_validator("conversation_id")
    @classmethod
    def valid_id(cls, value):
        return validate_document_id(value) if value is not None else None

@app.get("/")
def read_root():
    return {"message": "로봇자동화 SI 프로젝트 실적 AI 비서 API"}

@app.post("/api/data")
def create_data(project: ProjectData):
    doc_ref = require_db().collection("data").document()

    doc_ref.set(project.model_dump(), timeout=15, retry=None)

    return {
        "message": "프로젝트 데이터가 추가되었습니다.",
        "id": doc_ref.id
    }

@app.get("/api/data")
def get_data():
    result = load_project_data()

    return {
        "count": len(result),
        "data": result
    }

@app.put("/api/data/{id}")
def update_data(id: str, project: ProjectData):
    doc_ref = document("data", id)
    doc = doc_ref.get(timeout=15, retry=None)

    if not doc.exists:
        raise HTTPException(status_code=404, detail="해당 프로젝트 데이터를 찾을 수 없습니다.")

    try:
        doc_ref.update(project.model_dump(), timeout=15, retry=None)
    except NotFound:
        raise HTTPException(status_code=404, detail="해당 프로젝트 데이터를 찾을 수 없습니다.") from None

    return {
        "message": "프로젝트 데이터가 수정되었습니다.",
        "id": id
    }

@app.delete("/api/data/{id}")
def delete_data(id: str):
    doc_ref = document("data", id)
    doc = doc_ref.get(timeout=15, retry=None)

    if not doc.exists:
        raise HTTPException(status_code=404, detail="해당 프로젝트 데이터를 찾을 수 없습니다.")

    doc_ref.delete(timeout=15, retry=None)

    return {
        "message": "프로젝트 데이터가 삭제되었습니다.",
        "id": id
    }

@app.get("/api/data/summary")
def get_data_summary():
    return build_data_summary(load_project_data())


def build_data_summary(projects):
    items = sorted(projects, key=lambda item: item["date"])
    if not items:
        return {
            "period": None,
            "count": 0,
            "total_value": 0,
            "average_value": "해당 없음",
            "max_value": None,
            "min_value": None,
            "monthly_summary": [],
            "recent_trend": "데이터 없음"
        }

    values = [item["value"] for item in items]

    total_value = sum(values)
    count = len(values)
    average_value = round(total_value / count)

    monthly_data = {}

    for item in items:
        month = item["date"][:7]

        if month not in monthly_data:
            monthly_data[month] = {
                "count": 0,
                "total_value": 0
            }

        monthly_data[month]["count"] += 1
        monthly_data[month]["total_value"] += item["value"]

    start_month = datetime.strptime(items[0]["date"][:7], "%Y-%m")
    end_month = datetime.strptime(items[-1]["date"][:7], "%Y-%m")

    monthly_summary = []

    current_year = start_month.year
    current_month = start_month.month

    while (current_year, current_month) <= (end_month.year, end_month.month):
        month_key = f"{current_year:04d}-{current_month:02d}"

        if month_key in monthly_data:
            data = monthly_data[month_key]

            monthly_summary.append({
                "month": month_key,
                "count": data["count"],
                "total_value": data["total_value"],
                "average_value": round(
                    data["total_value"] / data["count"]
                )
            })
        else:
            monthly_summary.append({
                "month": month_key,
                "count": 0,
                "total_value": 0,
                "average_value": "해당 없음"
            })

        current_month += 1

        if current_month == 13:
            current_month = 1
            current_year += 1

    # 최근 3개월과 직전 3개월의 총 수주금액 비교
    if len(monthly_summary) >= 6:
        previous_3_months = monthly_summary[-6:-3]
        recent_3_months = monthly_summary[-3:]

        previous_total = sum(
            month["total_value"] for month in previous_3_months
        )
        recent_total = sum(
            month["total_value"] for month in recent_3_months
        )

        if previous_total == 0:
            if recent_total == 0:
                recent_trend = "최근 3개월과 직전 3개월 모두 수주 실적이 없습니다."
            else:
                recent_trend = "직전 3개월에는 수주가 없었고, 최근 3개월에는 수주가 발생했습니다."
        else:
            change_rate = round(
                ((recent_total - previous_total) / previous_total) * 100,
                1
            )

            if change_rate > 0:
                recent_trend = f"최근 3개월 수주금액은 직전 3개월 대비 {change_rate}% 증가했습니다."
            elif change_rate < 0:
                recent_trend = f"최근 3개월 수주금액은 직전 3개월 대비 {abs(change_rate)}% 감소했습니다."
            else:
                recent_trend = "최근 3개월 수주금액은 직전 3개월과 동일합니다."
    else:
        recent_trend = "추세를 비교하기 위한 데이터가 부족합니다."

    return {
        "period": {
            "start": items[0]["date"],
            "end": items[-1]["date"]
        },
        "count": count,
        "total_value": total_value,
        "average_value": average_value,
        "max_value": max(values),
        "min_value": min(values),
        "monthly_summary": monthly_summary,
        "recent_trend": recent_trend
    }

from uuid import uuid4

# Firebase 연결 전 임시 대화 저장소
conversations = {}

# 대화 기록 저장 (Firebase 연결 시 영구 저장)
def save_conversation(conversation_id, conversation_data):
    if db_configuration_failed:
        require_db()
    if db is not None:
        document("conversations", conversation_id).set(
            conversation_data, timeout=15, retry=None,
        )
    else:
        conversations[conversation_id] = conversation_data

# 대화 기록 1건 조회
def load_conversation(conversation_id):
    try:
        validate_document_id(conversation_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="유효한 문서 ID를 입력해 주세요.") from None
    if db_configuration_failed:
        require_db()
    if db is not None:
        doc = document("conversations", conversation_id).get(timeout=15, retry=None)

        if doc.exists:
            return doc.to_dict()
        return None

    return conversations.get(conversation_id)

# 전체 대화 기록 조회
def load_all_conversations():
    if db_configuration_failed:
        require_db()
    if db is not None:
        docs = db.collection("conversations").stream(timeout=30, retry=None)
        return [doc.to_dict() for doc in docs]

    return list(conversations.values())

@app.post("/api/conversations")
def create_conversation(conversation: ConversationCreate):
    conversation_id = str(uuid4())

    conversation_data = {
        "id": conversation_id,
        "title": conversation.title,
        "messages": [
            message.model_dump()
            for message in conversation.messages
        ],
        "created_at": datetime.now().isoformat()
    }

    save_conversation(conversation_id, conversation_data)

    return {
        "message": "대화 기록이 저장되었습니다.",
        "id": conversation_id
    }

@app.get("/api/conversations")
def get_conversations():
    conversation_list = load_all_conversations()

    return {
        "count": len(conversation_list),
        "conversations": conversation_list
    }



@app.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str):
    conversation = load_conversation(conversation_id)

    if conversation is None:
        raise HTTPException(
            status_code=404,
            detail="해당 대화 기록을 찾을 수 없습니다."
        )

    return conversation

@app.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: str):
    conversation = load_conversation(conversation_id)

    if conversation is None:
        raise HTTPException(
            status_code=404,
            detail="해당 대화 기록을 찾을 수 없습니다."
        )

    if db is not None:
        document("conversations", conversation_id).delete(timeout=15, retry=None)
    else:
        del conversations[conversation_id]

    return {
        "message": "대화 기록이 삭제되었습니다.",
        "id": conversation_id
    }

@app.put("/api/conversations/{conversation_id}")
def update_conversation(
    conversation_id: str,
    conversation: ConversationCreate
):
    conversation_data = load_conversation(conversation_id)

    if conversation_data is None:
        raise HTTPException(
            status_code=404,
            detail="해당 대화 기록을 찾을 수 없습니다."
        )

    conversation_data["title"] = conversation.title
    conversation_data["messages"] = [
        message.model_dump()
        for message in conversation.messages
    ]

    save_conversation(conversation_id, conversation_data)

    return {
        "message": "대화 기록이 수정되었습니다.",
        "id": conversation_id
    }

@app.get("/api/chat/test")
def test_chat():
    answer = request_ai_answer(
        [
            {
                "role": "user",
                "content": "안녕하세요? 한 문장으로 자기소개해 주세요."
            }
        ], diagnostic=True,
    )

    return {
        "message": answer
    }

@app.post("/api/chat")
def chat(request: ChatRequest):
    # 기존 대화 이어가기
    if request.conversation_id is not None:
        conversation_data = load_conversation(request.conversation_id)

        if conversation_data is None:
            raise HTTPException(
                status_code=404,
                detail="해당 대화 기록을 찾을 수 없습니다."
            )

        conversation_id = request.conversation_id
        try:
            stored = ConversationCreate.model_validate(conversation_data)
        except ValidationError:
            raise HTTPException(status_code=503, detail="저장된 대화 형식을 확인해 주세요.") from None
        messages = [message.model_dump() for message in stored.messages]

    # 새로운 대화 시작
    else:
        conversation_id = str(uuid4())
        messages = []

    # 사용자 질문 추가
    messages.append({
        "role": "user",
        "content": request.message
    })

    # Reserve room for the assistant reply before any paid request is made.
    if (len(messages) + 1 > MAX_CONVERSATION_MESSAGES or
            sum(len(message["content"]) for message in messages) + MAX_MESSAGE_CHARS > MAX_CONVERSATION_CHARS):
        raise HTTPException(status_code=413, detail="대화 길이 한도에 도달했습니다. 새 채팅을 시작해 주세요.")

    # Read once so every AI summary uses the same Firestore snapshot.
    projects = load_project_data()
    data_summary = build_data_summary(projects)
    project_context = get_project_context(projects)
    yearly_summary = get_yearly_summary(projects)
    monthly_summary = get_monthly_summary(projects)
    project_type_summary = get_project_type_summary(projects=projects)

    ai_messages = [
        {
            "role": "system",
            "content": (
                "당신은 로봇자동화 SI 프로젝트 실적 분석 AI 비서입니다.\n"
                "다음 Firestore 실적 데이터와 요약을 근거로 사용자의 질문에 답변하세요.\n"
                "데이터에 없는 수치나 사실은 만들어내지 마세요.\n"
                "금액 계산과 기간별 집계는 정확하게 수행하세요.\n\n"
                + "최근 추세는 마지막 데이터 월 기준이며 현재 날짜 기준이 아닙니다.\n"
                + "데이터 요약 (/api/data/summary와 동일한 계산):\n"
                + json.dumps(data_summary, ensure_ascii=False)
                + "\n\n" + project_context
                + "\n\nPython으로 계산한 정확한 연도별 집계 결과:\n"
                + str(yearly_summary)
                + "\n\nPython으로 계산한 정확한 월별 집계 결과:\n"
                + str(monthly_summary)
                + "\n\nPython으로 계산한 정확한 프로젝트 유형별 집계 결과:\n"
                + str(project_type_summary)
            )
        }
    ] + messages

    # 실제 AI 답변 생성
    answer = request_ai_answer(ai_messages)

    # AI 답변 추가
    messages.append({
        "role": "assistant",
        "content": answer
    })

    # 대화 기록 자동 저장
    if request.conversation_id is None:
        conversation_data = {
            "id": conversation_id,
            "title": request.message.strip()[:30],
            "messages": messages,
            "created_at": datetime.now().isoformat()
        }
    else:
        conversation_data["messages"] = messages

    save_conversation(conversation_id, conversation_data)

    return {
        "conversation_id": conversation_id,
        "answer": answer
    }

@app.get("/api/data/csv-test")
def test_csv_data():
    projects = load_project_data()

    return {
        "total_count": len(projects),
        "sample": [
            {key: str(value) for key, value in project.items() if key != "id"}
            for project in projects[:2]
        ]
    }

def get_project_context(projects=None):
    if projects is None:
        projects = load_project_data()

    lines = [
        "다음은 Firestore에 저장된 로봇자동화 SI 프로젝트 분석용 데이터입니다.",
        "금액 단위는 원이며, 제공된 데이터에 없는 정보는 추측하지 마세요.",
        "수주가 없는 달은 건수와 합계가 0이고, 평균은 '해당 없음'입니다.",
        "프로젝트 실적 데이터:"
    ]

    for project in projects:
        lines.append(
            f"날짜: {project['date']}, "
            f"금액: {project['value']}원, "
            f"유형: {project['project_type']}, "
            f"기간: {project['duration_days']}일, "
            f"메모: {project['memo']}"
        )

    return "\n".join(lines)

def get_yearly_summary(projects=None):
    if projects is None:
        projects = load_project_data()
    summary = {}

    for project in projects:
        year = project["date"][:4]
        value = int(project["value"])

        if year not in summary:
            summary[year] = {
                "count": 0,
                "total_value": 0
            }

        summary[year]["count"] += 1
        summary[year]["total_value"] += value

    for year, data in summary.items():
        data["average_value"] = (
            data["total_value"] // data["count"]
            if data["count"] > 0
            else "해당 없음"
        )

    return summary

@app.get("/api/data/yearly-summary")
def yearly_summary():
    return get_yearly_summary()

def get_monthly_summary(projects=None):
    if projects is None:
        projects = load_project_data()
    summary = {}

    # Preserve the existing 60-month range, extending it for new data years.
    years = [int(project["date"][:4]) for project in projects]
    for year in range(min([2022] + years), max([2026] + years) + 1):
        for month in range(1, 13):
            key = f"{year}-{month:02d}"
            summary[key] = {
                "count": 0,
                "total_value": 0,
                "average_value": "해당 없음"
            }

    # 실제 수주 데이터 집계
    for project in projects:
        month = project["date"][:7]
        value = int(project["value"])

        if month in summary:
            summary[month]["count"] += 1
            summary[month]["total_value"] += value

    # 월별 평균 계산
    for data in summary.values():
        if data["count"] > 0:
            data["average_value"] = (
                data["total_value"] // data["count"]
            )

    return summary


def get_project_type_summary(year=None, projects=None):
    if projects is None:
        projects = load_project_data()
    summary = {}

    for project in projects:
        # 연도가 선택된 경우 해당 연도 데이터만 집계
        if year is not None:
            project_year = str(project["date"])[:4]

            if project_year != str(year):
                continue

        project_type = project["project_type"]
        value = int(project["value"])

        if project_type not in summary:
            summary[project_type] = {
                "count": 0,
                "total_value": 0
            }

        summary[project_type]["count"] += 1
        summary[project_type]["total_value"] += value

    for data in summary.values():
        data["average_value"] = (
            data["total_value"] // data["count"]
            if data["count"] > 0
            else "해당 없음"
        )

    return summary


@app.get("/api/data/project-type-summary")
def project_type_summary(year: int | None = None):
    return get_project_type_summary(year)

@app.get("/api/data/monthly-summary")
def monthly_summary():
    return get_monthly_summary()
