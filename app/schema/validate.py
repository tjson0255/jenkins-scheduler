"""パラメータ値の展開（Jinja2 SandboxedEnvironment）と検証（仕様書 8.4）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from app.schema.diff import ERROR, WARNING, issue
from app.schema.normalize import default_as_str, kind_of
from app.timeutil import to_local

_env = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)


def build_context(
    *,
    label: str | None,
    start_date,
    end_date,
    job_path: str,
    scheduled_at_utc: datetime,
) -> dict[str, Any]:
    local = to_local(scheduled_at_utc)
    return {
        "schedule": {
            "label": label or "",
            "start_date": start_date.isoformat() if start_date else "",
            "end_date": end_date.isoformat() if end_date else "",
        },
        "run": {
            "scheduled_at": local.strftime("%Y-%m-%d %H:%M"),  # Asia/Tokyo（オフセットは付けない）
            "date": local.date().isoformat(),
        },
        "target": {"job_path": job_path},
    }


def render(template: str, context: dict[str, Any]) -> str:
    if "{{" not in template and "{%" not in template:
        return template
    return _env.from_string(template).render(**context)


def resolve_params(
    defs: list[dict[str, Any]],
    overrides: dict[str, str],
    context: dict[str, Any],
    pinned: dict[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """最新スキーマ defs に従って送信値を組み立てる。

    戻り値: (送信するパラメータ, 項目ごとの詳細 {value, source, template}, issues)
    """
    issues: list[dict[str, Any]] = []
    by_name = {d["name"]: d for d in defs}
    params: dict[str, str] = {}
    detail: dict[str, dict[str, Any]] = {}

    for name in overrides:
        if name not in by_name:
            issues.append(
                issue(WARNING, "param_removed", f"パラメータ {name} は Jenkins に存在しないため送信しません", name)
            )

    for d in defs:
        name = d["name"]
        if name in overrides:
            template, source = overrides[name], "override"
        elif pinned and name in pinned:
            template, source = pinned[name], "pinned"
        else:
            template, source = default_as_str(d.get("default")), "default"
        try:
            value = render(template, context)
        except TemplateError as exc:
            issues.append(issue(ERROR, "template_error", f"パラメータ {name} の展開に失敗しました: {exc}", name))
            value = template
        params[name] = value
        detail[name] = {"value": value, "source": source, "template": template}
        if source != "default":
            issues.extend(check_value(d, value))
    return params, detail, issues


def check_value(d: dict[str, Any], value: str) -> list[dict[str, Any]]:
    name = d["name"]
    kind = kind_of(d.get("type"))
    if kind == "boolean" and value.lower() not in ("true", "false"):
        return [issue(ERROR, "type_mismatch", f"パラメータ {name} は boolean ですが値が「{value}」です", name)]
    if kind == "choice":
        choices = d.get("choices") or []
        if value not in choices:
            return [issue(ERROR, "choice_invalid", f"パラメータ {name} の値「{value}」は選択肢にありません", name)]
    return []


def validate_explicit(defs: list[dict[str, Any]], requested: dict[str, str]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """即時実行・再実行用: 指定値をそのまま使い、無い項目はデフォルトで補完する。"""
    issues: list[dict[str, Any]] = []
    by_name = {d["name"]: d for d in defs}
    params: dict[str, str] = {}
    for name in requested:
        if name not in by_name:
            issues.append(issue(WARNING, "param_removed", f"パラメータ {name} は Jenkins に存在しないため送信しません", name))
    for d in defs:
        name = d["name"]
        if name in requested:
            value = str(requested[name])
            issues.extend(check_value(d, value))
        else:
            value = default_as_str(d.get("default"))
        params[name] = value
    return params, issues
