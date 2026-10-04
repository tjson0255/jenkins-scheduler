/* 実行結果: Jenkins のレーンで、いつ・どの件名（スケジューラ）を・どの内容で実行し、どうなったか。
 *  一覧: 件名で見分け、押すとパラメータ・メモなどの詳細を出す
 *  日付の表: 行=レーン、列=日付で、その日の結果の色でマスを塗る */
"use strict";

const ready = renderHeader("/history");

// 1日に複数の回があるときは、いちばん気にすべき結果の色にする（上ほど優先）
const SEVERITY = ["failure", "aborted", "holding", "missed", "unstable", "running", "queued", "success", "skipped", "cancelled", "scheduled"];
const EXECUTED = ["success", "unstable", "failure", "aborted"];
const LEGEND = [
  ["scheduled", "予定"], ["holding", "保留"], ["skipped", "スキップ"], ["missed", "見逃し"],
  ["running", "キュー/実行中"], ["success", "成功"], ["unstable", "不安定"], ["failure", "失敗"], ["aborted", "中断"],
];

const st = { view: "list", end: startOfToday(), days: 28, lane: "", q: "", doneOnly: false, categories: [], targets: [], runs: [] };
try {
  st.view = localStorage.getItem("historyView") === "grid" ? "grid" : "list";
} catch (_) {}

function startOfToday() {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d;
}

function dayList() {
  return Array.from({ length: st.days }, (_, i) => addDays(st.end, i - st.days + 1));
}

function worst(runs) {
  return runs.map((r) => r.status).sort((a, b) => SEVERITY.indexOf(a) - SEVERITY.indexOf(b))[0];
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
      ["状態", statusCell(r.status)],
      ["予定日時", fmtDateTime(r.scheduled_at, true)],
      r.triggered_at ? ["キック", fmtDateTime(r.triggered_at, true)] : null,
      r.finished_at ? ["終了", fmtDateTime(r.finished_at, true)] : null,
      r.build_url ? ["ビルド", el("a", { href: r.build_url, target: "_blank", rel: "noopener" }, `#${r.build_number}`)] : null,
      r.reason ? ["詳細", r.reason] : null,
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
      el("td", {}, statusCell(r.status)),
      el("td", {}, r.build_url ? el("a", { href: r.build_url, target: "_blank", rel: "noopener", onclick: (e) => e.stopPropagation() }, `#${r.build_number}`) : ""),
      el("td", { class: "small muted hist-reason" }, r.reason || "")));
  }
  box.replaceChildren(el("div", { class: "table-wrap" }, el("table", { class: "table hist-list" },
    el("thead", {}, el("tr", {}, ["時刻", "レーン", "件名", "状態", "ビルド", "詳細"].map((h) => el("th", {}, h)))),
    el("tbody", {}, rows))));
}

/* ---- 日付の表 ---- */
function openDayModal(t, day, runs) {
  const rows = runs.map((r) => el("tr", { class: "hist-row", onclick: () => openRunDetail(r) },
    el("td", { class: "mono" }, timeOf(r.scheduled_at)),
    el("td", { class: "hist-title" }, titleOf(r)),
    el("td", {}, statusCell(r.status)),
    el("td", {}, r.build_url ? el("a", { href: r.build_url, target: "_blank", rel: "noopener", onclick: (e) => e.stopPropagation() }, `#${r.build_number}`) : "")));
  openModal(`${t.display_name}　${fmtDate(day)}`, el("div", {},
    el("p", { class: "muted small" }, "行を押すと、パラメータやメモなどの詳細を出します。"),
    el("table", { class: "table small" },
      el("thead", {}, el("tr", {}, ["時刻", "件名", "状態", "ビルド"].map((h) => el("th", {}, h)))),
      el("tbody", {}, rows))), [el("button", { class: "btn", onclick: closeModal }, "閉じる")]);
}

