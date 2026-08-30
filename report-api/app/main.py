import base64
import hashlib
import json
import os
import time

import boto3

from botocore.client import Config
from botocore.exceptions import ClientError
import os
from datetime import date, timedelta

import clickhouse_connect
import jwt

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from jwt import PyJWKClient

S3_ENDPOINT = os.getenv(
    "S3_ENDPOINT",
    "http://minio:9000",
)

S3_ACCESS_KEY = os.getenv(
    "S3_ACCESS_KEY",
    "minioadmin",
)

S3_SECRET_KEY = os.getenv(
    "S3_SECRET_KEY",
    "minioadmin123",
)

S3_BUCKET = os.getenv(
    "S3_BUCKET",
    "bionicpro-reports",
)

CDN_BASE_URL = os.getenv(
    "CDN_BASE_URL",
    "http://localhost:8082",
)

CDN_SECRET = os.environ["CDN_SECRET"]



CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "clickhouse")
CLICKHOUSE_USER = os.getenv(
    "CLICKHOUSE_USER",
    "clickhouse",
)
CLICKHOUSE_PASSWORD = os.getenv(
    "CLICKHOUSE_PASSWORD",
    "clickhouse",
)

KEYCLOAK_ISSUER = os.getenv(
    "KEYCLOAK_ISSUER",
    "http://localhost:8080/realms/reports-realm",
)

KEYCLOAK_JWKS_URL = os.getenv(
    "KEYCLOAK_JWKS_URL",
    (
        "http://keycloak:8080/realms/reports-realm/"
        "protocol/openid-connect/certs"
    ),
)

JWT_AUDIENCE = "reports-api"

app = FastAPI()

jwks_client = PyJWKClient(KEYCLOAK_JWKS_URL)

s3 = boto3.client(
    "s3",
    endpoint_url=S3_ENDPOINT,
    aws_access_key_id=S3_ACCESS_KEY,
    aws_secret_access_key=S3_SECRET_KEY,
    config=Config(
        signature_version="s3v4",
        s3={
            "addressing_style": "path",
        },
    ),
)

def build_report_key(
    user_id: str,
    processed_until,
    crm_version: int,
    from_date,
    to_date,
) -> str:
    user_hash = hashlib.sha256(
        user_id.encode()
    ).hexdigest()

    return (
        f"users/{user_hash}/"
        f"{processed_until}-{crm_version}/"
        f"{from_date}_{to_date}.json"
    )


def report_exists(object_key: str) -> bool:
    try:
        s3.head_object(
            Bucket=S3_BUCKET,
            Key=object_key,
        )
        return True

    except ClientError as error:
        code = error.response["Error"]["Code"]

        if code in ("404", "NoSuchKey", "NotFound"):
            return False

        raise


def save_report(
    object_key: str,
    report: dict,
) -> None:
    body = json.dumps(
        report,
        default=str,
        ensure_ascii=False,
        indent=2,
    ).encode()

    s3.put_object(
        Bucket=S3_BUCKET,
        Key=object_key,
        Body=body,
        ContentType="application/json",
        CacheControl="public, max-age=86400, immutable",
    )


def create_cdn_url(
    object_key: str,
) -> str:
    path = f"/reports/{object_key}"

    expires = int(time.time()) + 15 * 60

    signature_source = (
        f"{expires}{path} {CDN_SECRET}"
    )

    digest = hashlib.md5(
        signature_source.encode()
    ).digest()

    signature = (
        base64.urlsafe_b64encode(digest)
        .rstrip(b"=")
        .decode()
    )

    return (
        f"{CDN_BASE_URL}{path}"
        f"?md5={signature}"
        f"&expires={expires}"
    )

def get_clickhouse():
    return clickhouse_connect.get_client(
        host=CLICKHOUSE_HOST,
        port=8123,
        username=CLICKHOUSE_USER,
        password=CLICKHOUSE_PASSWORD,
        database="bionicpro",
    )


def get_current_user(
    authorization: str = Header(...),
) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Bearer token required",
        )

    token = authorization.removeprefix("Bearer ").strip()

    try:
        signing_key = jwks_client.get_signing_key_from_jwt(
            token
        )

        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=KEYCLOAK_ISSUER,
            audience=JWT_AUDIENCE,
        )


    except jwt.PyJWTError as error:
        print("JWT validation error:",type(error).__name__,str(error),)

        raise HTTPException(
            status_code=401,
            detail=f"{type(error).__name__}: {error}",
        ) from error

    username = payload.get("preferred_username")

    if not username:
        raise HTTPException(
            status_code=401,
            detail="User identifier is missing",
        )

    return username


@app.get("/reports")
def get_report(
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    user_id: str = Depends(get_current_user),
):
    clickhouse = get_clickhouse()

    state = clickhouse.query(
        """
        SELECT max(processed_through)
        FROM bionicpro.etl_state
        WHERE pipeline = 'daily_reports'
        """
    )
    crm_version_result = clickhouse.query(
        """
        SELECT max(version)
        FROM bionicpro.crm_clients_current FINAL
        WHERE user_id = {user_id:String}
        """,
        parameters={
            "user_id": user_id,
        },
    )

    crm_version = (
            crm_version_result.first_row[0]
            or 0
    )

    processed_until = state.first_row[0]

    if processed_until is None:
        raise HTTPException(
            status_code=503,
            detail="Reports have not been prepared yet",
        )

    if to_date is None:
        to_date = processed_until

    if from_date is None:
        from_date = to_date - timedelta(days=6)

    if from_date > to_date:
        raise HTTPException(
            status_code=422,
            detail="from_date must be <= to_date",
        )

    if to_date > processed_until:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Reports are available only through "
                f"{processed_until}"
            ),
        )

    object_key = build_report_key(
        user_id=user_id,
        processed_until=processed_until,
        crm_version=crm_version,
        from_date=from_date,
        to_date=to_date,
    )

    if report_exists(object_key):
        print("Hit cache for report:", object_key)
        return {
            "url": create_cdn_url(object_key),
            "source": "s3",
            "processed_until": processed_until,
        }

    result = clickhouse.query(
        """
        SELECT
            report_date,
            prosthesis_id,
            full_name,
            telemetry_events,
            avg_signal_strength,
            movements,
            avg_battery_level

        FROM bionicpro.report_mart_cdc FINAL

        WHERE user_id = {user_id:String}
          AND report_date >= {from_date:Date}
          AND report_date <= {to_date:Date}
          AND is_deleted = 0

        ORDER BY report_date, prosthesis_id
        """,
        parameters={
            "user_id": user_id,
            "from_date": from_date,
            "to_date": to_date,
        },
    )

    reports = [
        {
            "date": row[0],
            "prosthesis_id": row[1],
            "full_name": row[2],
            "telemetry_events": row[3],
            "avg_signal_strength": row[4],
            "movements": row[5],
            "avg_battery_level": row[6],
        }
        for row in result.result_rows
    ]

    report = {
        "user_id": user_id,
        "from": from_date,
        "to": to_date,
        "processed_until": processed_until,
        "reports": reports,
    }

    save_report(
        object_key,
        report,
    )
    print("Generated new report and saved to S3:", object_key)

    return {
        "url": create_cdn_url(object_key),
        "source": "clickhouse",
        "processed_until": processed_until,
    }