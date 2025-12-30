"""Utilities for persisting uploads and extracting text content."""

import os
import uuid
from typing import Dict, Optional
from urllib.parse import quote

from fastapi import UploadFile

UPLOADS_ROOT = os.getenv("UPLOADS_ROOT", "./uploads")


def _ensure_root() -> str:
    """Create the upload root directory if needed and return its absolute path."""
    root = os.path.abspath(UPLOADS_ROOT)
    os.makedirs(root, exist_ok=True)
    return root


def save_upload(file: UploadFile, data: bytes) -> Dict[str, str]:
    """Persist an uploaded file to a unique directory and return its metadata."""
    root = _ensure_root()
    file_id = uuid.uuid4().hex
    safe_name = os.path.basename(file.filename or "upload")
    dest_dir = os.path.join(root, file_id)
    os.makedirs(dest_dir, exist_ok=True)

    dest_path = os.path.join(dest_dir, safe_name)
    with open(dest_path, "wb") as f:
        f.write(data)

    return {
        "file_id": file_id,
        "filename": safe_name,
        "path": dest_path,
        "url": f"/uploads/{file_id}/{quote(safe_name)}",
        "content_type": file.content_type or "application/octet-stream",
    }


def extract_text_from_bytes(data: bytes) -> str:
    """Best-effort text extraction from uploaded content."""
    try:
        return data.decode("utf-8")
    except Exception:
        return data.decode("utf-8", errors="ignore")


def describe_upload(record: Dict[str, str], size_bytes: Optional[int]) -> Dict[str, str]:
    """Return a user-facing description of an upload including size when available."""
    out = dict(record)
    if size_bytes is not None:
        out["size_bytes"] = size_bytes
    return out
