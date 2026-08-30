import base64
import hashlib
import os
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse


REPORTS_API_URL = os.getenv(
    "REPORTS_API_URL",
    "http://api:8001",
)
KEYCLOAK_PUBLIC_URL = os.getenv(
    "KEYCLOAK_PUBLIC_URL",
    "http://localhost:8080",
)
KEYCLOAK_INTERNAL_URL = os.getenv(
    "KEYCLOAK_INTERNAL_URL",
    "http://keycloak:8080",
)
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "reports-realm")
KEYCLOAK_CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID", "bionicpro-auth")
KEYCLOAK_CLIENT_SECRET = os.environ["KEYCLOAK_CLIENT_SECRET"]

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
REDIRECT_URI = os.getenv(
    "REDIRECT_URI",
    "http://localhost:8000/auth/callback",
)

COOKIE_NAME = "bionicpro_session"
SESSION_TTL = 30 * 60

fernet = Fernet(os.environ["AUTH_FERNET_KEY"].encode())

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@dataclass
class Session:
    access_token: str
    encrypted_refresh_token: bytes
    access_expires_at: float
    session_expires_at: float


sessions: dict[str, Session] = {}
oauth_flows: dict[str, tuple[str, float]] = {}


def keycloak_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(trust_env=False)


def auth_endpoint() -> str:
    return (
        f"{KEYCLOAK_PUBLIC_URL}/realms/{KEYCLOAK_REALM}"
        "/protocol/openid-connect/auth"
    )


def token_endpoint() -> str:
    return (
        f"{KEYCLOAK_INTERNAL_URL}/realms/{KEYCLOAK_REALM}"
        "/protocol/openid-connect/token"
    )


def userinfo_endpoint() -> str:
    return (
        f"{KEYCLOAK_INTERNAL_URL}/realms/{KEYCLOAK_REALM}"
        "/protocol/openid-connect/userinfo"
    )


def create_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    print("verifier for pkce", verifier, verifier.encode())

    digest = hashlib.sha256(verifier.encode()).digest()

    challenge = (
        base64.urlsafe_b64encode(digest)
        .rstrip(b"=")
        .decode()
    )
    print("challenge for pkce", challenge)

    return verifier, challenge


