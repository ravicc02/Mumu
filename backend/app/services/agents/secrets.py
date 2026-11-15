from __future__ import annotations

import ctypes
import hashlib
import os
import tempfile
from ctypes import wintypes
from pathlib import Path

from ...config import Settings


class SecretStoreError(RuntimeError):
    pass


class SecretNotFound(SecretStoreError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class DpapiSecretStore:
    """Store model credentials as per-user Windows DPAPI blobs."""

    _ENTROPY = b"mumu.agent.model-provider.v1"
    _CRYPTPROTECT_UI_FORBIDDEN = 0x1

    def __init__(self, root: Path):
        self.root = root.resolve()

    def _path(self, reference: str) -> Path:
        digest = hashlib.sha256(reference.encode("utf-8")).hexdigest()
        path = (self.root / f"{digest}.bin").resolve()
        if path.parent != self.root:
            raise SecretStoreError("密钥引用越过安全目录")
        return path

    @staticmethod
    def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
        buffer = ctypes.create_string_buffer(data)
        blob = _DataBlob(
            len(data),
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
        )
        return blob, buffer

    @staticmethod
    def _local_free(pointer) -> None:
        local_free = ctypes.windll.kernel32.LocalFree
        local_free.argtypes = [ctypes.c_void_p]
        local_free.restype = ctypes.c_void_p
        local_free(pointer)

    @classmethod
    def _protect_windows(cls, plaintext: bytes) -> bytes:
        if os.name != "nt":
            raise SecretStoreError("当前系统不支持 Windows DPAPI 密钥存储")
        source, source_buffer = cls._blob(plaintext)
        entropy, entropy_buffer = cls._blob(cls._ENTROPY)
        output = _DataBlob()
        crypt_protect = ctypes.windll.crypt32.CryptProtectData
        crypt_protect.argtypes = [
            ctypes.POINTER(_DataBlob),
            wintypes.LPCWSTR,
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        crypt_protect.restype = wintypes.BOOL
        if not crypt_protect(
            ctypes.byref(source),
            "Mumu model credential",
            ctypes.byref(entropy),
            None,
            None,
            cls._CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output),
        ):
            del source_buffer, entropy_buffer
            raise SecretStoreError("Windows DPAPI 无法保护模型密钥")
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            cls._local_free(output.pbData)
            del source_buffer, entropy_buffer

    @classmethod
    def _unprotect_windows(cls, ciphertext: bytes) -> bytes:
        if os.name != "nt":
            raise SecretStoreError("当前系统不支持 Windows DPAPI 密钥存储")
        source, source_buffer = cls._blob(ciphertext)
        entropy, entropy_buffer = cls._blob(cls._ENTROPY)
        output = _DataBlob()
        crypt_unprotect = ctypes.windll.crypt32.CryptUnprotectData
        crypt_unprotect.argtypes = [
            ctypes.POINTER(_DataBlob),
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        crypt_unprotect.restype = wintypes.BOOL
        if not crypt_unprotect(
            ctypes.byref(source),
            None,
            ctypes.byref(entropy),
            None,
            None,
            cls._CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output),
        ):
            del source_buffer, entropy_buffer
            raise SecretStoreError("Windows DPAPI 无法读取模型密钥")
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            cls._local_free(output.pbData)
            del source_buffer, entropy_buffer

    def put(self, reference: str, value: str) -> None:
        if not value:
            raise ValueError("密钥不能为空")
        self.root.mkdir(parents=True, exist_ok=True)
        target = self._path(reference)
        ciphertext = self._protect_windows(value.encode("utf-8"))
        handle, temp_name = tempfile.mkstemp(prefix=".mumu-secret-", dir=self.root)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(ciphertext)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, target)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    def get(self, reference: str) -> str:
        target = self._path(reference)
        try:
            ciphertext = target.read_bytes()
        except FileNotFoundError as error:
            raise SecretNotFound("模型密钥不存在") from error
        try:
            return self._unprotect_windows(ciphertext).decode("utf-8")
        except UnicodeDecodeError as error:
            raise SecretStoreError("模型密钥格式无效") from error

    def delete(self, reference: str) -> None:
        try:
            self._path(reference).unlink()
        except FileNotFoundError:
            pass


def secret_store_for(settings: Settings) -> DpapiSecretStore:
    return DpapiSecretStore(settings.secrets_root)
