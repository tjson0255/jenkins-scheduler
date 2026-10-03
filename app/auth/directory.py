"""Active Directory（LDAP）でのユーザー認証と、AD グループによる権限の判定。

- 本人の資格情報で LDAP バインドして確かめる（ツール用のサービスアカウントは不要）
- 空のパスワードは拒否する（AD は空パスワードの「認証なしバインド」を成功扱いにすることがあるため）
- 権限のグループは DN・グループ名（sAMAccountName / cn）・メールアドレス（メーリングリスト）で指定できる
- 入れ子のグループは LDAP_MATCHING_RULE_IN_CHAIN（1.2.840.113556.1.4.1941）で判定する
"""

from __future__ import annotations

import logging
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.auth.roles import ADMIN, MEMO_EDITOR, VIEWER, strongest
from app.config import Settings

log = logging.getLogger(__name__)

MATCHING_RULE_IN_CHAIN = "1.2.840.113556.1.4.1941"
GROUP_CACHE_SECONDS = 600


class AuthError(Exception):
    """ユーザー名・パスワードの誤り、または権限が無い。"""


class DirectoryUnavailable(Exception):
    """AD サーバーに接続できない。"""


@dataclass
class DirectoryUser:
    username: str
    display_name: str
    role: str
    dn: str | None = None


def split_groups(value: str) -> list[str]:
    return [g.strip() for g in (value or "").replace(";", ",").split(",") if g.strip()] if "=" not in (value or "") else _split_dns(value)


def _split_dns(value: str) -> list[str]:
    # DN 自体にカンマが含まれるので、「;」区切りか、"CN=" の手前で区切る
    if ";" in value:
        return [g.strip() for g in value.split(";") if g.strip()]
    parts, cur = [], []
    for token in value.split(","):
        t = token.strip()
        if cur and "=" in t and t.split("=", 1)[0].strip().upper() == "CN":
            parts.append(",".join(cur))
            cur = []
        cur.append(t)
    if cur:
        parts.append(",".join(cur))
    return [p.strip() for p in parts if p.strip()]


def role_groups(settings: Settings) -> dict[str, list[str]]:
    return {
        ADMIN: split_groups(settings.ldap_admin_groups),
        MEMO_EDITOR: split_groups(settings.ldap_memo_editor_groups),
        VIEWER: split_groups(settings.ldap_viewer_groups),
    }


def normalize_username(raw: str, settings: Settings) -> tuple[str, str]:
    """入力されたユーザー名から (バインドに使う名前, 表示・記録用のアカウント名) を作る。"""
    u = (raw or "").strip()
    if not u:
        raise AuthError("ユーザー名を入力してください")
    if "\\" in u:
        account = u.split("\\", 1)[1]
        return u, account
    if "@" in u:
        return u, u.split("@", 1)[0]
    if settings.ldap_upn_suffix:
        return f"{u}@{settings.ldap_upn_suffix}", u
    if settings.ldap_domain:
        return f"{settings.ldap_domain}\\{u}", u
    return u, u


