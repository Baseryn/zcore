"""Storage Security Validators.

This module provides validation controls to protect server integrity during file uploads.
It includes file extension checks, file size limit enforcement to defend against Denial
of Service (DoS) attacks, and MIME-type verification using magic bytes and payload inspection
to detect masqueraded executable scripts and stored XSS vectors.
"""

import mimetypes
import re

import structlog
from fastapi import UploadFile

from zcore.exceptions.base import ValidationError

logger = structlog.get_logger()

SIGNATURES: dict[bytes, str] = {
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
    b"%PDF": "application/pdf",
    b"RIFF": "image/webp",
}

BLOCKED_HEADER_PREFIXES: tuple[bytes, ...] = (
    b"MZ",
    b"#!/",
    b"\x7fELF",
)

MALICIOUS_CONTENT_PATTERNS: tuple[re.Pattern[bytes], ...] = (
    re.compile(b"<\?php", re.IGNORECASE),
    re.compile(b"<script[\s>]", re.IGNORECASE),
    re.compile(b"javascript:", re.IGNORECASE),
    re.compile(b"vbscript:", re.IGNORECASE),
    re.compile(b"data:text/html", re.IGNORECASE),
    re.compile(b"<!doctype\s+html", re.IGNORECASE),
    re.compile(b"<html[\s>]", re.IGNORECASE),
    re.compile(b"<iframe[\s>]", re.IGNORECASE),
    re.compile(b"<object[\s>]", re.IGNORECASE),
    re.compile(b"<embed[\s>]", re.IGNORECASE),
    re.compile(b"onload\s*=", re.IGNORECASE),
    re.compile(b"onerror\s*=", re.IGNORECASE),
    re.compile(b"onclick\s*=", re.IGNORECASE),
    re.compile(b"xlink:href\s*=\s*[\"']javascript:", re.IGNORECASE),
)


class BaseStorageValidator:
    """Base interface protocol defining standard storage validators."""

    def __call__(self, file: UploadFile) -> None:
        """Execute validation criteria against the target file.

        Args:
            file: The uploaded file to validate.

        Raises:
            NotImplementedError: If not implemented by the subclass.
            ValidationError: If the file fails to pass validation checks.
        """
        raise NotImplementedError


class FileExtensionValidator(BaseStorageValidator):
    """Enforce extension checks on incoming file names.

    Attributes:
        allowed_extensions: Normalized list of allowed file extensions.
        message: Diagnostic warning message dispatched on failures.
    """

    def __init__(
        self,
        allowed_extensions: list[str] | set[str],
        message: str | None = None,
    ) -> None:
        """Initialize the FileExtensionValidator.

        Args:
            allowed_extensions: List or set of allowed extension keys.
            message: Custom validation warning. Defaults to None.
        """
        self.allowed_extensions = {
            ext.lower() if ext.startswith(".") else f".{ext.lower()}"
            for ext in allowed_extensions
        }
        self.message = (
            message
            or f"File extension not allowed. Allowed extensions are: {', '.join(sorted(self.allowed_extensions))}"
        )

    def __call__(self, file: UploadFile) -> None:
        """Check the uploaded file's extension against the allowed list.

        Args:
            file: The uploaded file payload to inspect.

        Raises:
            ValidationError: If the file's extension is not permitted.
        """
        filename = file.filename or ""
        ext = f".{filename.split('.')[-1].lower()}" if "." in filename else ""

        if ext not in self.allowed_extensions:
            raise ValidationError(message=self.message)


class MaxFileSizeValidator(BaseStorageValidator):
    """Enforce file size limits to prevent denial of service (DoS) and storage exhaustion.

    Attributes:
        max_size_bytes: The computed size boundary converted to raw bytes.
        max_size_mb: The maximum size boundary defined in megabytes.
        message: Diagnostic warning message dispatched on failures.
    """

    def __init__(self, max_size_mb: float, message: str | None = None) -> None:
        """Initialize the MaxFileSizeValidator.

        Args:
            max_size_mb: Size threshold limit specified in Megabytes (MB).
            message: Custom validation warning. Defaults to None.
        """
        self.max_size_bytes = int(max_size_mb * 1024 * 1024)
        self.max_size_mb = max_size_mb
        self.message = message or f"File size exceeds the limit of {max_size_mb} MB."

    def __call__(self, file: UploadFile) -> None:
        """Check the uploaded file's size against the maximum limit.

        Args:
            file: The uploaded file payload to inspect.

        Raises:
            ValidationError: If the file size exceeds limits or cannot be verified.
        """
        size = getattr(file, "size", None)

        if size is None:
            try:
                file.file.seek(0, 2)
                size = file.file.tell()
                file.file.seek(0)
            except Exception as e:
                logger.error(f"Failed to dynamically evaluate file size: {e}")
                raise ValidationError(message="Failed to process file size validation.")

        if size > self.max_size_bytes:
            raise ValidationError(message=self.message)


class SafeMimeTypeValidator(BaseStorageValidator):
    """MIME-type validator utilizing Magic Byte verification and active payload inspection.

    Reads the initial byte buffers of the file payload to verify that content matches declared formats
    and scans against active script injections, executable headers, and stored XSS vectors.

    Attributes:
        allowed_mimes: Set containing approved MIME keys.
        message: Diagnostic warning message dispatched on failures.
    """

    def __init__(self, allowed_mimes: list[str] | set[str], message: str | None = None) -> None:
        """Initialize the SafeMimeTypeValidator.

        Args:
            allowed_mimes: Set or list of approved MIME strings.
            message: Custom validation warning. Defaults to None.
        """
        self.allowed_mimes = set(allowed_mimes)
        self.message = (
            message
            or "Uploaded file content is corrupted or its MIME-type is not allowed."
        )

    def __call__(self, file: UploadFile) -> None:
        """Analyze file headers and byte payloads for security compliance.

        Args:
            file: The uploaded file payload to inspect.

        Raises:
            ValidationError: If the file content violates security policies or contains
                unauthorized file signatures.
        """
        try:
            sample_bytes = file.file.read(8192)
            file.file.seek(0)
        except Exception as e:
            logger.error(f"Failed to read file header bytes for verification: {e}")
            raise ValidationError(message="Failed to validate file signatures.")

        if any(sample_bytes.startswith(prefix) for prefix in BLOCKED_HEADER_PREFIXES):
            logger.critical(
                f"Blocked dangerous executable binary header in uploaded file: '{file.filename}'"
            )
            raise ValidationError(
                message="Security policy violation: Executable binaries are strictly forbidden."
            )

        if any(pattern.search(sample_bytes) for pattern in MALICIOUS_CONTENT_PATTERNS):
            logger.critical(
                f"Blocked active script or XSS injection pattern in uploaded file: '{file.filename}'"
            )
            raise ValidationError(
                message="Security policy violation: Active scripts and embedded injections are strictly blocked."
            )

        detected_mime = None
        for signature, mime in SIGNATURES.items():
            if sample_bytes.startswith(signature):
                detected_mime = mime
                break

        if not detected_mime and file.filename:
            detected_mime, _ = mimetypes.guess_type(file.filename)

        if not detected_mime or detected_mime not in self.allowed_mimes:
            logger.warning(
                f"File validation failed: Detected MIME '{detected_mime}' not in allowed list."
            )
            raise ValidationError(message=self.message)