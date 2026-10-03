"""スキーマ差分の分類（仕様書 8.3）。

issue は dict: {"level": "error"|"warning"|"info", "code": str, "param": str|None, "message": str}
"""

from __future__ import annotations

from typing import Any

ERROR, WARNING, INFO = "error", "warning", "info"


def issue(level: str, code: str, message: str, param: str | None = None) -> dict[str, Any]:
    return {"level": level, "code": code, "param": param, "message": message}


def classify(
    old_defs: list[dict[str, Any]] | None,
    new_defs: list[dict[str, Any]],
    override_values: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """old（基準）→ new（最新）の差分を分類する。override_values は展開済みの上書き値。"""
    if old_defs is None:
        return []
    overrides = override_values or {}
    old = {d["name"]: d for d in old_defs}
    new = {d["name"]: d for d in new_defs}
    issues: list[dict[str, Any]] = []

    for name in sorted(new.keys() - old.keys()):
        issues.append(
            issue(WARNING, "param_added", f"パラメータ {name} が追加されました（Jenkins のデフォルト値で補完。要確認）", name)
        )
    for name in sorted(old.keys() - new.keys()):
        extra = "。上書き値は送信対象から外します" if name in overrides else ""
        issues.append(issue(WARNING, "param_removed", f"パラメータ {name} が削除されました{extra}", name))

    for name in sorted(old.keys() & new.keys()):
        o, n = old[name], new[name]
        if o.get("type") != n.get("type"):
            issues.append(
                issue(ERROR, "type_changed", f"パラメータ {name} の型が変わりました（{o.get('type')} → {n.get('type')}）", name)
            )
            continue
        if o.get("choices") != n.get("choices"):
            value = overrides.get(name)
            choices = n.get("choices") or []
            if value is not None and value not in choices:
                issues.append(
                    issue(ERROR, "choice_invalid", f"パラメータ {name} の選択肢が変わり、上書き値「{value}」が選択肢にありません", name)
                )
            else:
                issues.append(issue(INFO, "choices_changed", f"パラメータ {name} の選択肢が変わりました", name))
        if o.get("default") != n.get("default"):
            issues.append(
                issue(INFO, "default_changed", f"パラメータ {name} のデフォルト値が変わりました（{o.get('default')!r} → {n.get('default')!r}）", name)
            )
    return issues


def job_status_issues(job_info: dict[str, Any] | None, error: str | None = None) -> list[dict[str, Any]]:
    if error:
        return [issue(ERROR, "job_missing", error)]
    if job_info is not None and job_info.get("buildable") is False:
        return [issue(ERROR, "not_buildable", "ジョブがビルド不可（buildable=false）です")]
    return []


def max_level(issues: list[dict[str, Any]]) -> str | None:
    levels = {i["level"] for i in issues}
    for lv in (ERROR, WARNING, INFO):
        if lv in levels:
            return lv
    return None


def dedupe(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    out = []
    for i in issues:
        key = (i["code"], i.get("param"))
        if key in seen:
            continue
        seen.add(key)
        out.append(i)
    return out
