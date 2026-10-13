import csv
import firebase_admin
from firebase_admin import credentials, firestore


# Firebase 연결
cred = credentials.Certificate("firebase-service-account.json")
firebase_admin.initialize_app(cred)

db = firestore.client()


CSV_FILE = "robot_si_synthetic_2022_2026_revised.csv"


with open(CSV_FILE, "r", encoding="utf-8-sig") as file:
    reader = csv.DictReader(file)

    count = 0

    for index, row in enumerate(reader, start=1):
        data = {
            "date": row["date"],
            "value": int(row["value"]),
            "project_type": row["project_type"],
            "duration_days": int(row["duration_days"]),
            "memo": row["memo"],
        }

        document_id = f"project_{index:03d}"

        db.collection("data").document(document_id).set(data)

        count += 1


print(f"✅ Firestore 업로드 완료: {count}건")