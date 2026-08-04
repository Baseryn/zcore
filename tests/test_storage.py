import os
from io import BytesIO
from typing import AsyncGenerator
import pytest
import aiofiles
from fastapi import UploadFile

from zcore.exceptions.base import AppException, ValidationError
from zcore.storage.local import LocalStorageProvider
from zcore.storage.validators import (
    FileExtensionValidator,
    MaxFileSizeValidator,
    SafeMimeTypeValidator,
)
from zcore.storage.base import get_storage_provider, StorageProvider
from zcore.kernel.di import container

def create_mock_upload_file(content: bytes, filename: str, size: int | None = None) -> UploadFile:
    file_obj = BytesIO(content)
    upload_file = UploadFile(file=file_obj, filename=filename)
    if size is not None:
        upload_file.size = size
    else:
        if hasattr(upload_file, "size"):
            delattr(upload_file, "size")
    return upload_file

@pytest.mark.anyio
@pytest.mark.parametrize(
    "malicious_folder",
    [
        "../",
        "../../etc",
        "..\\..\\",
    ]
)
async def test_storage_path_traversal_prevention(test_storage_dir: str, malicious_folder: str) -> None:
    provider = LocalStorageProvider(base_path=test_storage_dir)
    file = create_mock_upload_file(b"test data", "malicious.txt")
    
    with pytest.raises(AppException) as exc_info:
        await provider.upload(file, malicious_folder)
    assert "Path traversal attempt detected" in str(exc_info.value)

    async def fake_stream() -> AsyncGenerator[bytes, None]:
        yield b"chunk"

    with pytest.raises(AppException) as exc_info:
        await provider.upload_stream(fake_stream(), "malicious.txt", malicious_folder)
    assert "Path traversal attempt detected" in str(exc_info.value)

    delete_success = await provider.delete(f"{test_storage_dir}/{malicious_folder}/target.txt")
    assert delete_success is False

