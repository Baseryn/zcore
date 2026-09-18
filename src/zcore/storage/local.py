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

    def _extract_key(self, file_path_or_url: str) -> str | None:
        """Extract the normalized storage key from a web URL, storage key, or filesystem path.

        Args:
            file_path_or_url: Web URL, storage key, or filesystem path.

        Returns:
            Normalized POSIX storage key, or None if an explicit path attempts sandbox traversal.
        """
        cleaned = file_path_or_url.replace("\\", "/").strip()

        if self.url_prefix and cleaned.startswith(self.url_prefix):
            return cleaned[len(self.url_prefix) :].lstrip("/")

        try:
            cand = StdPath(cleaned).resolve()
            if cand.is_relative_to(self.base_path):
                return cand.relative_to(self.base_path).as_posix()
            if StdPath(cleaned).is_absolute() or cleaned.startswith(("./", "../")):
                return None
        except Exception:
            pass

        return cleaned.lstrip("/")

    def _key_to_path(self, key: str | None) -> StdPath | None:
        """Safely resolve a storage key to an absolute filesystem path within sandbox boundaries.

        Args:
            key: Normalized storage key.

        Returns:
            Resolved absolute StdPath if within base_path boundaries, or None if outside.
        """
        if not key:
            return None

        try:
            resolved = (self.base_path / key).resolve()
            if resolved.is_relative_to(self.base_path) and resolved != self.base_path:
                return resolved
            return None
        except Exception:
            return None

    def _resolve_path(self, file_path_or_url: str) -> StdPath | None:
        """Resolve any incoming URL, storage key, or path to a validated filesystem path.

        Args:
            file_path_or_url: Web URL, storage key, or relative/absolute path.

        Returns:
            Resolved absolute StdPath if valid and within boundaries, or None otherwise.
        """
        if not file_path_or_url:
            return None
        key = self._extract_key(file_path_or_url)
        return self._key_to_path(key)

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
        ext = StdPath(filename).suffix.lower()
        secure_filename = f"{uuid.uuid4().hex}{ext}"
        storage_key = f"{normalized_folder}/{secure_filename}" if normalized_folder else secure_filename

        physical_path = self._key_to_path(storage_key)
        if physical_path is None:
            raise AppException("Path traversal attempt detected")

        await Path(str(physical_path.parent)).mkdir(parents=True, exist_ok=True)
        web_url = f"{self.url_prefix}/{storage_key}" if self.url_prefix else f"/{storage_key}"

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
            True if unlinking succeeds, False if traversal is detected or deletion fails.
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

        cleaned = file_path_or_url.replace("\\", "/").strip()
        if self.url_prefix and cleaned.startswith(self.url_prefix):
            return cleaned

        key = self._extract_key(file_path_or_url)
        if not key:
            return ""

        return f"{self.url_prefix}/{key}" if self.url_prefix else f"/{key}"