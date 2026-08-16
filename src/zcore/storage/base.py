"""Storage Provider Base Interface.

This module defines the primary storage contract for the ZCore framework, facilitating
file uploads, raw binary streaming, and secure asset deletions. It also provides
a FastAPI dependency stub to dynamically resolve storage providers from the IoC container.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator

from fastapi import UploadFile

from zcore.kernel.di import Inject


class StorageProvider(ABC):
    """Abstract Base Class specifying standard storage platform capabilities.

    Implementations must inherit from this class to manage file operations (e.g., local
    filesystems, AWS S3, or Google Cloud Storage) within the ZCore framework.
    """

    @abstractmethod
    async def upload(self, file: UploadFile, folder: str) -> str:
        """Upload a file to the configured storage target.

        Args:
            file: The validated UploadFile object representing the user input.
            folder: The target sub-directory or category bucket for storage.

        Returns:
            The safe relative or absolute path of the persisted asset.
        """
        pass

    @abstractmethod
    async def upload_stream(
        self, file_stream: AsyncGenerator[bytes, None], filename: str, folder: str
    ) -> str:
        """Stream binary raw chunks directly to the storage platform.

        Args:
            file_stream: An asynchronous generator yielding chunks of file bytes.
            filename: The target filename to assign to the streamed asset.
            folder: The target sub-directory or bucket directory for storage.

        Returns:
            The safe path representing the persisted streamed asset.
        """
        pass

    @abstractmethod
    async def delete(self, file_path: str) -> bool:
        """Securely delete a file from the storage platform.

        Args:
            file_path: The stored path of the file to remove.

        Returns:
            True if the deletion succeeds, False otherwise.
        """
        pass


async def get_storage_provider(provider: Inject[StorageProvider]) -> StorageProvider:
    """FastAPI dependency to resolve the active storage provider.

    Args:
        provider: Resolved StorageProvider instance retrieved from the global IoC container.

    Returns:
        The active storage provider instance.
    """
    return provider
