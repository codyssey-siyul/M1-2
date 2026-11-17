"""ASGI gate: auth and CSRF run before any business endpoint/body validation."""
import json
from http.cookies import SimpleCookie

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool


class SecurityMiddleware:
    def __init__(self, app, factory):
        self.app, self.factory = app, factory

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path, method = scope["path"], scope["method"]
        protected = path == "/api" or path.startswith("/api/") or path in {"/docs", "/redoc", "/openapi.json"}
        headers = {key.decode().lower(): value.decode("latin1") for key, value in scope["headers"]}

        async def reply(status, detail, extra=None):
            body = json.dumps({"detail": detail}, ensure_ascii=False).encode()
            result_headers = [(b"content-type", b"application/json; charset=utf-8"), (b"cache-control", b"no-store")]
            result_headers.extend((key.lower().encode(), value.encode()) for key, value in (extra or {}).items())
            await send({"type": "http.response.start", "status": status, "headers": result_headers})
            await send({"type": "http.response.body", "body": body})

        if path.rstrip("/") == "/api/chat/test":
            return await reply(404, "사용하지 않는 API입니다.")
        if method == "OPTIONS" or not protected:
            return await self.app(scope, receive, send)
        try:
            service = self.factory()
            cookie = SimpleCookie()
            try:
                cookie.load(headers.get("cookie", ""))
            except Exception:
                raise HTTPException(401, "로그인이 필요합니다.") from None
            item = cookie.get(service.config.cookie)
            token = item.value if item else None
            is_login = path == "/api/auth/login" and method == "POST"
            state = scope.setdefault("state", {})
            state["security"] = service
            if not is_login:
                state["session"] = await run_in_threadpool(service.authenticate, token)
            if method not in {"GET", "HEAD"}:
                service.origin(headers.get("origin"))
                if not is_login:
                    service.check_csrf(token, headers.get("x-csrf-token"))
                if method in {"POST", "PUT", "PATCH"} and headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
                    raise HTTPException(415, "JSON 형식으로 요청해 주세요.")
            if not is_login and path not in {"/api/auth/me", "/api/auth/logout", "/docs", "/redoc", "/openapi.json"}:
                state["session"] = await run_in_threadpool(service.authenticate, token, True)
            state["session_token"] = token
        except HTTPException as exc:
            return await reply(exc.status_code, exc.detail, exc.headers)
        except Exception:
            return await reply(503, "인증 요청을 처리할 수 없습니다.")

        # Limit actual streamed bytes too, not just a caller-supplied Content-Length.
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            limit = 4096 if is_login else 300000
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > limit:
                    return await reply(413, "요청 본문이 너무 큽니다.")
                if not message.get("more_body", False):
                    break
            sent = False

            async def replay():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()
            input_receive = replay
        else:
            input_receive = receive

        started = False
        finished = False

        async def output(message):
            nonlocal started, finished
            if message["type"] == "http.response.start":
                started = True
                message["headers"] = [(key, value) for key, value in message.get("headers", []) if key.lower() != b"cache-control"]
                message["headers"].append((b"cache-control", b"no-store"))
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                finished = True
            await send(message)

        try:
            return await self.app(scope, input_receive, output)
        except Exception:
            # Do not propagate raw upstream exceptions to access/server logs.
            if not started:
                return await reply(500, "요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.")
            if not finished:
                await send({"type": "http.response.body", "body": b"", "more_body": False})
