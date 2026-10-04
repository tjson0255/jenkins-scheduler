/* 実行結果: Jenkins のレーンで、いつ・どの件名（スケジューラ）を・どの内容で実行し、どうなったか。
 *  新しい順に日付ごとに並べ、件名で見分ける。行を押すと、送ったパラメータ・メモなどの詳細を出す */
"use strict";

const ready = renderHeader("/history");

const EXECUTED = ["success", "unstable", "failure", "aborted"];

const st = { end: startOfToday(), days: 28, lane: "", q: "", doneOnly: false, categories: [], targets: [], runs: [] };

function startOfToday() {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d;
}

function dayList() {
  return Array.from({ length: st.days }, (_, i) => addDays(st.end, i - st.days + 1));
}

function timeOf(iso) {
  const d = new Date(iso);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function titleOf(r) {
  return r.schedule_title || (r.retry_of_id ? "再実行" : "即時実行");
}

/** Jenkins のレーン（カテゴリ順・並び順） */
function lanes() {
  const out = [];
  for (const c of st.categories) {
    out.push(...st.targets.filter((t) => t.category_id === c.id && t.kind !== "memo").sort((a, b) => a.sort_order - b.sort_order || a.id - b.id));
  }
  return out;
}

/** 絞り込み（レーン・件名・実行済みだけ）に合う回 */
function filteredRuns() {
  const q = st.q.trim().toLowerCase();
  const laneIds = new Set(lanes().map((t) => t.id));
  return st.runs.filter((r) =>
    laneIds.has(r.target_id) &&
    (!st.lane || String(r.target_id) === st.lane) &&
    (!q || titleOf(r).toLowerCase().includes(q)) &&
    (!st.doneOnly || EXECUTED.includes(r.status)));
}

function statusCell(status) {
  return el("span", { title: runStatusHint(status) }, el("span", { class: `legend-dot rs-${status}` }), " ", RUN_STATUS_LABEL[status] || status);
}

/* ---- 詳細（件名・パラメータ・メモなど） ---- */
function openRunDetail(r) {
  const t = st.targets.find((x) => x.id === r.target_id);
  const dl = (rows) => el("dl", { class: "run-detail" }, rows.filter(Boolean).flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)]));
  const params = r.params
    ? el("table", { class: "table small" }, el("tbody", {}, Object.entries(r.params).map(([k, v]) => el("tr", {},
        el("th", {}, k, r.override_params && k in r.override_params ? el("span", { class: "replace-tag inline" }, "この回だけ変更") : null),
        el("td", { class: "mono" }, v === "" ? el("span", { class: "muted" }, "（空）") : String(v))))))
    : el("p", { class: "muted small" }, ["scheduled", "holding"].includes(r.status)
      ? "まだキックしていません。送るパラメータはキックするときに決まります。"
      : "キックしていないので、送ったパラメータはありません。");
  const body = el("div", {},
    dl([
      ["レーン", t ? `${t.display_name}（${t.job_path}）` : r.target_name || ""],
      ["件名", el("span", {}, el("b", {}, titleOf(r)), r.schedule_deleted ? el("span", { class: "replace-tag inline", title: "スケジューラは削除済みです。実行した時点の件名・パラメータ・メモを残しています" }, "スケジューラ削除済み") : null)],
      ["状態", statusCell(runShownStatus(r))],
      ["予定日時", fmtDateTime(r.scheduled_at, true)],
      r.triggered_at ? ["キック", fmtDateTime(r.triggered_at, true)] : null,
      r.finished_at ? ["終了", fmtDateTime(r.finished_at, true)] : null,
      r.build_url ? ["ビルド", el("a", { href: r.build_url, target: "_blank", rel: "noopener" }, `#${r.build_number}`)] : null,
      runShownReason(r) ? ["詳細", runShownReason(r)] : null,
    ]),
    el("h3", {}, "メモ"),
    r.schedule_note ? el("div", { class: "day-memo-note" }, r.schedule_note) : el("p", { class: "muted small" }, "メモはありません。"),
    el("h3", {}, "送ったパラメータ"),
    params);
  const buttons = [el("button", { class: "btn", onclick: closeModal }, "閉じる")];
  if (r.schedule_id) {
    buttons.unshift(el("a", { class: "btn", href: `/?date=${ymd(new Date(r.scheduled_at))}#schedule=${r.schedule_id}&run=${r.id}` }, "スケジューラを開く"));
  }
  openModal(`${titleOf(r)}　${fmtDateTime(r.scheduled_at, true)}`, body, buttons);
}

