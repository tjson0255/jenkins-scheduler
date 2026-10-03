"""初期データ投入（仕様書 5.2 / 5.3）。seed.toml は job_path で突合して冪等に反映する。

TOML は Python の標準ライブラリ（tomllib）で読む。
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.models import Category, Target

log = logging.getLogger(__name__)

DEFAULT_CATEGORIES = ["ビルドセット", "リリース関連", "その他"]


def ensure_default_categories(db: Session) -> None:
    if db.scalar(select(func.count()).select_from(Category)):
        return
    for i, name in enumerate(DEFAULT_CATEGORIES):
        db.add(Category(name=name, sort_order=i))
    db.commit()


def load_seed(db: Session, path: Path, default_overlap_policy: str = "skip") -> dict[str, int]:
    path = Path(path)
    if path.suffix.lower() in (".yaml", ".yml"):
        raise ValueError(
            f"初期データは TOML で書いてください（{path.name} → seed.toml）。書き方は seed.toml.example を参照してください"
        )
    # TOML の仕様で UTF-8 として読む（Windows の既定の文字コードに左右されない）
    data = tomllib.loads(path.read_text(encoding="utf-8-sig")) or {}
    created = {"categories": 0, "targets": 0, "updated_targets": 0}

    cats = {c.name: c for c in db.scalars(select(Category))}
    next_order = max((c.sort_order for c in cats.values()), default=-1) + 1
    for c in data.get("categories") or []:
        name = c["name"]
        if name not in cats:
            cats[name] = Category(name=name, sort_order=next_order)
            next_order += 1
            db.add(cats[name])
            created["categories"] += 1
    db.flush()

    for i, t in enumerate(data.get("targets") or []):
        cat_name = t.get("category") or "その他"
        if cat_name not in cats:
            cats[cat_name] = Category(name=cat_name, sort_order=next_order)
            next_order += 1
            db.add(cats[cat_name])
            db.flush()
        existing = db.scalars(select(Target).where(Target.job_path == t["job_path"])).first()
        fields = dict(
            display_name=t.get("display_name") or t["job_path"].split("/")[-1],
            category_id=cats[cat_name].id,
            pinned=bool(t.get("pinned", False)),
            color=t.get("color"),
            overlap_policy=t.get("overlap_policy", default_overlap_policy),
            note=t.get("note"),
            sort_order=t.get("sort_order", i),
        )
        if existing:
            for k, v in fields.items():
                setattr(existing, k, v)
            created["updated_targets"] += 1
        else:
            db.add(Target(job_path=t["job_path"], enabled=True, **fields))
            created["targets"] += 1
    audit.record(db, audit.SYSTEM, "seed.load", "system", None, {"file": str(path), **created})
    db.commit()
    log.info("seed を読み込みました: %s", created)
    return created
