"""Firebase authentication only; initialization never uploads or migrates data."""

import json
import os
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, firestore

BASE_DIR = Path(__file__).resolve().parent


def create_firestore_client():
    """Use JSON env, a configured key path, or the existing local key file."""
    raw_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    key_path = (
        os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH")
        or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    )
    if not raw_json and not key_path:
        local_key = BASE_DIR / "firebase-service-account.json"
        if not local_key.is_file():
            return None
        key_path = str(local_key)

    try:
        if raw_json:
            credential = credentials.Certificate(json.loads(raw_json))
        else:
            path = Path(key_path).expanduser()
            if not path.is_absolute():
                path = BASE_DIR / path
            credential = credentials.Certificate(str(path))
        try:
            app = firebase_admin.get_app()
        except ValueError:
            app = firebase_admin.initialize_app(credential)
        return firestore.client(
            app=app,
            database_id=os.getenv("FIRESTORE_DATABASE_ID", "(default)"),
        )
    except Exception:
        # SDK errors may include credential input. Never expose their text.
        raise RuntimeError("Firebase 인증 설정을 확인해 주세요.") from None