class LdapDirectory:
    def __init__(self, settings: Settings, connection_factory: Callable | None = None):
        self.settings = settings
        self._connect = connection_factory or self._default_connection
        self._group_cache: dict[str, tuple[float, list[str]]] = {}

    # ------------------------------------------------------------------ 接続
    def _default_connection(self, bind_name: str, password: str):
        from ldap3 import SIMPLE, Connection, Server, ServerPool, Tls, FIRST

        tls = Tls(
            validate=ssl.CERT_REQUIRED,
            ca_certs_file=str(self.settings.ldap_ca_bundle) if self.settings.ldap_ca_bundle else None,
            version=ssl.PROTOCOL_TLS_CLIENT,
        )
        urls = [u.strip() for u in self.settings.ldap_urls.split(",") if u.strip()]
        if not urls:
            raise DirectoryUnavailable("LDAP_URLS が設定されていません")
        servers = [
            Server(u, use_ssl=u.lower().startswith("ldaps://"), tls=tls, connect_timeout=self.settings.ldap_timeout_seconds)
            for u in urls
        ]
        pool = ServerPool(servers, FIRST, active=1, exhaust=True)
        conn = Connection(
            pool, user=bind_name, password=password, authentication=SIMPLE,
            receive_timeout=self.settings.ldap_timeout_seconds, raise_exceptions=False, read_only=True,
        )
        if self.settings.ldap_start_tls:
            conn.open()
            conn.start_tls()
        return conn

    # ------------------------------------------------------------------ 認証
    def authenticate(self, username: str, password: str) -> DirectoryUser:
        from ldap3.core.exceptions import LDAPException
        from ldap3.utils.conv import escape_filter_chars

        if not password:
            raise AuthError("パスワードを入力してください")
        bind_name, account = normalize_username(username, self.settings)
        try:
            conn = self._connect(bind_name, password)
            if not conn.bind():
                log.info("AD 認証に失敗しました user=%s result=%s", account, getattr(conn, "result", {}).get("description"))
                raise AuthError("ユーザー名またはパスワードが違います")
            try:
                flt = (
                    f"(&(objectClass=user)(userPrincipalName={escape_filter_chars(bind_name)}))"
                    if "@" in bind_name
                    else f"(&(objectClass=user)(sAMAccountName={escape_filter_chars(account)}))"
                )
                conn.search(self.settings.ldap_base_dn, flt, attributes=["distinguishedName", "displayName", "sAMAccountName", "memberOf"], size_limit=1)
                if not conn.entries:
                    raise AuthError("AD にユーザーが見つかりません")
                entry = conn.entries[0]
                dn = str(entry.entry_dn)
                display = str(entry.displayName.value or account) if "displayName" in entry else account
                sam = str(entry.sAMAccountName.value or account) if "sAMAccountName" in entry else account
                member_of = [str(g).lower() for g in (entry.memberOf.values if "memberOf" in entry else [])]
                roles = [role for role, groups in role_groups(self.settings).items() if self._is_member(conn, dn, member_of, groups)]
            finally:
                conn.unbind()
        except LDAPException as exc:
            log.error("AD サーバーに接続できません: %s", exc)
            raise DirectoryUnavailable("AD サーバーに接続できません") from exc
        role = strongest(roles)
        if role is None:
            log.info("AD 認証は成功したが、権限のグループに入っていません user=%s", sam)
            raise AuthError("このツールを使う権限がありません（AD グループに登録されていません）")
        return DirectoryUser(username=sam, display_name=display, role=role, dn=dn)

    def _is_member(self, conn, user_dn: str, member_of: list[str], groups: list[str]) -> bool:
        from ldap3.utils.conv import escape_filter_chars

        for group_dn in self._resolve_groups(conn, groups):
            if not self.settings.ldap_nested_groups:
                if group_dn.lower() in member_of:
                    return True
                continue
            flt = (
                f"(&(distinguishedName={escape_filter_chars(user_dn)})"
                f"(memberOf:{MATCHING_RULE_IN_CHAIN}:={escape_filter_chars(group_dn)}))"
            )
            conn.search(self.settings.ldap_base_dn, flt, attributes=["distinguishedName"], size_limit=1)
            if conn.entries:
                return True
        return False

    def _resolve_groups(self, conn, groups: list[str]) -> list[str]:
        """グループ名・メールアドレスで指定されたものを DN に直す（10分キャッシュ）。"""
        from ldap3.utils.conv import escape_filter_chars

        out: list[str] = []
        for g in groups:
            if "=" in g and "," in g:
                out.append(g)
                continue
            cached = self._group_cache.get(g.lower())
            if cached and time.monotonic() - cached[0] < GROUP_CACHE_SECONDS:
                out.extend(cached[1])
                continue
            v = escape_filter_chars(g)
            flt = f"(&(objectClass=group)(|(mail={v})(proxyAddresses=smtp:{v})(sAMAccountName={v})(cn={v})))"
            conn.search(self.settings.ldap_base_dn, flt, attributes=["distinguishedName"])
            dns = [str(e.entry_dn) for e in conn.entries]
            if not dns:
                log.warning("AD グループが見つかりません: %s", g)
            self._group_cache[g.lower()] = (time.monotonic(), dns)
            out.extend(dns)
        return out


class MockDirectory:
    """開発用。AUTH_MOCK_USERS（user:password:role[:表示名] をカンマ区切り）の利用者でログインできる。"""

    def __init__(self, settings: Settings):
        self.users: dict[str, tuple[str, str, str]] = {}
        for item in (settings.auth_mock_users or "").split(","):
            parts = [p.strip() for p in item.split(":")]
            if len(parts) >= 3 and parts[0]:
                self.users[parts[0].lower()] = (parts[1], parts[2], parts[3] if len(parts) > 3 else parts[0])

    def authenticate(self, username: str, password: str) -> DirectoryUser:
        u = (username or "").strip().lower()
        if not password:
            raise AuthError("パスワードを入力してください")
        rec = self.users.get(u)
        if not rec or rec[0] != password:
            raise AuthError("ユーザー名またはパスワードが違います")
        if rec[1] not in (ADMIN, MEMO_EDITOR, VIEWER):
            raise AuthError("このツールを使う権限がありません")
        return DirectoryUser(username=u, display_name=rec[2], role=rec[1])


class SharedAdminDirectory:
    """共有の管理者アカウント（AUTH_MODE=shared_admin）。Jenkins を操作する人だけでパスワードを共有する。"""

    def __init__(self, settings: Settings):
        self.username = (settings.admin_username or "admin").strip()
        self.password_hash = settings.admin_password_hash.strip()
        self.password = settings.admin_password
        if not self.password_hash and not self.password:
            raise ValueError("AUTH_MODE=shared_admin には ADMIN_PASSWORD_HASH（または ADMIN_PASSWORD）が必要です")
        if not self.password_hash:
            log.warning("管理者パスワードが平文（ADMIN_PASSWORD）で設定されています。python -m app --hash-password で作った ADMIN_PASSWORD_HASH に置き換えてください")

    def authenticate(self, username: str, password: str) -> DirectoryUser:
        import hmac

        from app.auth.passwords import verify_password

        if not password:
            raise AuthError("パスワードを入力してください")
        name_ok = hmac.compare_digest((username or "").strip().lower().encode(), self.username.lower().encode())
        if self.password_hash:
            pass_ok = verify_password(password, self.password_hash)
        else:
            pass_ok = hmac.compare_digest(password.encode("utf-8"), self.password.encode("utf-8"))
        if not (name_ok and pass_ok):
            raise AuthError("ユーザー名またはパスワードが違います")
        return DirectoryUser(username=self.username, display_name="管理者", role=ADMIN)


def make_directory(settings: Settings):
    mode = settings.effective_auth_mode
    if mode == "shared_admin":
        return SharedAdminDirectory(settings)
    if mode == "ldap":
        return LdapDirectory(settings)
    if mode == "mock":
        log.warning("AUTH_MODE=mock（開発用の仮ユーザー）でログインを受け付けます。本番では使わないでください")
        return MockDirectory(settings)
    return None
