/* スケジューラの基本情報フォーム（作成ダイアログと詳細パネルで共用） */
"use strict";

const CRON_PRESETS = [
  ["daily", "毎日 HH:MM"],
  ["weekdays", "平日 HH:MM"],
  ["weekly", "毎週 曜日 HH:MM"],
  ["hourly", "N時間ごと"],
  ["custom", "カスタム（cron 直接入力）"],
];

function parseCronPreset(expr) {
  const e = (expr || "").trim();
  let m;
  if ((m = e.match(/^(\d+) (\d+) \* \* \*$/))) return { preset: "daily", time: `${pad(m[2])}:${pad(m[1])}` };
  if ((m = e.match(/^(\d+) (\d+) \* \* 1-5$/))) return { preset: "weekdays", time: `${pad(m[2])}:${pad(m[1])}` };
  if ((m = e.match(/^(\d+) (\d+) \* \* ([0-6])$/))) return { preset: "weekly", time: `${pad(m[2])}:${pad(m[1])}`, dow: m[3] };
  if ((m = e.match(/^(\d+) \*\/(\d+) \* \* \*$/))) return { preset: "hourly", minute: m[1], hours: m[2] };
  if (!e) return { preset: "daily", time: "03:00" };
  return { preset: "custom", custom: e };
}

function buildCron(v) {
  const [h, mi] = (v.time || "00:00").split(":").map(Number);
  switch (v.preset) {
    case "daily":
      return `${mi} ${h} * * *`;
    case "weekdays":
      return `${mi} ${h} * * 1-5`;
    case "weekly":
      return `${mi} ${h} * * ${v.dow || 1}`;
    case "hourly":
      return `${Number(v.minute || 0)} */${Math.max(1, Number(v.hours || 1))} * * *`;
    default:
      return (v.custom || "").trim();
  }
}

/**
 * @param {object} s 初期値（schedule の API 形式。新規作成時は一部のみ）
 * @param {object} opts {targets: [...]} を渡すとレーン選択欄を出す
 */
