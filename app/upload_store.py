import base64
import uuid
from typing import Dict, Optional, Tuple

_uploads: Dict[str, Tuple[str, str, bytes]] = {}


def save_upload(filename: str, content_type: str, data: bytes) -> str:
    upload_id = str(uuid.uuid4())
    _uploads[upload_id] = (filename, content_type, data)
    return upload_id


def describe_upload(upload_id: str) -> Optional[dict]:
    item = _uploads.get(upload_id)
    if not item:
        return None
    filename, content_type, data = item
    return {
        "upload_id": upload_id,
        "filename": filename,
        "content_type": content_type,
        "size_bytes": len(data),
    }


def extract_text_from_bytes(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return base64.b64encode(data).decode("ascii")
