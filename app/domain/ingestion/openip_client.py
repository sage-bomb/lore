import os
from typing import Any, Dict

import requests

OPENIP_BASE_URL = os.getenv("OPENIP_BASE_URL", "https://api.openip.ai")
OPENIP_API_KEY = os.getenv("OPENIP_API_KEY")
OPENIP_INGEST_PATH = os.getenv("OPENIP_INGEST_PATH", "/v1/extract-lore")


def _build_headers() -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if OPENIP_API_KEY:
        headers["Authorization"] = f"Bearer {OPENIP_API_KEY}"
    return headers


def _build_url() -> str:
    base = OPENIP_BASE_URL.rstrip("/")
    path = OPENIP_INGEST_PATH
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"


def extract_lore(text: str) -> Dict[str, Any]:
    if not text or not text.strip():
        raise ValueError("text must be a non-empty string")

    response = requests.post(
        _build_url(),
        json={"text": text},
        headers=_build_headers(),
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("OpenIP response must be a JSON object")
    return data

