"""AD 認証と、AD グループによる権限分け（1. フルコントロール / 2. 自由記入のみ編集 / 3. 読み取り専用）。"""

import pytest
from fastapi.testclient import TestClient

from app.auth.directory import AuthError, LdapDirectory, normalize_username, split_groups
from app.auth.roles import ADMIN, MEMO_EDITOR, VIEWER
from app.config import Settings
from app.timeutil import local_today
from tests.conftest import CSRF_HEADERS

USERS = "admin:pw-a:admin:管理者,memo:pw-m:memo_editor:メモ係,view:pw-v:viewer:閲覧者"


@pytest.fixture
def auth_app(settings, mock_client):
    from app.main import create_app

    settings.auth_mode = "mock"
    settings.auth_mock_users = USERS
    app = create_app(settings, start_scheduler=False, client=mock_client)
    with TestClient(app, headers=CSRF_HEADERS) as c:
        yield c


def login(c, user, pw):
    r = c.post("/api/auth/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()


def test_login_required_and_redirect(auth_app):
    c = auth_app
    assert c.get("/api/targets").status_code == 401
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert c.get("/login").status_code == 200
    assert c.get("/static/app.css").status_code == 200  # ログイン画面の表示に必要
    assert c.get("/api/health").status_code in (200, 503)  # 監視用は認証対象外


def test_bad_password_and_lockout(auth_app):
    c = auth_app
    for _ in range(5):
        assert c.post("/api/auth/login", json={"username": "view", "password": "x"}).status_code == 401
    # 失敗が続いたら、正しいパスワードでもしばらく受け付けない
    assert c.post("/api/auth/login", json={"username": "view", "password": "pw-v"}).status_code == 429
    assert c.post("/api/auth/login", json={"username": "admin", "password": ""}).status_code == 401


def test_session_cookie_is_safe(auth_app):
    r = auth_app.post("/api/auth/login", json={"username": "admin", "password": "pw-a"})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie


def test_admin_can_do_everything(auth_app):
    c = auth_app
    me = login(c, "admin", "pw-a")
    assert me["role"] == ADMIN and me["display_name"] == "管理者" and me["can"] == {"admin": True, "edit_memo": True}
    t = c.post("/api/targets", json={"job_path": "buildset/core-pipeline"})
    assert t.status_code == 201
    audit = c.get("/api/audit?type=target").json()
    assert audit[0]["actor"] == "admin"


def test_memo_editor_can_only_edit_memo_text(auth_app):
    c = auth_app
    login(c, "admin", "pw-a")
    jenkins = c.post("/api/targets", json={"job_path": "buildset/core-pipeline"}).json()
    memo_item = c.post("/api/targets", json={"kind": "memo", "display_name": "計画"}).json()
    jsched = c.post("/api/schedules", json={"target_id": jenkins["id"], "start_date": local_today().isoformat(), "cron_expr": "0 3 * * *"}).json()
    c.post("/api/auth/logout")

    me = login(c, "memo", "pw-m")
    assert me["role"] == MEMO_EDITOR and me["can"] == {"admin": False, "edit_memo": True}
    # 自由記入の予定・メモは追加・変更・削除できる
    m = c.post("/api/schedules", json={"target_id": memo_item["id"], "start_date": local_today().isoformat(), "label": "QA"})
    assert m.status_code == 201
    assert c.patch(f"/api/schedules/{m.json()['id']}", json={"note": "本文"}).status_code == 200
    # Jenkins の予定・アイテム・実行・バックアップはできない
    assert c.post("/api/schedules", json={"target_id": jenkins["id"], "start_date": local_today().isoformat(), "cron_expr": "0 3 * * *"}).status_code == 403
    assert c.patch(f"/api/schedules/{jsched['id']}", json={"label": "x"}).status_code == 403
    assert c.delete(f"/api/schedules/{jsched['id']}").status_code == 403
    assert c.post(f"/api/schedules/{jsched['id']}/activate").status_code == 403
    assert c.post(f"/api/targets/{jenkins['id']}/run-now", json={}).status_code == 403
    # 自由記入アイテムとカテゴリは編集できる
    assert c.patch(f"/api/targets/{memo_item['id']}", json={"display_name": "計画（改）", "color": "#123456"}).status_code == 200
    new_memo = c.post("/api/targets", json={"kind": "memo", "display_name": "新しい行"})
    assert new_memo.status_code == 201
    assert c.delete(f"/api/targets/{new_memo.json()['id']}").status_code == 204
    cat = c.post("/api/categories", json={"name": "計画"})
    assert cat.status_code == 201
    assert c.patch(f"/api/categories/{cat.json()['id']}", json={"name": "計画（改）", "sort_order": 0}).status_code == 200
    assert c.delete(f"/api/categories/{cat.json()['id']}").status_code == 204
    # Jenkins アイテムは並び替えだけ（登録・設定変更・削除はできない）
    assert c.post("/api/targets", json={"kind": "jenkins", "job_path": "buildset/web-pipeline"}).status_code == 403
    assert c.patch(f"/api/targets/{jenkins['id']}", json={"sort_order": 5}).status_code == 200
    assert c.patch(f"/api/targets/{jenkins['id']}", json={"note": "担当: 山田"}).json()["note"] == "担当: 山田"  # メモは誰でも
    assert c.patch(f"/api/targets/{jenkins['id']}", json={"display_name": "x"}).status_code == 403
    assert c.patch(f"/api/targets/{jenkins['id']}", json={"enabled": False, "sort_order": 1}).status_code == 403
    assert c.delete(f"/api/targets/{jenkins['id']}").status_code == 403
    assert c.post("/api/backups").status_code == 403
    assert c.delete(f"/api/schedules/{m.json()['id']}").status_code == 204
    # 閲覧はできる
    assert c.get("/api/targets").status_code == 200


def test_viewer_is_read_only(auth_app):
    c = auth_app
    login(c, "admin", "pw-a")
    memo_item = c.post("/api/targets", json={"kind": "memo", "display_name": "計画"}).json()
    c.post("/api/auth/logout")
    me = login(c, "view", "pw-v")
    assert me["role"] == VIEWER and me["can"] == {"admin": False, "edit_memo": False}
    assert c.get("/api/targets").status_code == 200
    assert c.get("/api/schedules").status_code == 200
    assert c.post("/api/schedules", json={"target_id": memo_item["id"], "start_date": local_today().isoformat()}).status_code == 403
    assert c.post("/api/categories", json={"name": "x"}).status_code == 403
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/targets").status_code == 401  # ログアウト後は使えない


def test_csrf_header_and_origin_are_required(auth_app):
    c = auth_app
    login(c, "admin", "pw-a")
    r = c.post("/api/categories", json={"name": "x"}, headers={"X-Requested-With": ""})
    assert r.status_code == 403
    r = c.post("/api/categories", json={"name": "x"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert c.post("/api/categories", json={"name": "x"}).status_code == 201


def test_unknown_routes_default_deny_for_non_admin(auth_app):
    c = auth_app
    login(c, "memo", "pw-m")
    assert c.post("/api/targets/sync").status_code == 403
    assert c.post("/api/runs/1/retry").status_code == 403


# ---------------------------------------------------------------- AD（LDAP）
def test_username_forms():
    s = Settings(_env_file=None, ldap_upn_suffix="corp.local")
    assert normalize_username("yamada", s) == ("yamada@corp.local", "yamada")
    assert normalize_username("CORP\\yamada", s) == ("CORP\\yamada", "yamada")
    assert normalize_username("yamada@corp.local", s) == ("yamada@corp.local", "yamada")
    s2 = Settings(_env_file=None, ldap_domain="CORP")
    assert normalize_username("yamada", s2) == ("CORP\\yamada", "yamada")
    with pytest.raises(AuthError):
        normalize_username("  ", s)


def test_group_lists():
    assert split_groups("js-admins@corp.local, JS-Viewers") == ["js-admins@corp.local", "JS-Viewers"]
    assert split_groups("CN=A,OU=G,DC=corp,DC=local;CN=B,OU=G,DC=corp,DC=local") == ["CN=A,OU=G,DC=corp,DC=local", "CN=B,OU=G,DC=corp,DC=local"]
    assert split_groups("CN=A,OU=G,DC=corp,DC=local,CN=B,OU=G,DC=corp,DC=local") == ["CN=A,OU=G,DC=corp,DC=local", "CN=B,OU=G,DC=corp,DC=local"]


def make_fake_ad():
    """ldap3 の模擬サーバーで、AD のユーザーとグループ（メーリングリスト）を用意する。"""
    pytest.importorskip("ldap3")  # ldap3 は AD を使うときだけ入れる（requirements-ldap.txt）
    from ldap3 import MOCK_SYNC, OFFLINE_AD_2012_R2, Connection, Server

    server = Server("dc01.corp.local", get_info=OFFLINE_AD_2012_R2)
    seed = Connection(server, user="CN=seed", password="x", client_strategy=MOCK_SYNC)
    users = {
        "yamada@corp.local": ("CN=Yamada Taro,OU=Users,DC=corp,DC=local", "yamada", "山田 太郎", "pw1", ["CN=JS-Memo,OU=Groups,DC=corp,DC=local"]),
        "sato@corp.local": ("CN=Sato Hanako,OU=Users,DC=corp,DC=local", "sato", "佐藤 花子", "pw2",
                            ["CN=JS-Viewers,OU=Groups,DC=corp,DC=local", "CN=JS-Admins,OU=Groups,DC=corp,DC=local"]),
        "suzuki@corp.local": ("CN=Suzuki,OU=Users,DC=corp,DC=local", "suzuki", "鈴木", "pw3", ["CN=Other,OU=Groups,DC=corp,DC=local"]),
    }
    for upn, (dn, sam, display, pw, groups) in users.items():
        seed.strategy.add_entry(dn, {"objectClass": ["top", "person", "user"], "sAMAccountName": sam, "userPrincipalName": upn,
                                     "displayName": display, "userPassword": pw, "memberOf": groups})
    for cn, mail in (("JS-Admins", "js-admins@corp.local"), ("JS-Memo", "js-memo@corp.local"), ("JS-Viewers", "js-viewers@corp.local")):
        seed.strategy.add_entry(f"CN={cn},OU=Groups,DC=corp,DC=local", {"objectClass": ["top", "group"], "cn": cn, "sAMAccountName": cn, "mail": mail})

    def factory(bind_name, password):
        dn = users.get(bind_name.lower(), (bind_name,))[0]
        return Connection(server, user=dn, password=password, client_strategy=MOCK_SYNC)

    return factory


@pytest.fixture
def ad_settings():
    return Settings(
        _env_file=None,
        auth_mode="ldap",
        ldap_urls="ldaps://dc01.corp.local",
        ldap_upn_suffix="corp.local",
        ldap_base_dn="DC=corp,DC=local",
        ldap_nested_groups=False,  # 模擬サーバーは入れ子の照合（matching rule in chain）に対応していないため
        ldap_admin_groups="js-admins@corp.local",  # メーリングリストのアドレスで指定
        ldap_memo_editor_groups="JS-Memo",  # グループ名で指定
        ldap_viewer_groups="CN=JS-Viewers,OU=Groups,DC=corp,DC=local",  # DN で指定
    )


def test_ldap_roles_from_ad_groups(ad_settings):
    d = LdapDirectory(ad_settings, connection_factory=make_fake_ad())
    u = d.authenticate("yamada", "pw1")
    assert (u.username, u.display_name, u.role) == ("yamada", "山田 太郎", MEMO_EDITOR)
    # 複数のグループに入っていれば強い方
    assert d.authenticate("sato@corp.local", "pw2").role == ADMIN


def test_ldap_rejects_wrong_password_empty_password_and_no_group(ad_settings):
    d = LdapDirectory(ad_settings, connection_factory=make_fake_ad())
    with pytest.raises(AuthError, match="パスワードが違います"):
        d.authenticate("yamada", "wrong")
    with pytest.raises(AuthError):
        d.authenticate("yamada", "")  # 空パスワード（認証なしバインド）は必ず拒否
    with pytest.raises(AuthError, match="権限がありません"):
        d.authenticate("suzuki", "pw3")


# ---------------------------------------------------------------- 共有の管理者アカウント（AUTH_MODE=shared_admin）
@pytest.fixture
def shared_app(settings, mock_client):
    from app.auth.passwords import hash_password
    from app.main import create_app

    settings.auth_mode = None  # ADMIN_PASSWORD_HASH があれば自動で shared_admin になる
    settings.admin_username = "admin"
    settings.admin_password_hash = hash_password("correct horse battery", iterations=1000)
    app = create_app(settings, start_scheduler=False, client=mock_client)
    with TestClient(app, headers=CSRF_HEADERS) as c:
        yield c


def test_password_hash_roundtrip():
    from app.auth.passwords import hash_password, verify_password

    h = hash_password("パスワード123", iterations=1000)
    assert h.startswith("pbkdf2_sha256$") and "パスワード" not in h
    assert verify_password("パスワード123", h)
    assert not verify_password("違う", h)
    assert not verify_password("x", "壊れた値")


def test_everyone_can_view_and_edit_memo_without_login(shared_app):
    c = shared_app
    me = c.get("/api/auth/me").json()
    assert me["auth_mode"] == "shared_admin" and me["can"] == {"admin": False, "edit_memo": True}
    assert c.get("/", follow_redirects=False).status_code == 200  # ログイン画面に飛ばされない
    assert c.get("/api/targets").status_code == 200

    login(c, "admin", "correct horse battery")
    jenkins = c.post("/api/targets", json={"job_path": "buildset/core-pipeline"}).json()
    memo_item = c.post("/api/targets", json={"kind": "memo", "display_name": "計画"}).json()
    c.post("/api/auth/logout")

    m = c.post("/api/schedules", json={"target_id": memo_item["id"], "start_date": local_today().isoformat(), "label": "QA 期間"})
    assert m.status_code == 201
    j = c.post("/api/schedules", json={"target_id": jenkins["id"], "start_date": local_today().isoformat(), "cron_expr": "0 3 * * *"})
    assert j.status_code == 403 and "管理者ログイン" in j.json()["detail"]
    assert c.post(f"/api/targets/{jenkins['id']}/run-now", json={}).status_code == 403
    actor = c.get("/api/audit?type=schedule").json()[0]["actor"]
    assert actor.startswith("guest@")  # ログインしていない人の操作は接続元付きで記録


def test_admin_login_switch(shared_app):
    c = shared_app
    assert c.post("/api/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 401
    assert c.post("/api/auth/login", json={"username": "other", "password": "correct horse battery"}).status_code == 401
    me = login(c, "admin", "correct horse battery")
    assert me["can"]["admin"] is True and me["display_name"] == "管理者"
    assert c.post("/api/categories", json={"name": "新しいカテゴリ"}).status_code == 201
    c.post("/api/auth/logout")
    assert c.get("/api/auth/me").json()["can"]["admin"] is False
    assert c.post("/api/targets/sync").status_code == 403  # 管理者だけの操作
    assert c.post("/api/categories", json={"name": "別"}).status_code == 201  # カテゴリはみんな編集できる


def test_plain_admin_password_also_works(settings, mock_client):
    from app.main import create_app

    settings.admin_password = "plain-pass-for-test"
    app = create_app(settings, start_scheduler=False, client=mock_client)
    with TestClient(app, headers=CSRF_HEADERS) as c:
        assert c.get("/api/auth/me").json()["auth_mode"] == "shared_admin"
        login(c, "admin", "plain-pass-for-test")