/* ---- 一覧 ---- */
function renderList() {
  const runs = filteredRuns().slice().sort((a, b) => b.scheduled_at.localeCompare(a.scheduled_at));
  const box = document.getElementById("hist-body");
  if (!runs.length) {
    box.replaceChildren(el("p", { class: "muted" }, "この期間に、条件に合う回はありません。"));
    return;
  }
  const rows = [];
  let prevDay = null;
  for (const r of runs) {
    const day = ymd(new Date(r.scheduled_at));
    if (day !== prevDay) {
      rows.push(el("tr", { class: "hist-day-row" }, el("td", { colspan: 6 }, fmtDate(day))));
      prevDay = day;
    }
    const t = st.targets.find((x) => x.id === r.target_id);
    rows.push(el("tr", { class: "hist-row", tabindex: "0", onclick: () => openRunDetail(r), onkeydown: (e) => e.key === "Enter" && openRunDetail(r) },
      el("td", { class: "mono" }, timeOf(r.scheduled_at)),
      el("td", {}, el("span", { class: "swatch inline", style: `background:${(t && t.color) || "#8a94a6"}` }), t ? t.display_name : r.target_name || ""),
      el("td", { class: "hist-title" }, titleOf(r),
        r.schedule_deleted ? el("span", { class: "replace-tag inline", title: "スケジューラは削除済み（履歴として残しています）" }, "削除済み") : null,
        r.replaces_run_id ? el("span", { class: "replace-tag inline" }, "この回だけ変更") : null),
      el("td", {}, statusCell(runShownStatus(r))),
      el("td", {}, r.build_url ? el("a", { href: r.build_url, target: "_blank", rel: "noopener", onclick: (e) => e.stopPropagation() }, `#${r.build_number}`) : ""),
      el("td", { class: "small muted hist-reason" }, runShownReason(r))));
  }
  box.replaceChildren(el("div", { class: "table-wrap" }, el("table", { class: "table hist-list" },
    el("thead", {}, el("tr", {}, ["時刻", "レーン", "件名", "状態", "ビルド", "詳細"].map((h) => el("th", {}, h)))),
    el("tbody", {}, rows))));
}

/* ---- 共通 ---- */
function render() {
  const days = dayList();
  // 期間の欄と長さの選択欄を、今の期間に合わせる（2週・4週・8週に当てはまらなければ「指定」）
  document.getElementById("hist-from").value = ymd(days[0]);
  document.getElementById("hist-to").value = ymd(days[days.length - 1]);
  const preset = document.getElementById("hist-days");
  preset.value = [...preset.options].some((o) => o.value === String(st.days)) ? String(st.days) : "";
  renderList();
}

function renderControls() {
  const lane = document.getElementById("hist-lane");
  lane.replaceChildren(el("option", { value: "" }, "すべてのレーン"), lanes().map((t) => el("option", { value: t.id, selected: String(t.id) === st.lane }, t.display_name)));
}

async function load() {
  const days = dayList();
  try {
    const [categories, targets, runs] = await Promise.all([
      api("GET", "/api/categories"),
      api("GET", "/api/targets"),
      api("GET", `/api/runs?from=${ymd(days[0])}&to=${ymd(days[days.length - 1])}&limit=10000`),
    ]);
    Object.assign(st, { categories, targets, runs });
    renderControls();
    render();
  } catch (e) {
    toast(e.message, "error");
  }
}

// 期間をカレンダーで選ぶ（開始日〜終了日。最大1年）
const MAX_DAYS = 366;
function setPeriod(from, to) {
  if (to < from) [from, to] = [to, from];
  const days = Math.round((to - from) / 86400000) + 1;
  st.end = to;
  st.days = Math.min(days, MAX_DAYS);
  load();
}
document.getElementById("hist-from").onchange = (e) => e.target.value && setPeriod(parseYmd(e.target.value), st.end);
document.getElementById("hist-to").onchange = (e) => e.target.value && setPeriod(addDays(st.end, -st.days + 1), parseYmd(e.target.value));
document.getElementById("hist-prev").onclick = () => {
  st.end = addDays(st.end, -st.days);
  load();
};
document.getElementById("hist-next").onclick = () => {
  st.end = addDays(st.end, st.days);
  load();
};
document.getElementById("hist-today").onclick = () => {
  st.end = startOfToday();
  load();
};
document.getElementById("hist-days").onchange = (e) => {
  if (!e.target.value) return;
  st.days = Number(e.target.value);
  load();
};
document.getElementById("hist-lane").onchange = (e) => {
  st.lane = e.target.value;
  render();
};
document.getElementById("hist-q").addEventListener("input", debounce((e) => {
  st.q = e.target.value;
  render();
}, 200));
document.getElementById("hist-done").onchange = (e) => {
  st.doneOnly = e.target.checked;
  render();
};
ready.then(load);
setInterval(() => {
  if (document.hidden || document.getElementById("modal")) return; // 詳細を開いている間は読み直さない
  load();
}, 60000);
