"""Local Filesystem Storage Provider.

This module implements storage operations targeting the host machine's filesystem.
It protects against path traversal attacks by enforcing strict path bounds checks, generates
high-entropy collision-free identifiers, resolves web-accessible URL paths dynamically, and evaluates
user-supplied files through configurable security validators.
"""

import uuid
from collections.abc import AsyncGenerator
from pathlib import Path as StdPath

import aiofiles
import structlog
from anyio import Path
from fastapi import UploadFile

from zcore.exceptions.base import AppException
from zcore.storage.base import StorageProvider
from zcore.storage.validators import BaseStorageValidator

logger = structlog.get_logger()


class LocalStorageProvider(StorageProvider):
    """Storage provider targeting the host filesystem with secure web URL resolution.

    Attributes:
        base_path: Absolute resolved root path of the host upload directory.
        url_prefix: Normalized HTTP URL prefix mapped to exposed static assets.
        validators: List of security/validation rules executed against uploaded files.
    """

    def __init__(
        self,
        base_path: str = "./storage",
        url_prefix: str = "/storage",
        validators: list[BaseStorageValidator] | None = None,
    ) -> None:
        """Initialize the LocalStorageProvider.

        Args:
            base_path: The root directory reserved for file uploads. Defaults to "./storage".
            url_prefix: The HTTP prefix mapped to static uploads. Defaults to "/storage".
            validators: Configured file validators. Defaults to None.
        """
        self.raw_base_path = base_path
        self.base_path = StdPath(base_path).resolve()
        self.url_prefix = "/" + url_prefix.strip("/\\") if url_prefix.strip("/\\") else ""
        self.validators = validators or []
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, file_path_or_url: str) -> StdPath | None:
        """Resolve a physical filesystem path from a web URL or file path within sandbox boundaries.

        Args:
            file_path_or_url: Web URL or relative/absolute path to resolve.

        Returns:
            Resolved absolute StdPath if within base_path boundaries, or None if outside.
        """
        if not file_path_or_url:
            return None

        cleaned_str = file_path_or_url.replace("\\", "/").strip()
        candidate = StdPath(cleaned_str)

        try:
            if candidate.is_absolute():
                resolved = candidate.resolve()
                if resolved.is_relative_to(self.base_path):
                    return resolved

            direct_resolved = (StdPath.cwd() / candidate).resolve()
            if direct_resolved.is_relative_to(self.base_path):
                return direct_resolved

            if self.url_prefix and cleaned_str.startswith(self.url_prefix):
                cleaned_str = cleaned_str[len(self.url_prefix) :].lstrip("/")
            elif cleaned_str.startswith("/"):
                cleaned_str = cleaned_str.lstrip("/")

            target_file = (self.base_path / cleaned_str).resolve()
            if target_file.is_relative_to(self.base_path):
                return target_file

            return None
        except Exception:
            return None

    async def _resolve_destination(self, filename: str, folder: str = "") -> tuple[StdPath, str]:
        """Generate verified physical filesystem target paths and corresponding web URL representations.

        Args:
            filename: Original name of the target file.
            folder: Subdirectory categorization partition. Defaults to "".

        Returns:
            A tuple containing the absolute physical destination path and the relative web URL.

        Raises:
            AppException: If a directory traversal attempt is detected.
        """
        normalized_folder = folder.strip("/\\").replace("\\", "/")
        target_dir = (self.base_path / normalized_folder).resolve()

        if not target_dir.is_relative_to(self.base_path):
            raise AppException("Path traversal attempt detected")

        await Path(str(target_dir)).mkdir(parents=True, exist_ok=True)

        ext = StdPath(filename).suffix.lower()
        secure_filename = f"{uuid.uuid4().hex}{ext}"

        physical_path = target_dir / secure_filename
        relative_web_path = f"{normalized_folder}/{secure_filename}" if normalized_folder else secure_filename
        web_url = f"{self.url_prefix}/{relative_web_path}" if self.url_prefix else f"/{relative_web_path}"

        return physical_path, web_url

    def _validate_file(self, file: UploadFile) -> None:
        """Internal helper running the active validator list against a file.

        Args:
            file: The UploadFile instance to analyze.
        """
        for validator in self.validators:
            validator(file)

    async def upload(self, file: UploadFile, folder: str = "") -> str:
        """Upload a file to the local storage target and return its normalized web URL.

        Args:
            file: The UploadFile payload to persist.
            folder: Subdirectory categorization partition. Defaults to "".

        Returns:
            The normalized web URL path of the saved file.

        Raises:
            AppException: If path traversal is detected or a filesystem write failure occurs.
        """
        self._validate_file(file)

        try:
            physical_path, web_url = await self._resolve_destination(file.filename or "file", folder)
            async with aiofiles.open(physical_path, "wb") as buffer:
                while chunk := await file.read(1024 * 1024):
                    await buffer.write(chunk)
            return web_url
        except AppException:
            raise
        except Exception as e:
            logger.error(f"Failed to upload file to local storage due to system error: {e}")
            raise AppException("Error saving file")

    async def upload_stream(
        self, file_stream: AsyncGenerator[bytes, None], filename: str, folder: str = ""
    ) -> str:
        """Directly stream binary data chunks to a local file destination and return its web URL.

        Args:
            file_stream: Asynchronous binary data chunk generator.
            filename: Target file name.
            folder: Subdirectory categorization partition. Defaults to "".

        Returns:
            The normalized web URL path of the saved file.

        Raises:
            AppException: If path traversal is detected or a filesystem write failure occurs.
        """
        try:
            physical_path, web_url = await self._resolve_destination(filename, folder)
            async with aiofiles.open(physical_path, "wb") as buffer:
                async for chunk in file_stream:
                    await buffer.write(chunk)
            return web_url
        except AppException:
            raise
        except Exception as e:
            logger.error(f"Failed to stream upload file to local storage due to system error: {e}")
            raise AppException("Error saving file")

    async def delete(self, file_path_or_url: str) -> bool:
        """Securely remove a file from local storage using its physical path or web URL.

        Args:
            file_path_or_url: Stored web URL or relative path of the file to remove.

        Returns:
            True if unlinking succeeds, False if traversal is detected or target is absent.
        """
        if not file_path_or_url:
            return False

        target_file = self._resolve_path(file_path_or_url)
        if target_file is None:
            logger.warning(
                f"Prevented arbitrary file deletion attempt outside base path: {file_path_or_url}"
            )
            return False

        try:
            path_obj = Path(str(target_file))
            await path_obj.unlink(missing_ok=True)
            return True
        except Exception as e:
            logger.error(f"Failed to delete file '{file_path_or_url}': {e}")
            return False

    async def exists(self, file_path_or_url: str) -> bool:
        """Verify the existence of a file within the local storage sandbox.

        Args:
            file_path_or_url: Stored web URL or relative path of the target file.

        Returns:
            True if the target file exists and is within storage boundaries, False otherwise.
        """
        if not file_path_or_url:
            return False

        target_file = self._resolve_path(file_path_or_url)
        if target_file is None:
            return False

        try:
            path_obj = Path(str(target_file))
            return await path_obj.exists()
        except Exception:
            return False

    async def get_url(self, file_path_or_url: str) -> str:
        """Resolve the normalized web-accessible URL for a stored asset.

        Args:
            file_path_or_url: Stored web URL or relative path of the target file.

        Returns:
            The normalized web URL string.
        """
        if not file_path_or_url:
            return ""

        cleaned_path = file_path_or_url.replace("\\", "/").strip()
        if self.url_prefix and cleaned_path.startswith(self.url_prefix):
            return cleaned_path

        target_file = self._resolve_path(file_path_or_url)
        if target_file is not None and target_file.is_relative_to(self.base_path):
            relative_part = target_file.relative_to(self.base_path).as_posix()
            return f"{self.url_prefix}/{relative_part}" if self.url_prefix else f"/{relative_part}"

        trimmed = cleaned_path.lstrip("/")
        return f"{self.url_prefix}/{trimmed}" if self.url_prefix else f"/{trimmed}"