# 로봇자동화 SI 실적 AI 비서

FastAPI·Firestore·OpenAI와 HTML/CSS/JavaScript·Chart.js로 구성한 과제 평가용 서비스다. 공통 계정 로그인 후 수주 데이터·대시보드·실제 GPT 채팅·평가용 대화 기록을 사용한다.

로봇자동화 SI 기업의 수주 실적을 연도별·월별·프로젝트 유형별로 분석하고, 실제 데이터 요약에 근거해 자연어 질문에 답한다. 2022~2026년 합성 수주 데이터 120건을 기반으로 하며, 평가용으로 추가한 프로젝트도 분석에 함께 반영한다.

## 기술 스택

| 구분 | 기술 |
|---|---|
| 백엔드 | Python, FastAPI, Pydantic, Uvicorn |
| 프론트엔드 | HTML, CSS, JavaScript, Chart.js — 프레임워크 미사용 |
| 저장소·AI | Firebase Firestore, Firebase Admin SDK, OpenAI API |
| 배포 | Render 백엔드, Vercel 프론트엔드·동일 출처 API 프록시 |

## 운영 배포 및 확인 결과

Render와 Vercel 배포를 완료했다. 아래 동작 결과는 운영 배포 후 사용자의 실제 테스트를 기준으로 한다.

| 항목 | 주소 또는 확인 결과 |
|---|---|
| 프론트엔드 | https://m1-2-fawn.vercel.app |
| 백엔드 | https://m1-2-8d9s.onrender.com |
| Swagger UI | https://m1-2-8d9s.onrender.com/docs — 로그인 필요 |
| 로그인 | 실제 공통 계정 로그인 정상 동작 |
| 대시보드 | 요약 카드·그래프 4개·연도 필터 정상 동작 |
| 데이터 관리 | 평가용 프로젝트 추가·수정·삭제 및 화면 갱신 정상 동작 |
| 대화 기록 | 저장·목록 조회·이전 대화 불러오기 정상 동작 |
| GPT 채팅 | 실제 GPT 응답 정상 동작 확인. 일부 질문에서는 유효한 응답을 받지 못한 사례도 있음 |

AI 채팅은 모든 질문의 성공을 보장하지 않는다. 일부 요청에서 `/api/chat` 502가 발생했으며, API 요청 오류 또는 유효한 답변 검증 실패를 구분해 추가 확인할 필요가 있다. 실패한 요청도 제공자 측 처리 여부에 따라 비용이 발생할 수 있으므로 반복 호출을 피한다. 평가용 로그인 정보는 README에 기재하지 않고 평가자에게 별도로 전달한다.

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

프로젝트 루트에서 Python 3.14.6 기준으로 가상환경과 의존성을 준비한다. 기존 `.venv`가 있으면 생성 단계는 생략한다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

처음 설정할 때만 `.env.example`을 참고해 `.env`를 만들고 Firebase 인증과 OpenAI 설정을 입력한다. 이미 있는 설정 파일은 유지한다.

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
- 실제 서버에서 로그인하는 것부터 `auth_*` 컬렉션 쓰기가 발생한다. 데이터 CRUD는 평가용 컬렉션에 반영되며, 실제 GPT 채팅은 비용이 발생할 수 있다. 외부 서비스에 접근하지 않는 검증은 아래 모의 테스트를 사용한다.

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

## Render 배포 설정

`render.yaml`, `.python-version`, 고정 버전 `requirements.txt`를 사용하며, Render 운영 배포를 완료했다.

- Build: `pip install -r requirements.txt`
- Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Health: `/health` — 인증 설정·DB 클라이언트 구성 여부 확인. 실제 DB RPC 권한/연결 성공까지 보장하는 상태 검사는 아니다.
- Python: `.python-version`에 3.14.6 지정.

Render 서버에서 관리하는 환경변수(비밀값은 저장소에 포함하지 않음):

| 변수 | 설정 |
|---|---|
| `APP_ENV` | `production` |
| `EVAL_USERNAME`, `EVAL_PASSWORD_HASH` | 사용자 지정 계정·검증용 해시 |
| `AUTH_SECRET`, `AUTH_VERSION` | 충분한 난수 비밀값·세션 무효화 버전 |
| `AUTH_ALLOWED_ORIGINS` | 정확한 Vercel 운영 Origin. Render `/app`·`/docs`도 쓸 경우 Render Origin도 추가 |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | 서비스 계정 JSON을 비밀 환경변수로 설정하는 방식 |
| `FIREBASE_SERVICE_ACCOUNT_PATH`, `GOOGLE_APPLICATION_CREDENTIALS` | JSON 환경변수 대신 인증 파일 경로를 지정하는 대안. 한 가지 방식 선택 |
| `FIRESTORE_DATABASE_ID` | 기존 `(default)` |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | 실제 키·모델, 서버에서만 관리 |
| `OPENAI_BASE_URL` | 별도 호환 API 제공자를 사용할 때 해당 접속 주소 지정. 키 발급처와 일치해야 함 |
| `OPENAI_MAX_COMPLETION_TOKENS` | 기본 1,200, 허용 범위 64~3,000 |
| `OPENAI_TOKEN_LIMIT_PARAMETER` | 기본 `max_completion_tokens`. 제공자가 요구하는 경우 `max_tokens` 사용 |
| 출력·로그인·AI 제한 변수 | `.env.example` 참조. 잘못된 인증 제한 값은 503 |

