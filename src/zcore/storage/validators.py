"""Storage Security Validators.

This module provides validation controls to protect server integrity during file uploads.
It includes file extension checks, file size limit enforcement to defend against Denial
of Service (DoS) attacks, and MIME-type verification using magic bytes to detect
masqueraded executable scripts.
"""

import mimetypes

import structlog
from fastapi import UploadFile

from zcore.exceptions.base import ValidationError

logger = structlog.get_logger()

# Hardened digital signatures of popular safe formats (Magic Bytes)
SIGNATURES = {
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
    b"%PDF": "application/pdf",
    b"RIFF": "image/webp",
}


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
        allowed_extensions: Normalized list of allowed file extensions (e.g., '.png').
        message: Diagnostic warning message dispatched on failures.
    """

    def __init__(
        self,
        allowed_extensions: list[str] | set[str],
        message: str | None = None,
    ):
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

    def __init__(self, max_size_mb: float, message: str | None = None):
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
        # Utilize Starlette's native file.size if populated, fallback to seek evaluation
        size = getattr(file, "size", None)

        if size is None:
            try:
                # Fallback seeking for chunked multipart parsers
                file.file.seek(0, 2)
                size = file.file.tell()
                file.file.seek(0)
            except Exception as e:
                logger.error(f"Failed to dynamically evaluate file size: {e}")
                raise ValidationError(message="Failed to process file size validation.")

        if size > self.max_size_bytes:
            raise ValidationError(message=self.message)


class SafeMimeTypeValidator(BaseStorageValidator):
    """MIME-type validator utilizing Magic Byte verification.

    Reads the header bytes of the file payload to verify that the file's content
    matches its declared format. Blocks high-risk patterns such as PHP tag injections,
    HTML script tags, Unix shebang scripts, and Windows executables.

    Attributes:
        allowed_mimes: Set containing approved MIME keys.
        message: Diagnostic warning message dispatched on failures.
    """

    def __init__(self, allowed_mimes: list[str] | set[str], message: str | None = None):
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
        """Analyze file headers to detect masquerading extensions or script injections.

        Args:
            file: The uploaded file payload to inspect.

        Raises:
            ValidationError: If the file content violates security policies or contains
                unauthorized file signatures.
        """
        try:
            header_bytes = file.file.read(2048)
            file.file.seek(0)
        except Exception as e:
            logger.error(f"Failed to read magic bytes for verification: {e}")
            raise ValidationError(message="Failed to validate file signatures.")

        # Blocks PHP block tags, HTML script tags, Unix shebang scripts and Windows Executables
        if (
            b"<?php" in header_bytes
            or b"<script" in header_bytes
            or header_bytes.startswith(b"MZ")
            or header_bytes.startswith(b"#!/")
        ):
            logger.critical(
                f"BLOCKED EXECUTE INJECTION ATTEMPT. Malicious file signature in filename: '{file.filename}'"
            )
            raise ValidationError(
                message="Security policy violation: Executable scripts are strictly blocked."
            )

        detected_mime = None
        for signature, mime in SIGNATURES.items():
            if header_bytes.startswith(signature):
                detected_mime = mime
                break

        # Fallback to standard Python guessing if signature is not mapped (e.g. for safe text files)
        if not detected_mime and file.filename:
            detected_mime, _ = mimetypes.guess_type(file.filename)

        if not detected_mime or detected_mime not in self.allowed_mimes:
            logger.warning(
                f"File validation failed. Detected MIME '{detected_mime}' not in allowed list."
            )
            raise ValidationError(message=self.message)
