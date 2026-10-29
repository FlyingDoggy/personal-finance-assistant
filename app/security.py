"""Loopback-only browser sessions. No remote-device or MCP authority is implied."""
import secrets
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse

COOKIE = "finance_session"


def install_security(app, token=None, ai_token=None):
    session_secret = token or secrets.token_urlsafe(48)

    def deny(code, message, status=403):
        return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)

    @app.middleware("http")
    async def local_security(request: Request, call_next):
        host = request.headers.get("host", "")
        try:
            hostname = urlsplit("http://" + host).hostname
        except ValueError:
            return deny("invalid_host", "无效的本机地址")
        if hostname not in {"localhost", "127.0.0.1", "::1"}:
            return deny("invalid_host", "仅允许本机访问")
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{host}"
        if origin and origin != expected:
            return deny("invalid_origin", "请从应用本身打开此操作")
        if request.headers.get("sec-fetch-site") == "cross-site":
            return deny("invalid_origin", "拒绝跨站访问")
        authorization = request.headers.get("authorization", "")
        if request.url.path.startswith("/api/ai/v1/"):
            supplied = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
            if not ai_token or not supplied or not secrets.compare_digest(supplied.encode("utf-8"), ai_token.encode("utf-8")):
                return deny("ai_token_required", "财务 MCP 需要独立的机器访问令牌。", 401)
            request.state.ai_actor = "ai"
        elif request.url.path.startswith("/api/"):
            # A declared machine caller cannot bootstrap or reuse browser authority.
            if authorization:
                return deny("machine_scope_denied", "机器凭证不能调用用户操作或建立用户会话。", 401)
            if request.url.path != "/api/v1/session":
                supplied = request.cookies.get(COOKIE, "")
                if not secrets.compare_digest(supplied.encode("utf-8"), session_secret.encode("utf-8")):
                    return deny("session_required", "请刷新应用以建立本机会话", 401)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/v1/session")
    def session():
        response = JSONResponse({"authenticated": True, "mode": "local"})
        response.set_cookie(COOKIE, session_secret, httponly=True, samesite="strict", max_age=43200)
        return response
