"""FastAPI backend for the E-Commerce Agent.

This file exists to fix four things the reference implementation of this
pattern gets wrong, each of which is a real defect rather than a style
preference:

1. **A new ``InMemorySessionService`` per request.** The reference constructs
   one inside the request handler, so the session it looks up never exists and
   the conversation has no memory: every turn starts from nothing. Here the
   service is a module-level singleton, and ``SESSION_DB_URL`` swaps it for
   ``DatabaseSessionService`` so sessions also survive a restart and are shared
   across replicas.

2. **A new ``Agent`` and ``Runner`` per request.** Rebuilding the agent graph
   and re-resolving the toolset on every message is pure latency. Built once,
   at startup.

3. **Identity carried in the conversation.** The reference prompt says to reuse
   the user's ID from previous turns, which makes identity attacker-supplied.
   Here the ID token is verified per request and the verified email is written
   into session state; the model never sees or sets it.

4. **No auth failure handling.** An invalid or expired token here produces a
   401, or a guest session when guest browsing is enabled — not a silent
   downgrade to "some user".

Run with::

    uvicorn backend.app:app --reload --port 8080
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ecommerce_agent.agent import build_agent  # noqa: E402
from ecommerce_agent.config import config  # noqa: E402
from ecommerce_agent.identity import (  # noqa: E402
    IdentityError,
    apply_identity_to_state,
    clear_identity,
    verify_google_id_token,
)

logging.basicConfig(level=config.log_level)
logger = logging.getLogger(__name__)

APP_NAME = "ecommerce_agent"

# Built once at import, not per request.
_session_service: Any = None
_runner: Runner | None = None


def _build_session_service() -> Any:
    """Build the session service, persistent when configured."""
    if config.session_db_url:
        try:
            from google.adk.sessions import DatabaseSessionService

            logger.info("Using DatabaseSessionService for persistent sessions.")
            return DatabaseSessionService(db_url=config.session_db_url)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Could not open the session database (%s); falling back to "
                "in-memory sessions. Conversations will be lost on restart and "
                "will not be shared between replicas.",
                exc,
            )
    else:
        logger.warning(
            "SESSION_DB_URL is not set, so sessions are in-memory: they are "
            "lost on restart and not shared between replicas. Set it for any "
            "deployment with more than one instance."
        )
    return InMemorySessionService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build the agent, runner and session service once, at startup."""
    global _session_service, _runner

    _session_service = _build_session_service()

    # The token getter closes over a per-request context variable, so the
    # Toolbox toolset can resolve the caller's token without the agent graph
    # being rebuilt for each request.
    agent = build_agent(auth_token_getter=_current_id_token)
    _runner = Runner(
        app_name=APP_NAME,
        agent=agent,
        session_service=_session_service,
    )
    logger.info("Agent and runner initialised.")
    yield
    logger.info("Shutting down.")


app = FastAPI(
    title="E-Commerce Agent",
    description="Sports retail shopping assistant built with the Google ADK.",
    version="1.0.0",
    lifespan=lifespan,
)

# Tighten this for a real deployment: a wildcard origin with credentials is
# refused by browsers anyway, and an explicit list is what you want.
_allowed_origins = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


# --------------------------------------------------------------------------
# Per-request ID token, for the Toolbox auth binding
# --------------------------------------------------------------------------
import contextvars  # noqa: E402

_id_token_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "id_token", default=None
)


async def _current_id_token() -> str:
    """Return the current request's raw ID token for Toolbox.

    Toolbox calls this to resolve the ``google_signin`` auth service and inject
    the verified ``email`` claim into the SQL parameters that declare it.
    """
    return _id_token_var.get() or ""


# --------------------------------------------------------------------------
# Request / response models
# --------------------------------------------------------------------------
class ChatRequest(BaseModel):
    """One turn of conversation."""

    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(
        default=None, description="Omit to start a new session."
    )


class ChatResponse(BaseModel):
    """A non-streamed reply."""

    session_id: str
    reply: str
    authenticated: bool


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _strip_bearer(header: str | None) -> str:
    """Extract the raw token from an Authorization header."""
    if not header:
        return ""
    value = header.strip()
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return value


