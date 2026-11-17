"""Run manually to create ignored .env.auth; never generates an account/password."""
import getpass
import os
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from auth import password_hash


def main():
    destination = ROOT / ".env.auth"
    if destination.exists():
        raise SystemExit(".env.auth가 이미 있습니다. 자동으로 덮어쓰지 않습니다.")
    username = input("사용할 평가 계정 ID: ").strip()
    password = getpass.getpass("사용할 비밀번호 (표시되지 않음): ")
    confirmation = getpass.getpass("비밀번호 확인: ")
    if (not username or len(username) > 100 or any(char in username for char in "\r\n'$\\")
            or not password.strip() or len(password) < 12 or len(password) > 256 or password != confirmation):
        raise SystemExit("ID와 12~256자 비밀번호를 확인해 주세요. 파일은 생성하지 않았습니다.")
    content = (
        "APP_ENV=development\n"
        f"EVAL_USERNAME='{username}'\n"
        f"EVAL_PASSWORD_HASH='{password_hash(password)}'\n"
        f"AUTH_SECRET='{secrets.token_urlsafe(48)}'\n"
        "AUTH_VERSION=1\n"
        "AUTH_ALLOWED_ORIGINS=http://127.0.0.1:8000\n"
    )
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(content)
    print(".env.auth를 저장했습니다. 내용은 출력하지 않았습니다. Git에 포함하지 마세요.")


if __name__ == "__main__":
    main()