해시와 비밀값은 로컬 파일에서 사용자가 플랫폼에 직접 등록하며, dotenv 파일의 따옴표는 값에 포함하지 않는다. 비밀번호를 바꾸면 해시와 `AUTH_VERSION`도 변경해 기존 세션을 무효화한다.

## Vercel 동일 출처 프록시

브라우저의 `/api/*`는 Vercel rewrite를 거쳐 Render로 전달된다. 현재 운영 배포에서 로그인 및 인증된 업무 API 통신을 확인했다. 프록시 설정 변경 시 쿠키·CSRF·본문·응답 Set-Cookie 보존 여부를 회귀 검증한다. Render 직접 요청도 동일 인증을 요구한다.

`vercel.json`은 정적 설정이라 빌드 중 환경변수 문자열 치환에 의존하지 않는다. 현재 API 프록시는 `https://m1-2-8d9s.onrender.com`으로 설정되어 있다. **Vercel의 `BACKEND_ORIGIN`도 같은 주소로 설정해야 한다.** 백엔드 주소를 변경할 때는 아래 명령으로 설정을 다시 준비한다. 이 명령은 로컬 파일 준비일 뿐 배포하지 않는다.

```powershell
$env:BACKEND_ORIGIN="https://m1-2-8d9s.onrender.com"
node tools/build_frontend.mjs --configure
```

`BACKEND_ORIGIN` 설정 명령은 위 코드 블록처럼 일반 문자열을 사용하며 Markdown 링크 문법을 넣지 않는다. Vercel은 Framework Other, Output Directory `dist`, Build Command `node tools/build_frontend.mjs`로 설정하고 동일한 `BACKEND_ORIGIN`을 제공한다. 주소와 설정이 다르면 빌드는 실패한다. 재배포 전에 `dist`의 예상치 못한 파일은 사용자가 확인한다.

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

## 제출 스크린샷

| 종류 | 캡처할 내용 |
|---|---|
| AI 채팅 | 수주 데이터 요약에 근거한 질문과 실제 AI 답변이 함께 보이는 화면 |
| 데이터 관리(CRUD) | 평가용 프로젝트 목록과 추가·수정·삭제 중 동작 결과를 확인할 수 있는 화면 |
| 대화 기록 | 이전 대화 목록에서 저장된 대화를 선택하고 메시지를 불러온 화면 |

스크린샷의 실제 파일 경로는 아직 확인되지 않아 이미지 링크를 넣지 않았다. 제출 전에 위 3종을 준비해 실제 파일 또는 링크를 첨부한다. 인증정보·쿠키·환경변수·개인정보가 포함된 개발자 도구 화면은 캡처하지 않는다. GPT 증빙은 가능하면 이미 성공한 대화를 활용해 불필요한 유료 호출을 피한다.

## 운영 주의사항 및 추가 확인

- Render 무료 인스턴스가 유휴 상태라면 첫 요청에 시간이 걸릴 수 있다. 로그인 처리 중에는 반복 제출하지 않는다.
- 일부 AI 질문의 응답 실패 원인은 추가 확인 대상이다. 모델·접속 주소·토큰 파라미터는 실제 제공자와 일치시킨다.
- 모바일 배치·로그인 만료·로그아웃·콜드스타트 등 운영 경계 상황은 별도로 확인한다. 정상 동작 확인이 모든 오류 상황의 검증 완료를 의미하지 않는다.
- 기존 `data` 120건과 `conversations`는 보존한다. 평가용 데이터와 대화만 별도 컬렉션에서 관리한다.
- `.env`, `.env.auth`, Firebase 인증 JSON 및 실제 계정·해시·세션 비밀값·API 키는 GitHub와 제출 자료에 포함하지 않는다.

설계 문서보다 이번 최종 결정(로그인 필수·실제 GPT·평가용 컬렉션 분리)이 우선한다. 이전 무인증 시연 계획은 적용하지 않는다.
