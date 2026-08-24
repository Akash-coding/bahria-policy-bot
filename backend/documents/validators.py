from __future__ import annotations

import os

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile

ALLOWED_CONTENT_TYPES = {
    ".pdf": {"application/pdf", "application/x-pdf", "binary/octet-stream"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
        "application/octet-stream",
        "binary/octet-stream",
    },
    ".txt": {"text/plain", "application/octet-stream", "binary/octet-stream"},
}

MAGIC_CHECKS = {
    ".pdf": (b"%PDF",),
    ".docx": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
}


def validate_uploaded_policy_file(uploaded: UploadedFile) -> str:
    if not uploaded or not uploaded.name:
        raise ValidationError("A file is required.")

    _, ext = os.path.splitext(uploaded.name.lower())
    if ext not in settings.ALLOWED_UPLOAD_EXTENSIONS:
        raise ValidationError(
            f"Unsupported file type '{ext}'. Allowed types: PDF, DOCX, TXT."
        )

    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    if uploaded.size is not None and uploaded.size > max_bytes:
        raise ValidationError(
            f"File is too large. Maximum size is {settings.MAX_UPLOAD_MB} MB."
        )
    if uploaded.size == 0:
        raise ValidationError("The uploaded file is empty.")

    content_type = (uploaded.content_type or "").lower()
    allowed_types = ALLOWED_CONTENT_TYPES.get(ext, set())
    if content_type and content_type not in allowed_types and not content_type.startswith("text/"):
        raise ValidationError(
            f"Unexpected content type '{uploaded.content_type}' for {ext} files."
        )

    header = uploaded.read(16)
    uploaded.seek(0)
    magic_options = MAGIC_CHECKS.get(ext)
    if magic_options and not any(header.startswith(magic) for magic in magic_options):
        raise ValidationError(f"The file does not look like a valid {ext} document.")

    if ext == ".txt":
        try:
            sample = uploaded.read(4096)
            uploaded.seek(0)
            sample.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError("TXT files must be valid UTF-8 text.") from exc

    return ext.lstrip(".")
