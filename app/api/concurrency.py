"""同時編集の検出（楽観的ロック）。

保存のリクエストに「読み込んだ時点の revision」を付けてもらい、
`UPDATE ... SET revision = revision + 1 WHERE id = ? AND revision = ?` が1行も更新しなければ、
他の人（または別の画面）が先に変更したと判断して 409 を返す。
この UPDATE で SQLite の書き込みロックを先に取るので、同時に来た2つ目の保存は1つ目の確定を待ってから失敗する。
revision を付けない呼び出し（スクリプト等）は照合せずに番号だけ進める。
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.orm import Session

CONFLICT_MESSAGE = "他の人（または別の画面）が先に更新しました。最新の内容を読み込み直してから、もう一度変更してください"


def bump_revision(db: Session, model, obj_id: int, expected: int | None) -> None:
    q = update(model).where(model.id == obj_id)
    if expected is not None:
        q = q.where(model.revision == expected)
    res = db.execute(q.values(revision=model.revision + 1).execution_options(synchronize_session=False))
    if res.rowcount != 1:
        db.rollback()
        raise HTTPException(409, {"code": "conflict", "message": CONFLICT_MESSAGE})
    obj = db.get(model, obj_id)
    if obj is not None:
        db.refresh(obj, ["revision"])
