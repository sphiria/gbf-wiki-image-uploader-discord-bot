"""Conditional, create-only storage for animation assets on Cloudflare R2."""

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


def r2_user_agent():
    """Preserve the storage identity without coupling it to asset downloads."""
    return os.environ.get(
        "USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
    )


def _verification_failure(route, status, response):
    """Report only status and numeric codes; API messages may contain secrets."""
    detail = f"{route}: HTTP {status}"
    if not isinstance(response, dict):
        return detail
    errors = response.get("errors")
    if isinstance(errors, list):
        codes = [
            str(error["code"])
            for error in errors
            if isinstance(error, dict) and type(error.get("code")) is int
        ]
        if codes:
            detail += ", Cloudflare code " + ", ".join(codes)
    result = response.get("result")
    if isinstance(result, dict) and result.get("status") in (
        "active",
        "disabled",
        "expired",
    ):
        detail += ", token status " + result["status"]
    return detail


def credentials(path):
    from dotenv import dotenv_values

    values = dict(dotenv_values(path)) if path.exists() else {}
    for name in (
        "R2_ENDPOINT_URL",
        "R2_BUCKET",
        "R2_API_TOKEN",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
    ):
        if os.environ.get(name):
            values[name] = os.environ[name]
    required = ["R2_ENDPOINT_URL", "R2_BUCKET"]
    if any(not values.get(name) for name in required):
        raise ValueError("Credential file is missing required settings")
    if not re.fullmatch(
        r"https://[a-f0-9]{32}(?:\.(?:eu|fedramp))?\.r2\.cloudflarestorage\.com/?",
        values["R2_ENDPOINT_URL"],
    ):
        raise ValueError("Expected an HTTPS Cloudflare R2 account endpoint")
    if values.get("R2_API_TOKEN"):
        account = values["R2_ENDPOINT_URL"].split("//")[1].split(".")[0]
        token = values["R2_API_TOKEN"]
        failures = []
        for kind, route in (
            ("user", "user/tokens/verify"),
            ("account", "accounts/" + account + "/tokens/verify"),
        ):
            request = urllib.request.Request(
                "https://api.cloudflare.com/client/v4/" + route,
                headers={
                    "Authorization": "Bearer " + token,
                    "User-Agent": r2_user_agent(),
                },
            )
            try:
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                    request, timeout=30
                ) as response:
                    status = response.status
                    result = json.load(response)
                if (
                    result.get("success")
                    and result.get("result", {}).get("status") == "active"
                ):
                    values["AWS_ACCESS_KEY_ID"] = result["result"]["id"]
                    values["AWS_SECRET_ACCESS_KEY"] = hashlib.sha256(
                        token.encode()
                    ).hexdigest()
                    print("Using R2_API_TOKEN (verified).", flush=True)
                    break
                failures.append(_verification_failure(kind, status, result))
            except urllib.error.HTTPError as error:
                if error.code not in (400, 401, 403):
                    raise
                with error:
                    try:
                        result = json.load(error)
                    except (ValueError, UnicodeError):
                        result = None
                failures.append(_verification_failure(kind, error.code, result))
        else:
            source = (
                "environment"
                if os.environ.get("R2_API_TOKEN")
                else "R2 credential file"
            )
            raise ValueError(
                f"R2_API_TOKEN verification failed ({'; '.join(failures)}; "
                f"token source: {source}). R2 storage was not contacted."
            )
    elif not all(
        values.get(name) for name in ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]
    ):
        raise ValueError("Supply R2_API_TOKEN or an S3 key pair")
    return values


class R2Store:
    def __init__(self):
        values = credentials(Path(os.environ.get("R2_ENV_FILE", ".env.r2")))
        self.bucket = values["R2_BUCKET"]
        self.client = boto3.client(
            "s3",
            endpoint_url=values["R2_ENDPOINT_URL"],
            region_name="auto",
            aws_access_key_id=values["AWS_ACCESS_KEY_ID"],
            aws_secret_access_key=values["AWS_SECRET_ACCESS_KEY"],
            config=Config(
                signature_version="s3v4",
                proxies={},
                user_agent=r2_user_agent(),
                retries={"max_attempts": 4},
                connect_timeout=15,
                read_timeout=60,
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )

    def put(self, key, content, content_type):
        if not re.fullmatch(r"anim/\d{10}/(?:[A-Za-z0-9_]+\.js|manifest\.json)", key):
            raise ValueError("Invalid animation object key")
        try:
            result = self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            if error.response["ResponseMetadata"]["HTTPStatusCode"] != 404:
                raise
        else:
            with result["Body"] as body:
                if body.read() != content:
                    raise ValueError("Existing object differs: " + key)
            return "existing"
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=content,
                IfNoneMatch="*",
                ContentType=content_type,
                CacheControl="public, max-age=86400",
                Metadata={"sha256": hashlib.sha256(content).hexdigest()},
            )
        except ClientError as error:
            if error.response["ResponseMetadata"]["HTTPStatusCode"] != 412:
                raise
            return self.put(key, content, content_type)
        return "uploaded"