@pytest.mark.parametrize(
    "content, filename, allowed_mimes, should_pass, error_message",
    [
        (b"\xff\xd8\xff\xe0\x00\x10JFIF", "image.jpg", ["image/jpeg"], True, ""),
        (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", "image.png", ["image/jpeg"], False, "MIME-type is not allowed"),
        (b"<?php echo 'hello'; ?>", "test.jpg", ["image/jpeg"], False, "Security policy violation"),
        (b"<script>alert(1)</script>", "evil.jpg", ["image/jpeg"], False, "Security policy violation"),
        (b"MZ\x90\x00\x03\x00\x00\x00", "payload.jpg", ["image/jpeg"], False, "Security policy violation"),
        (b"#!/bin/sh\nrm -rf /", "exploit.jpg", ["image/jpeg"], False, "Security policy violation"),
    ]
)
def test_validator_mime_magic_bytes(
    content: bytes,
    filename: str,
    allowed_mimes: list[str],
    should_pass: bool,
    error_message: str
) -> None:
    validator = SafeMimeTypeValidator(allowed_mimes=allowed_mimes)
    file = create_mock_upload_file(content, filename)
    
    if should_pass:
        validator(file)
    else:
        with pytest.raises(ValidationError) as exc_info:
            validator(file)
        assert error_message in str(exc_info.value)

@pytest.mark.parametrize(
    "max_size_mb, size_property, content_len, should_pass",
    [
        (1.0, 500 * 1024, b"dummy", True),
        (1.0, 2 * 1024 * 1024, b"dummy", False),
        (1.0, None, 500 * 1024, True),
        (1.0, None, 2 * 1024 * 1024, False),
    ]
)
def test_validator_max_file_size(
    max_size_mb: float,
    size_property: int | None,
    content_len: int,
    should_pass: bool
) -> None:
    validator = MaxFileSizeValidator(max_size_mb=max_size_mb)
    content = b"x" * content_len if size_property is None else b"short"
    
    file = create_mock_upload_file(content, "test.bin", size=size_property)
    
    if should_pass:
        validator(file)
    else:
        with pytest.raises(ValidationError) as exc_info:
            validator(file)
        assert "exceeds the limit" in str(exc_info.value)

@pytest.mark.anyio
async def test_storage_successful_upload(test_storage_dir: str) -> None:
    provider = LocalStorageProvider(base_path=test_storage_dir)
    file = create_mock_upload_file(b"test data content", "hello.txt")
    path = await provider.upload(file, "text_files")
    assert os.path.exists(path)
    with open(path, "rb") as f:
        assert f.read() == b"test data content"

@pytest.mark.anyio
async def test_storage_successful_upload_stream(test_storage_dir: str) -> None:
    provider = LocalStorageProvider(base_path=test_storage_dir)
    async def fake_stream() -> AsyncGenerator[bytes, None]:
        yield b"chunk_one_"
        yield b"chunk_two"
    path = await provider.upload_stream(fake_stream(), "streamed.bin", "streams")
    assert os.path.exists(path)
    with open(path, "rb") as f:
        assert f.read() == b"chunk_one_chunk_two"

@pytest.mark.anyio
async def test_storage_collision_prevention(test_storage_dir: str) -> None:
    provider = LocalStorageProvider(base_path=test_storage_dir)
    f1 = create_mock_upload_file(b"content 1", "test.txt")
    f2 = create_mock_upload_file(b"content 2", "test.txt")
    p1 = await provider.upload(f1, "collision")
    p2 = await provider.upload(f2, "collision")
    assert p1 != p2
    assert os.path.exists(p1)
    assert os.path.exists(p2)

@pytest.mark.anyio
async def test_storage_auto_directory_creation(test_storage_dir: str) -> None:
    provider = LocalStorageProvider(base_path=test_storage_dir)
    f = create_mock_upload_file(b"data", "test.txt")
    path = await provider.upload(f, "new/nested/folder")
    assert os.path.exists(path)

@pytest.mark.anyio
async def test_storage_successful_delete(test_storage_dir: str) -> None:
    provider = LocalStorageProvider(base_path=test_storage_dir)
    f = create_mock_upload_file(b"data", "test.txt")
    path = await provider.upload(f, "docs")
    assert os.path.exists(path)
    res = await provider.delete(path)
    assert res is True
    assert not os.path.exists(path)

@pytest.mark.anyio
async def test_storage_delete_non_existent(test_storage_dir: str) -> None:
    provider = LocalStorageProvider(base_path=test_storage_dir)
    fake_path = os.path.join(test_storage_dir, "docs", "missing.txt")
    res = await provider.delete(fake_path)
    assert res is True

def test_validator_file_extension_case_insensitive() -> None:
    validator = FileExtensionValidator(allowed_extensions=["png", "JPEG"])
    f1 = create_mock_upload_file(b"", "image.PNG")
    f2 = create_mock_upload_file(b"", "photo.jpeg")
    f3 = create_mock_upload_file(b"", "avatar.png")
    validator(f1)
    validator(f2)
    validator(f3)

def test_validator_file_extension_blocked() -> None:
    validator = FileExtensionValidator(allowed_extensions=["pdf"])
    f = create_mock_upload_file(b"", "script.py")
    with pytest.raises(ValidationError):
        validator(f)

def test_validator_file_extension_missing() -> None:
    validator = FileExtensionValidator(allowed_extensions=["pdf"])
    f = create_mock_upload_file(b"", "config")
    with pytest.raises(ValidationError):
        validator(f)

def test_validator_file_extension_double_extension_attack() -> None:
    validator = FileExtensionValidator(allowed_extensions=["pdf", "png"])
    f1 = create_mock_upload_file(b"", "doc.pdf.exe")
    f2 = create_mock_upload_file(b"", "avatar.png.php")
    with pytest.raises(ValidationError):
        validator(f1)
    with pytest.raises(ValidationError):
        validator(f2)

def test_validator_mime_fallback() -> None:
    validator = SafeMimeTypeValidator(allowed_mimes=["application/json", "text/plain"])
    f1 = create_mock_upload_file(b'{"key": "value"}', "data.json")
    f2 = create_mock_upload_file(b"plain text", "note.txt")
    validator(f1)
    validator(f2)

def test_validator_mime_read_error() -> None:
    validator = SafeMimeTypeValidator(allowed_mimes=["image/png"])
    class ErrorFile:
        def read(self, *args, **kwargs):
            raise OSError("Hardware read error")
        def seek(self, *args, **kwargs):
            pass
    upload_file = UploadFile(file=ErrorFile(), filename="test.png")
    with pytest.raises(ValidationError) as exc_info:
        validator(upload_file)
    assert "Failed to validate file signatures." in str(exc_info.value)

@pytest.mark.anyio
async def test_storage_os_permission_error(test_storage_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = LocalStorageProvider(base_path=test_storage_dir)
    file = create_mock_upload_file(b"test data", "permission.txt")
    def mock_open(*args, **kwargs):
        raise OSError("Mock disk error")
    monkeypatch.setattr(aiofiles, "open", mock_open)
    with pytest.raises(AppException) as exc_info:
        await provider.upload(file, "uploads")
    assert "Error saving file" in str(exc_info.value)

@pytest.mark.anyio
async def test_storage_provider_dependency(test_storage_dir: str) -> None:
    provider_instance = LocalStorageProvider(base_path=test_storage_dir)
    container.register_singleton(StorageProvider, provider_instance)
    resolved = await get_storage_provider(provider_instance)
    assert resolved is provider_instance