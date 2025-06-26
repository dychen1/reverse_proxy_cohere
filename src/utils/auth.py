import hmac
from functools import wraps

from aiohttp import web

from src.settings import settings


def auth_required(handler):
    """Decorator to protect an endpoint with a static bearer token."""

    @wraps(handler)
    async def wrapper(self, request: web.Request):
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return web.Response(
                text="Authorization header missing or invalid", status=401, headers={"WWW-Authenticate": "Bearer"}
            )

        try:
            token = auth_header.split(" ")[1]
        except IndexError:
            return web.Response(
                text="Invalid authorization header format", status=401, headers={"WWW-Authenticate": "Bearer"}
            )

        # Constant-time string comparison to prevent timing attacks
        if not hmac.compare_digest(token, settings.admin_secret_token):
            return web.Response(text="Invalid token", status=403, headers={"WWW-Authenticate": "Bearer"})

        return await handler(self, request)

    return wrapper
