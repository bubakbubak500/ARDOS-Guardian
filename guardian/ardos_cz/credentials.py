"""Device secrets live in Windows Credential Manager, never in station JSON."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import json
import sys


class WindowsCredentials:
    def __init__(self, server: str, station: str, profile: str):
        identity = f"{server}\n{station}\n{profile}".encode()
        self.target = 'Guardian/ARDOS-CZ/' + hashlib.sha256(identity).hexdigest()

    def _api(self):
        if sys.platform != 'win32':
            raise RuntimeError('ARDOS CZ device storage requires Windows Credential Manager')
        class Credential(ctypes.Structure):
            _fields_ = [('Flags', wintypes.DWORD), ('Type', wintypes.DWORD),
                ('TargetName', wintypes.LPWSTR), ('Comment', wintypes.LPWSTR),
                ('LastWritten', wintypes.FILETIME), ('CredentialBlobSize', wintypes.DWORD),
                ('CredentialBlob', ctypes.POINTER(ctypes.c_ubyte)), ('Persist', wintypes.DWORD),
                ('AttributeCount', wintypes.DWORD), ('Attributes', ctypes.c_void_p),
                ('TargetAlias', wintypes.LPWSTR), ('UserName', wintypes.LPWSTR)]
        api = ctypes.WinDLL('advapi32', use_last_error=True)
        api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.POINTER(ctypes.POINTER(Credential))]
        api.CredReadW.restype = wintypes.BOOL
        api.CredWriteW.argtypes = [ctypes.POINTER(Credential), wintypes.DWORD]
        api.CredWriteW.restype = wintypes.BOOL
        api.CredFree.argtypes = [ctypes.c_void_p]
        return api, Credential

    def load(self) -> dict | None:
        api, cls = self._api()
        pointer = ctypes.POINTER(cls)()
        if not api.CredReadW(self.target, 1, 0, ctypes.byref(pointer)):
            error = ctypes.get_last_error()
            if error == 1168:
                return None
            raise OSError(error, 'Credential Manager read failed')
        try:
            value = pointer.contents
            return json.loads(ctypes.string_at(value.CredentialBlob, value.CredentialBlobSize))
        finally:
            api.CredFree(pointer)

    def save(self, value: dict) -> None:
        api, cls = self._api()
        data = json.dumps(value).encode()
        blob = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        credential = cls(Type=1, TargetName=self.target, CredentialBlobSize=len(data),
                         CredentialBlob=blob, Persist=2, UserName='Guardian ARDOS CZ')
        if not api.CredWriteW(ctypes.byref(credential), 0):
            raise OSError(ctypes.get_last_error(), 'Credential Manager write failed')
