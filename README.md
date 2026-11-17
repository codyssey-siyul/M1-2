# 로봇자동화 SI 실적 AI 비서

FastAPI·Firestore·OpenAI와 HTML/CSS/JavaScript·Chart.js로 구성한 과제 평가용 서비스다. 공통 계정 로그인 후 수주 데이터·대시보드·실제 GPT 채팅·평가용 대화 기록을 사용한다.

## 데이터 보호

| 컬렉션 | 처리 |
|---|---|
| `data` | 기존 120건 읽기 전용. API 수정·삭제 403 |
| `evaluation_data` | 새 평가 프로젝트 CRUD. 서버 생성 `eval_` ID 사용 |
| `conversations` | 기존 기록 보존. 평가용 API에서 접근하지 않음 |
| `evaluation_conversations` | 평가용 대화 저장·조회·수정·삭제 |
| `auth_sessions`, `auth_limits` | 해시 세션·만료·전역 로그인/AI 제한·동시 호출 lease |

목록·요약·그래프·GPT 컨텍스트는 `data`와 `evaluation_data`를 함께 읽는다. 평가자는 새 프로젝트를 추가한 뒤 그 프로젝트를 수정·삭제한다. 기존 프로젝트는 화면에서도 읽기 전용으로 표시한다. `upload_data.py`는 재업로드 방지를 위해 비활성화했다.

## 로컬 설정

기존 `.env`와 Firebase 인증 JSON을 덮어쓰지 않는다. 아래 명령은 사용자가 실제 계정을 정한 뒤 **직접 실행**한다.

```powershell
.\.venv\Scripts\python.exe tools\setup_local_auth.py
```

도구는 사용자가 입력한 ID·비밀번호로 검증용 해시와 세션 비밀값을 만들어 새 `.env.auth`에만 저장한다. 실제 비밀번호는 출력·저장하지 않는다. 기존 `.env.auth`가 있으면 덮어쓰지 않는다. 파일은 Git에서 제외되며 채팅에 붙여넣지 않는다. Windows에서는 파일 접근 권한과 동기화 위치를 사용자가 관리한다.

설정 우선순위는 프로세스 환경변수 → `.env.auth` → `.env`다. 서버를 재시작해야 반영된다. 개발 모드라도 인증을 생략하지 않는다.

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

- 화면: `http://127.0.0.1:8000/app`
- Swagger: 같은 호스트에서 로그인 후 `http://127.0.0.1:8000/docs`
- `localhost`와 `127.0.0.1`을 혼용하지 않는다.
- 실제 서버에서 로그인하는 것부터 `auth_*` 컬렉션 쓰기가 발생한다. 실제 CRUD·채팅 검증은 별도 승인 후 진행한다.

계정 설정이 없으면 업무 API는 503으로 차단된다. 기본 계정·기본 비밀번호·무인증 시연 전환은 제공하지 않는다.

## 인증과 제한

- `/api/auth/login`, `/api/auth/me`, `/api/auth/logout` 제공.
- 운영 쿠키: `__Host-m1_session`, HttpOnly·Secure·SameSite=Lax·Path=/, Domain 미설정.
- 명시적 로컬 개발 모드: `m1_session`, HttpOnly·SameSite=Lax, HTTP용 Secure=False.
- 절대 만료 8시간, 유휴 만료 60분. `/auth/me` 확인만으로 유휴 만료를 연장하지 않는다.
- POST·PUT·DELETE는 정확한 Origin과 세션 기반 CSRF 헤더를 검사한다. 로그인은 Origin·JSON·본문 크기와 시도 횟수를 검사한다.
- 기본 로그인 제한: 접속 peer당 10분 10회, 전역 10분 60회. 임의 전달 IP 헤더를 신뢰하지 않는다. 프록시 peer를 공유하는 환경에서는 사용자들이 IP 제한을 공유할 수 있다.
- 기본 AI 제한: 세션당 분 3회, 전체 분 5회·UTC 일 100회, 동시 1개. 카운터는 Firestore 트랜잭션으로 저장한다. 실패한 GPT 시도도 환불하지 않는다.
- 출력 기본 1,200토큰, 설정 범위 64~3,000. 입력은 질문·대화·프롬프트 문자 및 UTF-8 크기로 추가 제한한다. 입력 토큰을 정확히 계수하는 기능은 아니다.
- SDK 타임아웃 30초·자동 재시도 0회. lease는 120초 후 장애 복구되며, 응답/저장 결과가 불확실한 요청은 자동 재전송하지 않는다.
- `/api/chat/test`는 모든 환경에서 404이며 GPT를 호출하지 않는다.
- 요청 횟수 제한은 금액 상한이 아니다. 실제 GPT 비용·사용량은 사용자 OpenAI 프로젝트에서 관리한다.

## Render 배포 준비

`render.yaml`, `.python-version`, 고정 버전 `requirements.txt`를 준비했다. 실제 배포는 아직 수행하지 않았다.

- Build: `pip install -r requirements.txt`
- Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Health: `/health` — 인증 설정·DB 클라이언트 구성 여부 확인. 실제 DB RPC 권한/연결 성공까지 보장하는 상태 검사는 아니다.
- Python: 로컬에서 검증한 3.14.6 지정. Render Linux 설치·실행은 별도 검증 필요.