def set_session_cookie(response, session_id: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=session_id,
        max_age=SESSION_TTL,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def get_session(request: Request) -> tuple[str, Session]:
    session_id = request.cookies.get(COOKIE_NAME)

    if not session_id:
        print("No session id in request")
        raise HTTPException(status_code=401)

    session = sessions.get(session_id)

    if not session:
        print("Session not found")
        raise HTTPException(status_code=401)

    if session.session_expires_at < time.time():
        sessions.pop(session_id, None)
        print("Session expired")
        raise HTTPException(status_code=401)

    return session_id, session


async def refresh_access_token(session: Session) -> None:
    if session.access_expires_at > time.time() + 5:
        print("Session not need to prolongate")
        return

    refresh_token = fernet.decrypt(
        session.encrypted_refresh_token
    ).decode()

    async with keycloak_client() as client:
        response = await client.post(
            token_endpoint(),
            data={
                "grant_type": "refresh_token",
                "client_id": KEYCLOAK_CLIENT_ID,
                "client_secret": KEYCLOAK_CLIENT_SECRET,
                "refresh_token": refresh_token,
            },
        )

    if response.is_error:
        print("Refresh token failed")
        raise HTTPException(status_code=401)

    tokens = response.json()

    session.access_token = tokens["access_token"]
    session.access_expires_at = (
        time.time() + tokens["expires_in"]
    )

    new_refresh_token = tokens.get(
        "refresh_token",
        refresh_token,
    )

    session.encrypted_refresh_token = fernet.encrypt(
        new_refresh_token.encode()
    )


def rotate_session(
    response,
    old_session_id: str,
    session: Session,
) -> None:
    new_session_id = secrets.token_urlsafe(32)

    sessions[new_session_id] = session
    sessions.pop(old_session_id, None)

    set_session_cookie(response, new_session_id)
    print("Session rotated", old_session_id, new_session_id)


async def get_profile(session: Session) -> dict:
    await refresh_access_token(session)

    async with keycloak_client() as client:
        print("access token for userinfo", session.access_token)
        print("url", userinfo_endpoint())
        response = await client.get(
            userinfo_endpoint(),
            headers={
                "Authorization": (
                    f"Bearer {session.access_token}"
                )
            },
        )
        print(
            "userinfo status:", response.status_code,
            "body:", response.text,
            "headers:", dict(response.headers),
        )

    if response.is_error:
        print("Failed to get user profile")
        raise HTTPException(status_code=401)

    return response.json()


@app.get("/auth/login")
async def login():
    verifier, challenge = create_pkce()

    state = secrets.token_urlsafe(32)

    oauth_flows[state] = (
        verifier,
        time.time() + 300,
    )

    params = {
        "client_id": KEYCLOAK_CLIENT_ID,
        "response_type": "code",
        "scope": "openid profile email",
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }

    return RedirectResponse(
        f"{auth_endpoint()}?{urlencode(params)}"
    )


@app.get("/auth/callback")
async def callback(
    code: str,
    state: str,
):
    flow = oauth_flows.pop(state, None)

    if not flow:
        raise HTTPException(
            status_code=400,
            detail="Invalid OAuth state",
        )

    verifier, expires_at = flow

    if expires_at < time.time():
        raise HTTPException(
            status_code=400,
            detail="OAuth flow expired",
        )

    async with keycloak_client() as client:
        response = await client.post(
            token_endpoint(),
            data={
                "grant_type": "authorization_code",
                "client_id": KEYCLOAK_CLIENT_ID,
                "client_secret": KEYCLOAK_CLIENT_SECRET,
                "redirect_uri": REDIRECT_URI,
                "code": code,
                "code_verifier": verifier,
            },
        )

    response.raise_for_status()

    tokens = response.json()

    session_id = secrets.token_urlsafe(32)
    print("new session created", session_id, "access token", tokens["access_token"], "expires in", tokens["expires_in"], "refresh token", tokens["refresh_token"])
    sessions[session_id] = Session(
        access_token=tokens["access_token"],
        encrypted_refresh_token=fernet.encrypt(
            tokens["refresh_token"].encode()
        ),
        access_expires_at=(
            time.time() + tokens["expires_in"]
        ),
        session_expires_at=time.time() + SESSION_TTL,
    )

    result = RedirectResponse(FRONTEND_URL)

    set_session_cookie(result, session_id)

    return result


@app.get("/auth/me")
async def me(request: Request):
    session_id, session = get_session(request)

    profile = await get_profile(session)

    response = JSONResponse(profile)

    rotate_session(
        response,
        session_id,
        session,
    )

    return response


@app.get("/api/protected")
async def protected(request: Request):
    session_id, session = get_session(request)

    profile = await get_profile(session)

    response = JSONResponse(
        {
            "authenticated": True,
            "user": profile,
        }
    )

    rotate_session(
        response,
        session_id,
        session,
    )

    return response


@app.post("/auth/logout")
async def logout(request: Request):
    session_id = request.cookies.get(COOKIE_NAME)

    if session_id:
        sessions.pop(session_id, None)

    response = JSONResponse({"ok": True})

    response.delete_cookie(
        COOKIE_NAME,
        path="/",
    )

    return response

@app.get("/reports")
async def reports(request: Request):
    session_id, session = get_session(request)

    await refresh_access_token(session)

    async with httpx.AsyncClient() as client:
        print("Forwarding request to reports API", request.url, "with params", dict(request.query_params))
        print("Url", f"{REPORTS_API_URL}/reports")
        print("access token for reports API", session.access_token)
        upstream = await client.get(
            f"{REPORTS_API_URL}/reports",
            params=dict(request.query_params),
            headers={
                "Authorization": (
                    f"Bearer {session.access_token}"
                )
            },
        )

    try:
        content = upstream.json()
    except ValueError:
        content = {
            "detail": upstream.text,
        }

    response = JSONResponse(
        status_code=upstream.status_code,
        content=content,
    )

    rotate_session(
        response,
        session_id,
        session,
    )

    return response