function scheduleForm(s, opts) {
  opts = opts || {};
  const cp = parseCronPreset(s.cron_expr);
  const f = {};
  const field = (label, input, extra) => el("label", { class: "field" + (extra ? " " + extra : "") }, el("span", {}, label), input);

  if (opts.targets) {
    f.target = el("select", {}, opts.targets.map((t) => el("option", { value: t.id, selected: t.id === s.target_id }, `${t.display_name}（${t.job_path}）`)));
  }
  f.label = el("input", { type: "text", value: s.label || "" });
  f.start = el("input", { type: "date", value: s.start_date || ymd(new Date()), required: true });
  f.end = el("input", { type: "date", value: s.end_date || "" });
  f.infinite = el("input", { type: "checkbox", checked: !s.end_date });
  f.mode = el("select", {},
    el("option", { value: "cron", selected: (s.mode || "cron") === "cron" }, "cron（定期）"),
    el("option", { value: "once", selected: s.mode === "once" }, "1回"));
  f.preset = el("select", {}, CRON_PRESETS.map(([v, l]) => el("option", { value: v, selected: v === cp.preset }, l)));
  f.time = el("input", { type: "time", value: cp.time || "03:00" });
  f.dow = el("select", {}, WD.map((w, i) => el("option", { value: i, selected: String(i) === String(cp.dow ?? 1) }, w + "曜")));
  f.hours = el("input", { type: "number", min: 1, max: 23, value: cp.hours || 6, style: { width: "5em" } });
  f.minute = el("input", { type: "number", min: 0, max: 59, value: cp.minute || 0, style: { width: "5em" } });
  f.custom = el("input", { type: "text", value: cp.custom || s.cron_expr || "", placeholder: "分 時 日 月 曜日（例: 30 2 * * 1-5）", class: "mono" });
  f.onceAt = el("input", { type: "datetime-local", value: toLocalInput(s.once_at) || `${s.start_date || ymd(new Date())}T03:00` });
  f.missed = el("select", {},
    el("option", { value: "run_late", selected: (s.missed_policy || "run_late") === "run_late" }, "予定時刻を過ぎても、猶予の分数以内ならキックする"),
    el("option", { value: "skip", selected: s.missed_policy === "skip" }, "予定時刻を過ぎたらキックしない（見逃し）"));
  f.grace = el("input", { type: "number", min: 0, max: 1440, value: s.grace_minutes ?? 10, style: { width: "6em" } });
  f.exclusive = el("input", { type: "checkbox", checked: !!s.exclusive });
  f.note = el("textarea", { rows: 2 }, s.note || "");

  const cronOut = el("div", { class: "cron-preview" });
  const cronExprView = el("code", { class: "mono" });
  const presetBox = el("div", { class: "row wrap" });

  function renderPreset() {
    const p = f.preset.value;
    presetBox.replaceChildren(
      ...[
      f.preset,
      p === "weekly" ? f.dow : null,
      ["daily", "weekdays", "weekly"].includes(p) ? f.time : null,
      p === "hourly" ? el("span", { class: "row" }, f.hours, "時間ごと ", f.minute, "分") : null,
      p === "custom" ? f.custom : null,
      ].filter(Boolean)
    );
  }

  const updatePreview = debounce(async () => {
    if (f.mode.value !== "cron") return;
    const expr = cronValue();
    cronExprView.textContent = expr;
    if (!expr) {
      cronOut.replaceChildren();
      return;
    }
    try {
      const r = await api("POST", "/api/cron/preview", { cron_expr: expr, count: 5, start_date: f.start.value || null });
      cronOut.replaceChildren(el("div", { class: "muted" }, `次回以降の予定（${r.summary}）`), el("ol", {}, r.times_local.map((t) => el("li", {}, t))));
    } catch (e) {
      cronOut.replaceChildren(el("div", { class: "error-text" }, e.message));
    }
  }, 250);

  function cronValue() {
    return buildCron({
      preset: f.preset.value,
      time: f.time.value,
      dow: f.dow.value,
      hours: f.hours.value,
      minute: f.minute.value,
      custom: f.custom.value,
    });
  }

  const cronSection = el("div", { class: "subsection" }, field("実行規則", presetBox), el("div", { class: "muted small" }, "cron: ", cronExprView, "（Asia/Tokyo で評価。Jenkins の H 記法は使えません）"), cronOut);
  const onceSection = el("div", { class: "subsection" }, field("実行日時", f.onceAt));

  function renderMode() {
    cronSection.hidden = f.mode.value !== "cron";
    onceSection.hidden = f.mode.value !== "once";
    updatePreview();
  }
  function renderEnd() {
    f.end.disabled = f.infinite.checked;
    if (!f.infinite.checked && !f.end.value) f.end.value = f.start.value;
  }

  f.preset.addEventListener("change", () => {
    if (f.preset.value === "custom" && !f.custom.value) f.custom.value = cronValue();
    renderPreset();
    updatePreview();
  });
  for (const i of [f.time, f.dow, f.hours, f.minute, f.custom]) i.addEventListener("input", updatePreview);
  f.mode.addEventListener("change", renderMode);
  f.start.addEventListener("change", updatePreview);
  f.infinite.addEventListener("change", renderEnd);

  const root = el(
    "div",
    { class: "form" },
    f.target ? field("レーン", f.target) : null,
    field("件名", f.label),
    el("div", { class: "row period-row" }, field("開始日", f.start), field("終了日", f.end), el("label", { class: "check" }, f.infinite, " 無期限"),
      el("label", { class: "check", title: "臨時のスケジューラ用。有効にしている間、開始日〜終了日に入る同じレーンの他のスケジューラの回はキックしません（スキップ）。一時停止・削除すれば元に戻ります。終了日が必要です" },
        f.exclusive, " 他スケジューラ停止")),
    field("モード", f.mode),
    cronSection,
    onceSection,
    el("div", { class: "row wrap" }, field("予定時刻にキックできなかったとき", f.missed), field("猶予（分）", f.grace)),
    field("メモ", f.note)
  );
  renderPreset();
  renderEnd();
  renderMode();

  return {
    root,
    value() {
      const v = {
        label: f.label.value.trim() || null,
        start_date: f.start.value,
        end_date: f.infinite.checked ? null : f.end.value || null,
        mode: f.mode.value,
        cron_expr: f.mode.value === "cron" ? cronValue() : null,
        once_at: f.mode.value === "once" ? f.onceAt.value : null,
        missed_policy: f.missed.value,
        grace_minutes: Number(f.grace.value || 0),
        exclusive: f.exclusive.checked,
        note: f.note.value.trim() || null,
      };
      if (f.target) v.target_id = Number(f.target.value);
      return v;
    },
  };
}

/** テキストのレーンに書く予定のフォーム（件名・期間・詳細だけ） */
function memoForm(s, opts) {
  opts = opts || {};
  const field = (label, input) => el("label", { class: "field" }, el("span", {}, label), input);
  const f = {};
  if (opts.targets) {
    f.target = el("select", {}, opts.targets.map((t) => el("option", { value: t.id, selected: t.id === s.target_id }, t.display_name)));
  }
  f.label = el("input", { type: "text", value: s.label || "" });
  f.start = el("input", { type: "date", value: s.start_date || ymd(new Date()), required: true });
  f.end = el("input", { type: "date", value: s.end_date || "" });
  f.infinite = el("input", { type: "checkbox", checked: !s.end_date && !!s.id });
  f.note = el("textarea", { rows: 7 }, s.note || "");
  const renderEnd = () => {
    f.end.disabled = f.infinite.checked;
    if (!f.infinite.checked && !f.end.value) f.end.value = f.start.value;
  };
  f.infinite.addEventListener("change", renderEnd);
  renderEnd();
  const root = el("div", { class: "form" },
    f.target ? field("レーン", f.target) : null,
    field("件名", f.label),
    el("div", { class: "row wrap" }, field("開始日", f.start), field("終了日", f.end), el("label", { class: "check" }, f.infinite, " 無期限")),
    field("詳細", f.note));
  return {
    root,
    value() {
      const v = {
        label: f.label.value.trim() || null,
        start_date: f.start.value,
        end_date: f.infinite.checked ? null : f.end.value || null,
        note: f.note.value.trim() || null,
      };
      if (f.target) v.target_id = Number(f.target.value);
      return v;
    },
  };
}
