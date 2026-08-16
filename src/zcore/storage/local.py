"""Local Filesystem Storage Provider.

This module implements storage operations targeting the host machine's filesystem.
It protects against path traversal attacks by enforcing strict path bounds checks and
evaluating user-supplied files through configurable security validators.
"""

import uuid
from collections.abc import AsyncGenerator
from pathlib import PureWindowsPath

import aiofiles
import structlog
from anyio import Path
from fastapi import UploadFile

from zcore.exceptions.base import AppException
from zcore.storage.base import StorageProvider
from zcore.storage.validators import BaseStorageValidator

logger = structlog.get_logger()


class LocalStorageProvider(StorageProvider):
    """Storage provider targeting the host filesystem.

    Saves incoming files to local directories, utilizing randomized naming structures
    to prevent file name collisions and path validation to prevent directory traversal.

    Attributes:
        base_path: The absolute or relative root path of the host upload directory.
        validators: List of security/validation rules executed against uploaded files.
    """

    def __init__(
        self, base_path: str, validators: list[BaseStorageValidator] | None = None
    ) -> None:
        """Initialize the LocalStorageProvider.

        Args:
            base_path: The root directory reserved for file uploads.
            validators: Configured file validators. Defaults to an empty list.
        """
        self.base_path = base_path
        self.validators = validators or []

    async def generate_path(self, filename: str, related_type: str) -> str:
        """Generate a secure, sanitized, and collision-resistant path for a new file.

        Enforces strict subdirectory constraints to mitigate path traversal exploits.

        Args:
            filename: The original name of the file to extract extensions from.
            related_type: Category identifier mapping to subfolder structures.

        Returns:
            The fully resolved destination path string on the local filesystem.

        Raises:
            AppException: If a directory traversal attempt is detected.
        """
        base = await Path(self.base_path).resolve(strict=False)
        normalized_type = PureWindowsPath(related_type).as_posix()
        target_folder = await (Path(self.base_path) / normalized_type).resolve(
            strict=False
        )

        if not target_folder.is_relative_to(base):
            raise AppException("Path traversal attempt detected")

        uuid_file_name = str(uuid.uuid4())[:15]
        ext = Path(filename).suffix.lower()

        new_file_name = f"{uuid_file_name}{ext}"
        final_path = target_folder / new_file_name

        if not await target_folder.exists():
            await target_folder.mkdir(parents=True, exist_ok=True)

        return final_path.as_posix()

    def _validate_file(self, file: UploadFile) -> None:
        """Internal helper running the active validator list against a file.

        Args:
            file: The UploadFile instance to analyze.
        """
        for validator in self.validators:
            validator(file)

    async def upload(self, file: UploadFile, related_type: str) -> str:
        """Upload a file to the local directory path.

        Applies configured file validation, generates a secure path, and streams
        the raw file chunks to disk asynchronously.

        Args:
            file: The UploadFile payload to persist.
            related_type: Category identifier mapping to subfolder structures.

        Returns:
            The resolved local path of the saved file.

        Raises:
            AppException: If path traversal is detected or a filesystem write failure occurs.
        """
        self._validate_file(file)

        try:
            path = await self.generate_path(file.filename or "file", related_type)
            async with aiofiles.open(path, "wb") as buffer:
                while chunk := await file.read(1024 * 1024):
                    await buffer.write(chunk)
        except AppException as e:
            raise e
        except Exception as e:
            logger.error(
                f"Failed to upload file to local storage due to system error: {e}"
            )
            raise AppException("Error saving file")
        return path

    async def upload_stream(
        self, file_stream: AsyncGenerator[bytes, None], filename: str, related_type: str
    ) -> str:
        """Directly stream binary data chunks to a local file destination.

        Args:
            file_stream: Asynchronous binary data generator.
            filename: Target file name.
            related_type: Category identifier mapping to subfolder structures.

        Returns:
            The local path of the saved file.

        Raises:
            AppException: If path traversal is detected or a filesystem write failure occurs.
        """
        try:
            path = await self.generate_path(filename, related_type)
            async with aiofiles.open(path, "wb") as buffer:
                async for chunk in file_stream:
                    await buffer.write(chunk)
        except AppException as e:
            raise e
        except Exception as e:
            logger.error(
                f"Failed to stream upload file to local storage due to system error: {e}"
            )
            raise AppException("Error saving file")
        return path

    async def delete(self, file_path: str) -> bool:
        """Securely remove a file from local storage.

        Verifies that the target path resides within the configured root directory
        before unlinking the asset to prevent arbitrary file deletion exploits.

        Args:
            file_path: Relative or absolute local path of the file to remove.

        Returns:
            True if unlinking succeeds, False if traversal is detected or deletion fails.
        """
        try:
            base = await Path(self.base_path).resolve(strict=False)
            normalized_path = PureWindowsPath(file_path).as_posix()
            path_to_delete = await Path(normalized_path).resolve(strict=False)

            if not path_to_delete.is_relative_to(base):
                logger.warning(
                    f"Prevented arbitrary file deletion attempt outside base path: {file_path}"
                )
                return False

            await path_to_delete.unlink(missing_ok=True)
            return True
        except Exception as e:
            logger.error(f"Failed to delete file: {file_path}. Error: {e}")
            return False
