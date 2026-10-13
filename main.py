import os
from dotenv import load_dotenv
from openai import OpenAI
import csv
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI
from pydantic import BaseModel
import firebase_admin
from firebase_admin import credentials, firestore

CSV_PATH = Path(__file__).resolve().parent / "robot_si_synthetic_2022_2026_revised.csv"

def load_project_data():
    with open(CSV_PATH, mode="r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        return list(reader)

app = FastAPI()

load_dotenv()

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL")
)

from pathlib import Path

if Path("firebase-service-account.json").is_file():
    cred = credentials.Certificate("firebase-service-account.json")
    firebase_admin.initialize_app(cred)
    db = firestore.client()
else:
    db = None
    print("Firebase 인증 파일 없음 - 개발 모드로 실행")

def doc_to_dict(doc):
    data = doc.to_dict()

    if data is None:
        return {}

    return data

class ProjectData(BaseModel):
    date: str
    value: int
    project_type: str
    duration_days: int
    memo: str

class ConversationMessage(BaseModel):
    role: str
    content: str


class ConversationCreate(BaseModel):
    title: str
    messages: list[ConversationMessage]

class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None

@app.get("/")
def read_root():
    return {"message": "로봇자동화 SI 프로젝트 실적 AI 비서 API"}

@app.post("/api/data")
def create_data(project: ProjectData):
    doc_ref = db.collection("data").document()

    doc_ref.set(project.model_dump())

    return {
        "message": "프로젝트 데이터가 추가되었습니다.",
        "id": doc_ref.id
    }

@app.get("/api/data")
def get_data():
    docs = db.collection("data").stream()

    result = []

    for doc in docs:
        item = doc_to_dict(doc)
        item["id"] = doc.id
        result.append(item)

    return {
        "count": len(result),
        "data": result
    }

@app.put("/api/data/{id}")
def update_data(id: str, project: ProjectData):
    doc_ref = db.collection("data").document(id)
    doc = doc_ref.get()

    if not doc.exists:
        return {
            "message": "해당 프로젝트 데이터를 찾을 수 없습니다."
        }

    doc_ref.update(project.model_dump())

    return {
        "message": "프로젝트 데이터가 수정되었습니다.",
        "id": id
    }

@app.delete("/api/data/{id}")
def delete_data(id: str):
    doc_ref = db.collection("data").document(id)
    doc = doc_ref.get()

    if not doc.exists:
        return {
            "message": "해당 프로젝트 데이터를 찾을 수 없습니다."
        }

    doc_ref.delete()

    return {
        "message": "프로젝트 데이터가 삭제되었습니다.",
        "id": id
    }

@app.get("/api/data/summary")
def get_data_summary():
    docs = list(db.collection("data").stream())

    if not docs:
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

    items = [doc_to_dict(doc) for doc in docs]
    items.sort(key=lambda x: x["date"])

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


@app.post("/api/conversations")
def create_conversation(conversation: ConversationCreate):
    conversation_id = str(uuid4())

    conversations[conversation_id] = {
        "id": conversation_id,
        "title": conversation.title,
        "messages": [
            message.model_dump()
            for message in conversation.messages
        ],
        "created_at": datetime.now().isoformat()
    }

    return {
        "message": "대화 기록이 저장되었습니다.",
        "id": conversation_id
    }

@app.get("/api/conversations")
def get_conversations():
    return {
        "count": len(conversations),
        "conversations": list(conversations.values())
    }

from fastapi import HTTPException


@app.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str):
    if conversation_id not in conversations:
        raise HTTPException(
            status_code=404,
            detail="해당 대화 기록을 찾을 수 없습니다."
        )

    return conversations[conversation_id]

@app.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: str):
    if conversation_id not in conversations:
        raise HTTPException(
            status_code=404,
            detail="해당 대화 기록을 찾을 수 없습니다."
        )

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
    if conversation_id not in conversations:
        raise HTTPException(
            status_code=404,
            detail="해당 대화 기록을 찾을 수 없습니다."
        )

    conversations[conversation_id]["title"] = conversation.title
    conversations[conversation_id]["messages"] = [
        message.model_dump()
        for message in conversation.messages
    ]

    return {
        "message": "대화 기록이 수정되었습니다.",
        "id": conversation_id
    }

@app.get("/api/chat/test")
def test_chat():
    response = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL"),
        messages=[
            {
                "role": "user",
                "content": "안녕하세요? 한 문장으로 자기소개해 주세요."
            }
        ]
    )

    return {
        "message": response.choices[0].message.content
    }

@app.post("/api/chat")
def chat(request: ChatRequest):
    # 기존 대화 이어가기
    if request.conversation_id is not None:
        if request.conversation_id not in conversations:
            raise HTTPException(
                status_code=404,
                detail="해당 대화 기록을 찾을 수 없습니다."
            )

        conversation_id = request.conversation_id
        messages = conversations[conversation_id]["messages"].copy()

    # 새로운 대화 시작
    else:
        conversation_id = str(uuid4())
        messages = []

    # 사용자 질문 추가
    messages.append({
        "role": "user",
        "content": request.message
    })

    # CSV 실적 데이터를 AI에게 전달
    project_context = get_project_context()

    # Python으로 계산한 연도별 실적
    yearly_summary = get_yearly_summary()

    # Python으로 계산한 월별 실적
    monthly_summary = get_monthly_summary()

    # Python으로 계산한 프로젝트 유형별 실적
    project_type_summary = get_project_type_summary()

    ai_messages = [
        {
            "role": "system",
            "content": (
                "당신은 로봇자동화 SI 프로젝트 실적 분석 AI 비서입니다.\n"
                "다음 CSV 데이터를 근거로 사용자의 질문에 답변하세요.\n"
                "데이터에 없는 수치나 사실은 만들어내지 마세요.\n"
                "금액 계산과 기간별 집계는 정확하게 수행하세요.\n\n"
                + project_context
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
    response = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL"),
        messages=ai_messages
    )

    answer = response.choices[0].message.content or ""

    # AI 답변 추가
    messages.append({
        "role": "assistant",
        "content": answer
    })

    # 대화 기록 자동 저장
    if request.conversation_id is None:
        conversations[conversation_id] = {
            "id": conversation_id,
            "title": request.message[:30],
            "messages": messages,
            "created_at": datetime.now().isoformat()
        }
    else:
        conversations[conversation_id]["messages"] = messages

    return {
        "conversation_id": conversation_id,
        "answer": answer
    }

@app.get("/api/data/csv-test")
def test_csv_data():
    projects = load_project_data()

    return {
        "total_count": len(projects),
        "sample": projects[:2]
    }

def get_project_context():
    projects = load_project_data()

    lines = [
        "다음은 2022~2026년 로봇자동화 SI 프로젝트의 실제 분석용 데이터입니다.",
        "금액 단위는 원이며, CSV에 없는 정보는 추측하지 마세요.",
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

def get_yearly_summary():
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

def get_monthly_summary():
    projects = load_project_data()
    summary = {}

    # 2022년 1월 ~ 2026년 12월 초기화
    for year in range(2022, 2027):
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

@app.get("/api/data/monthly-summary")
def monthly_summary():
    return get_monthly_summary()

def get_project_type_summary():
    projects = load_project_data()
    summary = {}

    for project in projects:
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
def project_type_summary():
    return get_project_type_summary()