"""スキーマの正規化・ハッシュ（8.1）と差分分類（8.3）、展開・検証（8.4）。"""

from datetime import date, datetime

import pytest

from app.schema.diff import classify, job_status_issues
from app.schema.normalize import normalize, schema_hash
from app.schema.validate import build_context, resolve_params, validate_explicit

RAW = [
    {"name": "BRANCH", "type": "StringParameterDefinition", "description": "branch", "defaultParameterValue": {"value": "main"}},
    {"name": "MODE", "type": "ChoiceParameterDefinition", "description": "", "defaultParameterValue": {"value": "a"}, "choices": ["a", "b"]},
    {"name": "FLAG", "type": "BooleanParameterDefinition", "defaultParameterValue": {"value": True}},
]


def codes(issues):
    return sorted((i["level"], i["code"], i["param"]) for i in issues)


# ---------------------------------------------------------------- normalize / hash
def test_hash_is_stable_against_order_and_description():
    shuffled = [
        {"defaultParameterValue": {"value": True}, "type": "BooleanParameterDefinition", "name": "FLAG", "description": "x"},
        {"choices": ["a", "b"], "name": "MODE", "description": "別の説明", "type": "ChoiceParameterDefinition", "defaultParameterValue": {"value": "a"}},
        {"name": "BRANCH", "type": "StringParameterDefinition", "description": "変更", "defaultParameterValue": {"value": "main"}},
    ]
    assert schema_hash(normalize(RAW)) == schema_hash(normalize(shuffled))


def test_hash_changes_with_default():
    changed = [dict(RAW[0], defaultParameterValue={"value": "develop"}), RAW[1], RAW[2]]
    assert schema_hash(normalize(RAW)) != schema_hash(normalize(changed))


def test_normalize_keeps_only_relevant_fields():
    n = normalize(RAW)
    assert [d["name"] for d in n] == ["BRANCH", "FLAG", "MODE"]
    assert set(n[0]) == {"name", "type", "default", "choices"}


# ---------------------------------------------------------------- diff
def test_diff_added_and_removed():
    old = normalize(RAW)
    new = normalize(RAW[:2] + [{"name": "NEW", "type": "StringParameterDefinition", "defaultParameterValue": {"value": ""}}])
    issues = classify(old, new, {"FLAG": "false"})
    assert ("warning", "param_added", "NEW") in codes(issues)
    removed = [i for i in issues if i["code"] == "param_removed"]
    assert removed and removed[0]["level"] == "warning" and "送信対象から外します" in removed[0]["message"]


def test_diff_default_changed_is_info():
    old = normalize(RAW)
    new = normalize([dict(RAW[0], defaultParameterValue={"value": "develop"}), RAW[1], RAW[2]])
    assert codes(classify(old, new)) == [("info", "default_changed", "BRANCH")]


def test_diff_choice_change_with_invalid_override_is_error():
    old = normalize(RAW)
    new = normalize([RAW[0], dict(RAW[1], choices=["a", "c"]), RAW[2]])
    assert ("error", "choice_invalid", "MODE") in codes(classify(old, new, {"MODE": "b"}))
    # override が新しい選択肢にあれば info
    assert codes(classify(old, new, {"MODE": "c"})) == [("info", "choices_changed", "MODE")]


def test_diff_type_changed_is_error():
    old = normalize(RAW)
    new = normalize([dict(RAW[0], type="TextParameterDefinition"), RAW[1], RAW[2]])
    assert ("error", "type_changed", "BRANCH") in codes(classify(old, new))


def test_job_missing_or_not_buildable_is_error():
    assert job_status_issues(None, "ジョブが見つかりません")[0]["level"] == "error"
    assert job_status_issues({"buildable": False})[0]["code"] == "not_buildable"
    assert job_status_issues({"buildable": True}) == []


# ---------------------------------------------------------------- render / validate
def ctx():
    return build_context(
        label="v1.2.0",
        start_date=date(2027, 1, 1),
        end_date=None,
        job_path="buildset/core",
        scheduled_at_utc=datetime(2027, 1, 4, 18, 0),  # JST 2027-01-05 03:00
    )


def test_render_variables_in_tokyo_time():
    params, detail, issues = resolve_params(
        normalize(RAW), {"BRANCH": "release/{{schedule.label}}-{{run.date}}"}, ctx()
    )
    assert params["BRANCH"] == "release/v1.2.0-2027-01-05"
    assert params["FLAG"] == "true"  # boolean のデフォルトは文字列化
    assert detail["BRANCH"]["source"] == "override"
    assert issues == []


def test_validate_choice_bool_unknown_and_template_error():
    defs = normalize(RAW)
    _p, _d, issues = resolve_params(
        defs, {"MODE": "zzz", "FLAG": "yes", "GONE": "1", "BRANCH": "{{ nope.x }}"}, ctx()
    )
    c = codes(issues)
    assert ("error", "choice_invalid", "MODE") in c
    assert ("error", "type_mismatch", "FLAG") in c
    assert ("warning", "param_removed", "GONE") in c
    assert ("error", "template_error", "BRANCH") in c


def test_sandbox_blocks_unsafe_access():
    _p, _d, issues = resolve_params(normalize(RAW), {"BRANCH": "{{ ''.__class__.__mro__ }}"}, ctx())
    assert any(i["code"] == "template_error" for i in issues)


def test_pinned_values_used_when_no_override():
    params, detail, _ = resolve_params(normalize(RAW), {}, ctx(), pinned={"BRANCH": "frozen"})
    assert params["BRANCH"] == "frozen" and detail["BRANCH"]["source"] == "pinned"
    assert params["MODE"] == "a"


def test_validate_explicit_fills_defaults():
    params, issues = validate_explicit(normalize(RAW), {"MODE": "b"})
    assert params == {"BRANCH": "main", "FLAG": "true", "MODE": "b"}
    assert issues == []


def test_scheduled_at_variable_is_tokyo_without_offset():
    params, _d, _i = resolve_params(normalize(RAW), {"BRANCH": "{{run.scheduled_at}}"}, ctx())
    assert params["BRANCH"] == "2027-01-05 03:00"


def test_render_variables_only():
    from app.schema.validate import TemplateError, render

    c = ctx()
    assert render("plain text", c) == "plain text"
    assert render("{{schedule.label}}/{{ run.date }}", c) == f"{c['schedule']['label']}/{c['run']['date']}"
    for bad in ("{{ run }}", "{{ run.date | upper }}", "{% if 1 %}x{% endif %}", "{{ run.nope }}", "{{ 1 + 1 }}", "{{"):
        with pytest.raises(TemplateError):
            render(bad, c)
