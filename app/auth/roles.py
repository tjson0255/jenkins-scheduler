"""権限（ロール）。AD グループで割り当て、強いものが優先される。"""

from __future__ import annotations

from dataclasses import dataclass

ADMIN = "admin"  # 1. フルコントロール
MEMO_EDITOR = "memo_editor"  # 2. 予定・メモのみ編集可能
VIEWER = "viewer"  # 3. 読み取り専用

ROLE_RANK = {VIEWER: 1, MEMO_EDITOR: 2, ADMIN: 3}
ROLE_LABEL = {ADMIN: "フルコントロール", MEMO_EDITOR: "予定・メモのみ編集", VIEWER: "読み取り専用"}


@dataclass(frozen=True)
class User:
    username: str
    display_name: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == ADMIN

    @property
    def can_edit_memo(self) -> bool:
        return ROLE_RANK.get(self.role, 0) >= ROLE_RANK[MEMO_EDITOR]


def strongest(roles) -> str | None:
    roles = [r for r in roles if r in ROLE_RANK]
    return max(roles, key=ROLE_RANK.__getitem__) if roles else None