async def _resolve_session(session_id: str | None, user_id: str) -> Any:
    """Fetch an existing session or create one.

    Looking the session up *before* creating it is what makes the conversation
    continuous; the reference creates unconditionally and so starts fresh every
    turn.
    """
    assert _session_service is not None

    if session_id:
        existing = await _session_service.get_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        )
        if existing is not None:
            return existing

    return await _session_service.create_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id or f"sess-{uuid.uuid4().hex[:16]}",
        state={},
    )


async def _authenticate(authorization: str | None) -> tuple[str, dict[str, Any] | None]:
    """Verify the caller and return ``(user_id, claims)``.

    Raises:
        HTTPException: 401 when a token is absent or invalid and guest
            browsing is disabled.
    """
    token = _strip_bearer(authorization)
    _id_token_var.set(token or None)

    if not token:
        if config.allow_guest_browsing:
            return "guest", None
        raise HTTPException(status_code=401, detail="Sign-in is required.")

    try:
        claims = verify_google_id_token(token)
    except IdentityError as exc:
        logger.info("Rejected a token: %s", exc)
        if config.allow_guest_browsing:
            # Downgrade to guest, but make it explicit rather than silently
            # treating the caller as authenticated-but-unknown.
            _id_token_var.set(None)
            return "guest", None
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    return claims["email"], claims


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    """Liveness probe, plus what this deployment can actually do."""
    return {
        "status": "ok",
        "toolbox_url": config.toolbox_url,
        "toolbox_toolset": config.toolbox_toolset,
        "identity_verification": config.identity_verification_enabled,
        "guest_browsing": config.allow_guest_browsing,
        "persistent_sessions": bool(config.session_db_url),
        "currency": config.currency,
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    authorization: str | None = Header(default=None),
) -> ChatResponse:
    """Send one message and get the full reply."""
    if _runner is None:  # pragma: no cover - lifespan guarantees this
        raise HTTPException(status_code=503, detail="The agent is not ready.")

    user_id, claims = await _authenticate(authorization)
    session = await _resolve_session(payload.session_id, user_id)

    # Identity is written into state here, by the server, from verified
    # claims. No tool argument and no model output can change it.
    if claims:
        apply_identity_to_state(session.state, claims)
    else:
        clear_identity(session.state)

    content = types.Content(role="user", parts=[types.Part(text=payload.message)])

    chunks: list[str] = []
    async for event in _runner.run_async(
        user_id=user_id, session_id=session.id, new_message=content
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    chunks.append(part.text)

    return ChatResponse(
        session_id=session.id,
        reply="".join(chunks).strip(),
        authenticated=claims is not None,
    )


@app.post("/chat/stream")
async def chat_stream(
    payload: ChatRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> StreamingResponse:
    """Send one message and stream the reply as server-sent events."""
    if _runner is None:  # pragma: no cover
        raise HTTPException(status_code=503, detail="The agent is not ready.")

    user_id, claims = await _authenticate(authorization)
    session = await _resolve_session(payload.session_id, user_id)

    if claims:
        apply_identity_to_state(session.state, claims)
    else:
        clear_identity(session.state)

    content = types.Content(role="user", parts=[types.Part(text=payload.message)])

    async def event_stream():
        yield f"event: session\ndata: {session.id}\n\n"
        try:
            async for event in _runner.run_async(
                user_id=user_id, session_id=session.id, new_message=content
            ):
                if await request.is_disconnected():
                    logger.info("Client disconnected; stopping the stream.")
                    break
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.text:
                            # SSE needs one `data:` line per line of payload.
                            for line in part.text.split("\n"):
                                yield f"data: {line}\n"
                            yield "\n"
        except Exception as exc:  # noqa: BLE001 - must not break the stream contract
            logger.exception("The agent failed mid-stream")
            yield f"event: error\ndata: {exc}\n\n"
        else:
            yield "event: done\ndata: \n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
