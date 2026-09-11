"""Storage Provider Base Interface.

This module defines the primary storage contract for the ZCore framework, facilitating
file uploads, raw binary streaming, URL resolution, and secure asset deletions. It also
provides a FastAPI dependency stub to dynamically resolve storage providers from the IoC container.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator

from fastapi import UploadFile

from zcore.kernel.di import Inject


class StorageProvider(ABC):
    """Abstract Base Class specifying standard storage platform capabilities.

    Implementations must inherit from this class to manage file operations across
    local filesystems or distributed cloud object stores.
    """

    @abstractmethod
    async def upload(self, file: UploadFile, folder: str = "") -> str:
        """Upload an evaluated file payload and return its public or relative web URL.

        Args:
            file: The validated UploadFile instance.
            folder: Target subdirectory or bucket partition. Defaults to "".

        Returns:
            The normalized web URL path of the persisted asset.
        """
        pass

    @abstractmethod
    async def upload_stream(
        self, file_stream: AsyncGenerator[bytes, None], filename: str, folder: str = ""
    ) -> str:
        """Stream raw binary chunks directly to the storage target.

        Args:
            file_stream: Asynchronous binary data chunk generator.
            filename: The target filename to assign to the streamed asset.
            folder: Target subdirectory or bucket partition. Defaults to "".

        Returns:
            The normalized web URL path of the persisted asset.
        """
        pass

    @abstractmethod
    async def delete(self, file_path_or_url: str) -> bool:
        """Securely remove a file from the storage platform.

        Args:
            file_path_or_url: The stored URL or identifier of the asset to delete.

        Returns:
            True if deletion succeeds, False otherwise.
        """
        pass

    @abstractmethod
    async def exists(self, file_path_or_url: str) -> bool:
        """Verify the physical presence of an asset on the storage platform.

        Args:
            file_path_or_url: The stored URL or identifier of the target asset.

        Returns:
            True if the target asset exists, False otherwise.
        """
        pass

    @abstractmethod
    async def get_url(self, file_path_or_url: str) -> str:
        """Resolve the publicly accessible web URL for an asset.

        Args:
            file_path_or_url: The stored identifier or relative asset path.

        Returns:
            The formatted web URL string.
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