function renderGrid() {
  const days = dayList();
  const today = ymd(new Date());
  const byCell = new Map();
  for (const r of filteredRuns()) {
    const k = `${r.target_id}|${ymd(new Date(r.scheduled_at))}`;
    if (!byCell.has(k)) byCell.set(k, []);
    byCell.get(k).push(r);
  }
  for (const list of byCell.values()) list.sort((a, b) => a.scheduled_at.localeCompare(b.scheduled_at));
  const shown = lanes().filter((t) => !st.lane || String(t.id) === st.lane);
  const dayCls = (d, base) => [base, d.getDay() === 0 ? "sun" : d.getDay() === 6 ? "sat" : "", ymd(d) === today ? "today" : "", d.getDate() === 1 ? "month-start" : ""].filter(Boolean).join(" ");

  const head = el("tr", {},
    el("th", { class: "hist-lane" }, "レーン"),
    days.map((d) => el("th", { class: dayCls(d, "hist-day"), title: fmtDate(ymd(d)) }, el("div", {}, `${d.getDate()}`), el("div", { class: "hist-wd" }, WD[d.getDay()]))),
    el("th", { class: "hist-sum" }, "成功 / 実行"));
  const body = shown.map((t) => {
    let ok = 0;
    let done = 0;
    const cells = days.map((d) => {
      const k = ymd(d);
      const runs = byCell.get(`${t.id}|${k}`) || [];
      for (const r of runs) {
        if (EXECUTED.includes(r.status)) done++;
        if (r.status === "success") ok++;
      }
      if (!runs.length) return el("td", { class: dayCls(d, "hist-cell") });
      const tip = [`${t.display_name} ${fmtDate(k)}`, ...runs.map((r) => `${timeOf(r.scheduled_at)} ${titleOf(r)} ${RUN_STATUS_LABEL[r.status] || r.status}`)].join("\n");
      return el("td", { class: dayCls(d, "hist-cell") },
        el("button", { type: "button", class: `hist-mark rs-${worst(runs)}`, title: tip + "\n\n押すと詳細", onclick: () => openDayModal(t, k, runs) },
          runs.length > 1 ? String(runs.length) : ""));
    });
    return el("tr", {},
      el("th", { class: "hist-lane", scope: "row" }, el("span", { class: "swatch inline", style: `background:${t.color || "#8a94a6"}` }), t.display_name),
      cells,
      el("td", { class: `hist-sum${done && ok < done ? " has-fail" : ""}` }, done ? `${ok} / ${done}` : "—"));
  });
  const box = document.getElementById("hist-body");
  const prev = box.querySelector(".hist-wrap");
  const keep = prev && st.keepScroll ? prev.scrollLeft : null;
  box.replaceChildren(shown.length
    ? el("div", { class: "table-wrap hist-wrap" }, el("table", { class: "hist-table" }, el("thead", {}, head), el("tbody", {}, body)))
    : el("p", { class: "muted" }, "Jenkins のレーンがありません。"));
  // 新しい日付が右端なので、最初は右端（直近）を見せる。自動更新のときは見ていた位置のまま
  const wrap = box.querySelector(".hist-wrap");
  if (wrap) wrap.scrollLeft = keep ?? wrap.scrollWidth;
}

/* ---- 共通 ---- */
function render() {
  const days = dayList();
  document.getElementById("hist-range").textContent = `${fmtDate(ymd(days[0]))} 〜 ${fmtDate(ymd(days[days.length - 1]))}`;
  document.querySelectorAll("[data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === st.view));
  st.view === "grid" ? renderGrid() : renderList();
  st.keepScroll = false;
}

function renderControls() {
  const lane = document.getElementById("hist-lane");
  lane.replaceChildren(el("option", { value: "" }, "すべてのレーン"), lanes().map((t) => el("option", { value: t.id, selected: String(t.id) === st.lane }, t.display_name)));
  // 問題がこのツール側にあるか、Jenkins 側にあるかで分けて並べる
  const item = ([k, label]) => el("span", { class: "hist-legend-item", title: runStatusHint(k) }, el("span", { class: `hist-mark small rs-${k}` }), label);
  document.getElementById("hist-legend").replaceChildren(
    el("span", { class: "legend-side", title: SIDE_HINT.tool }, "ツール側"),
    ...LEGEND.filter(([k]) => RUN_SIDE[k] === "tool").map(item),
    el("span", { class: "legend-sep" }),
    el("span", { class: "legend-side", title: SIDE_HINT.jenkins }, "Jenkins 側"),
    ...LEGEND.filter(([k]) => RUN_SIDE[k] === "jenkins").map(item));
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

document.querySelectorAll("[data-view]").forEach((b) => {
  b.onclick = () => {
    st.view = b.dataset.view;
    try {
      localStorage.setItem("historyView", st.view);
    } catch (_) {}
    render();
  };
});
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
  st.keepScroll = true;
  load();
}, 60000);