사용자가 Render에 직접 설정할 변수:

| 변수 | 설정 |
|---|---|
| `APP_ENV` | `production` |
| `EVAL_USERNAME`, `EVAL_PASSWORD_HASH` | 사용자 지정 계정·검증용 해시 |
| `AUTH_SECRET`, `AUTH_VERSION` | 충분한 난수 비밀값·세션 무효화 버전 |
| `AUTH_ALLOWED_ORIGINS` | 정확한 Vercel 운영 Origin. Render `/app`·`/docs`도 쓸 경우 Render Origin도 추가 |
| Firebase 인증 변수 | JSON 비밀 환경변수 또는 Secret File 경로 중 하나 |
| `FIRESTORE_DATABASE_ID` | 기존 `(default)` |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | 실제 키·모델, 서버에서만 관리 |
| 출력·로그인·AI 제한 변수 | `.env.example` 참조. 잘못된 인증 제한 값은 503 |

해시와 비밀값은 로컬 파일에서 사용자가 플랫폼에 직접 등록하며, dotenv 파일의 따옴표는 값에 포함하지 않는다. 비밀번호를 바꾸면 해시와 `AUTH_VERSION`도 변경해 기존 세션을 무효화한다.

## Vercel 동일 출처 프록시

브라우저의 `/api/*`는 Vercel rewrite를 거쳐 Render로 전달된다. 쿠키·CSRF·본문·응답 Set-Cookie가 실제 배포에서 보존되는지 확인해야 한다. Render 직접 요청도 동일 인증을 요구한다.

`vercel.json`은 정적 설정이라 빌드 중 환경변수 문자열 치환에 의존하지 않는다. 현재 API 프록시는 `https://m1-2-8d9s.onrender.com`으로 설정되어 있다. **Vercel의 `BACKEND_ORIGIN`도 같은 주소로 설정해야 한다.** 백엔드 주소를 변경할 때는 아래 명령으로 설정을 다시 준비한다. 이 명령은 로컬 파일 준비일 뿐 배포하지 않는다.

```powershell
$env:BACKEND_ORIGIN="https://m1-2-8d9s.onrender.com"
node tools/build_frontend.mjs --configure
```

그 후 별도 승인받아 변경한 공개 `vercel.json`을 커밋한다. Vercel은 Framework Other, Output Directory `dist`, Build Command `node tools/build_frontend.mjs`로 설정하고 동일한 `BACKEND_ORIGIN`을 제공한다. 주소와 설정이 다르면 빌드는 실패한다. 배포 전에 `dist`의 예상치 못한 파일은 사용자가 확인한다.

출력 허용 파일은 `index.html`, `style.css`, `auth-client.js`, `script.js`, `data-manager.js`뿐이다. 비밀번호·해시·AUTH_SECRET·OpenAI/Firebase 키는 Vercel에 제공하지 않는다. `/api/*` 응답은 no-store로 처리한다. Preview 도메인은 기본 허용하지 않는다.

Render에서 직접 Swagger를 볼 경우 해당 Render `/app`에서 먼저 로그인한다. Vercel 쿠키는 다른 호스트인 Render의 직접 페이지에 자동 공유되지 않는다.

## 모의 테스트

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node tests/test_chat_ui.js
node tests/test_data_ui.js
node tests/test_auth_ui.js
```

모든 테스트는 주입한 모의 DB·모의 GPT와 고정된 **테스트 전용** 계정을 사용한다. 테스트 계정은 실제 서비스 기본값이 아니며 운영 코드에서 사용하지 않는다. 테스트 개발 의존성은 `requirements-dev.txt`에 정리했다.

모의 브라우저 확인 서버:

```powershell
.\.venv\Scripts\python.exe tests\mock_ui_server.py
```

`http://127.0.0.1:8765/app`에서 모의 로그인 후 검증한다. 테스트 전용 값은 `tests/security_helpers.py`에 있으며 실제 계정으로 사용하지 않는다. 서버는 loopback에만 바인딩되고 메모리 DB·모의 GPT만 사용한다. 실제 Firebase 자격증명을 초기화하지 않는다.

## 제출 전 남은 검증

- 실제 계정·해시·비밀값 사용자 설정, Firebase IAM 권한 확인.
- Firestore `evaluation_data`, `evaluation_conversations`, `auth_sessions`, `auth_limits` 실제 쓰기는 별도 승인 후 검증. 기존 `data`/`conversations` 수정·이관 없음.
- Render Linux 빌드, Vercel proxy·쿠키·CORS·no-store·콜드스타트 검증.
- 실브라우저 모바일 배치·로그인 만료·로그아웃·그래프·연도 필터 확인.
- 별도 승인 후 최소 실제 GPT 호출로 모델·토큰 파라미터 호환성과 과제 증빙 확인.
- 배포 URL·스크린샷 작성. 현재 실제 배포 URL은 없다.

설계 문서보다 이번 최종 결정(로그인 필수·실제 GPT·평가용 컬렉션 분리)이 우선한다. 이전 무인증 시연 계획은 적용하지 않는다.
