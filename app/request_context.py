"""
Request correlation IDs (issue #77).

Every API request gets an ID: the client's ``X-Request-ID`` header when it is
a short token, otherwise a new one. It is returned in the response header,
added to every log record (``request_id``), stored on job logs and passed
to Celery tasks in the message headers, so a request can be followed from
the API into the worker that ran its job.
"""
import contextvars
import logging
import re
import uuid
from typing import Optional

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

request_id_var: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar("request_id", default=None)


def current_request_id() -> Optional[str]:
    return request_id_var.get()


def accept_request_id(incoming: Optional[str]) -> str:
    """The client's request ID if it is well formed, otherwise a new one."""
    if incoming and _VALID_REQUEST_ID.match(incoming):
        return incoming
    return uuid.uuid4().hex


class RequestIdFilter(logging.Filter):
    """Adds ``request_id`` (or "-") to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        return True


class RequestIdMiddleware:
    """
    Pure ASGI middleware (it does not buffer responses, so the SSE stream is
    unaffected) that binds the request ID for the duration of the request.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope.get("headers") or []).get(REQUEST_ID_HEADER.lower().encode(), b"")
        request_id = accept_request_id(incoming.decode("latin-1"))
        token = request_id_var.set(request_id)

        async def send_with_request_id(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append((REQUEST_ID_HEADER.lower().encode(), request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            request_id_var.reset(token)
