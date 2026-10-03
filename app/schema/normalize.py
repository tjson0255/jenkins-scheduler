"""パラメータ定義の正規化とハッシュ（仕様書 8.1）。"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def normalize(raw_defs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`name, type, default, choices` だけ残し、name でソートする（description は除外）。"""
    out = []
    for d in raw_defs:
        dpv = d.get("defaultParameterValue")
        default = dpv.get("value") if isinstance(dpv, dict) else None
        choices = d.get("choices")
        out.append(
            {
                "name": d.get("name"),
                "type": d.get("type"),
                "default": default,
                "choices": list(choices) if choices is not None else None,
            }
        )
    out.sort(key=lambda x: x["name"] or "")
    return out


def canonical_json(defs: list[dict[str, Any]]) -> str:
    return json.dumps(defs, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def schema_hash(normalized: list[dict[str, Any]]) -> str:
    return hashlib.sha256(canonical_json(normalized).encode("utf-8")).hexdigest()


def descriptions(raw_defs: list[dict[str, Any]]) -> dict[str, str]:
    return {d.get("name"): d.get("description") or "" for d in raw_defs}


def kind_of(type_name: str | None) -> str:
    """Jenkins の型名を UI 用の種類に寄せる。"""
    t = (type_name or "").lower()
    if t.startswith("boolean"):
        return "boolean"
    if t.startswith("choice"):
        return "choice"
    if t.startswith("text"):
        return "text"
    if t.startswith("string"):
        return "string"
    if t.startswith("password"):
        return "password"
    return "other"


def default_as_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
