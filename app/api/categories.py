from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.api.deps import get_actor, get_db, not_found
from app.api.serializers import category_out
from app.models import Category

router = APIRouter(prefix="/api/categories", tags=["categories"])


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    sort_order: int | None = None


class CategoryPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    sort_order: int | None = None


@router.get("")
def list_categories(db: Session = Depends(get_db)):
    return [category_out(c) for c in db.scalars(select(Category).order_by(Category.sort_order, Category.id))]


@router.post("", status_code=201)
def create_category(body: CategoryIn, db: Session = Depends(get_db), actor: str = Depends(get_actor)):
    if db.scalars(select(Category).where(Category.name == body.name)).first():
        raise HTTPException(409, "同じ名前のカテゴリがあります")
    order = body.sort_order
    if order is None:
        order = (db.scalar(select(func.max(Category.sort_order))) or 0) + 1
    c = Category(name=body.name, sort_order=order)
    db.add(c)
    db.flush()
    audit.record(db, actor, "category.create", "category", c.id, {"name": c.name})
    db.commit()
    return category_out(c)


@router.patch("/{cid}")
def update_category(cid: int, body: CategoryPatch, db: Session = Depends(get_db), actor: str = Depends(get_actor)):
    c = db.get(Category, cid)
    if not c:
        raise not_found("カテゴリ")
    changes = body.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] != c.name:
        if db.scalars(select(Category).where(Category.name == changes["name"])).first():
            raise HTTPException(409, "同じ名前のカテゴリがあります")
    for k, v in changes.items():
        if v is not None:
            setattr(c, k, v)
    audit.record(db, actor, "category.update", "category", c.id, changes)
    db.commit()
    return category_out(c)


@router.delete("/{cid}", status_code=204)
def delete_category(cid: int, db: Session = Depends(get_db), actor: str = Depends(get_actor)):
    c = db.get(Category, cid)
    if not c:
        raise not_found("カテゴリ")
    if c.targets:
        raise HTTPException(409, "所属するレーンがあるため削除できません")
    audit.record(db, actor, "category.delete", "category", c.id, {"name": c.name})
    db.delete(c)
    db.commit()
