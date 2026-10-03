"""Windows 資格情報マネージャーからの読み出し（外部ライブラリを使わず、Win32 API の CredReadW を ctypes で呼ぶ）。

登録は Windows 標準の cmdkey で行う:
    cmdkey /generic:jenkins-scheduler /user:scheduler-bot /pass
（/pass の後ろを空にすると、トークンを画面に出さずに入力できる）

以前の版で keyring ライブラリを使って登録したものも、同じ場所（汎用資格情報）なのでそのまま読める。
"""

from __future__ import annotations

import sys

CRED_TYPE_GENERIC = 1
ERROR_NOT_FOUND = 1168


def _read(target: str) -> tuple[str, str] | None:
    """汎用資格情報 target の (ユーザー名, パスワード) を返す。無ければ None。"""
    import ctypes
    from ctypes import wintypes

    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    class CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIALW))]
    advapi32.CredReadW.restype = wintypes.BOOL
    advapi32.CredFree.argtypes = [ctypes.c_void_p]

    pcred = ctypes.POINTER(CREDENTIALW)()
    if not advapi32.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pcred)):
        err = ctypes.get_last_error()
        if err == ERROR_NOT_FOUND:
            return None
        raise OSError(err, f"資格情報マネージャーの読み出しに失敗しました (target={target})")
    try:
        cred = pcred.contents
        blob = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
        # cmdkey・keyring とも UTF-16-LE で保存する
        return cred.UserName or "", blob.decode("utf-16-le")
    finally:
        advapi32.CredFree(pcred)


def get_password(service: str, username: str) -> str | None:
    """service（と username）に対応するパスワードを返す。keyring の Windows 版と同じ探し方をする。"""
    if sys.platform != "win32":
        raise RuntimeError("資格情報マネージャーは Windows でのみ使えます。JENKINS_TOKEN_SOURCE=env にしてください")
    found = _read(service)
    if found and (not username or found[0] == username):
        return found[1]
    # 同じ service に別のユーザーが登録済みのときは「ユーザー名@service」に保存される
    found = _read(f"{username}@{service}") if username else None
    return found[1] if found else None
