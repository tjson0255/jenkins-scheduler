/* 1日の予定（日付を選んで、その日の Jenkins の実行と予定・メモを一覧にする） */
"use strict";

const ready = renderHeader("/day");
const input = document.getElementById("day-date");
const PLANNED_LABEL = { draft: "ドラフト（キックされない）", active: "予定", paused: "一時停止中（キックされない）" };
let targets = new Map();

function currentDay() {
  return input.value || ymd(new Date());
}

function setDay(s) {
  input.value = s;
  const url = new URL(location.href);
  if (s === ymd(new Date())) url.searchParams.delete("date");
  else url.searchParams.set("date", s);
  history.replaceState(null, "", url);
  load();
}

function itemCell(targetId, name) {
  const t = targets.get(targetId);
  return el("td", {},
    el("span", { class: "swatch", style: `background:${t?.color || "#8a94a6"}` }),
    el("a", { href: `/?date=${currentDay()}`, title: "タイムラインで見る" }, name || t?.display_name || `#${targetId}`),
    t?.category_name ? el("span", { class: "muted small" }, ` ${t.category_name}`) : null);
}

function timeOf(iso) {
  const d = new Date(iso);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function runRow(r) {
  // ドラフト・一時停止中のスケジュールの未実行の run はキックされない
  if (r.status === "scheduled" && ["draft", "paused"].includes(r.schedule_status)) {
    return plannedRow(r);
  }
  return el("tr", { class: `rs-row-${r.status}` },
    el("td", { class: "mono" }, timeOf(r.scheduled_at)),
    itemCell(r.target_id, r.target_name),
    el("td", {}, r.schedule_title || (r.retry_of_id ? "再実行" : "即時実行")),
    el("td", {}, el("span", { class: `legend-dot rs-${r.status}` }), " ", RUN_STATUS_LABEL[r.status] || r.status),
    el("td", {}, r.build_url ? el("a", { href: r.build_url, target: "_blank", rel: "noopener" }, `#${r.build_number}`) : r.build_number ? `#${r.build_number}` : ""),
    el("td", { class: "small muted" }, r.reason || ""));
}

function plannedRow(p) {
  const off = p.schedule_status !== "active";
  return el("tr", { class: off ? "planned off" : "planned" },
    el("td", { class: "mono" }, timeOf(p.scheduled_at)),
    itemCell(p.target_id, p.target_name),
    el("td", {}, p.schedule_title),
    el("td", {}, el("span", { class: "legend-dot rs-scheduled" }), " ", PLANNED_LABEL[p.schedule_status] || p.schedule_status),
    el("td", {}, ""),
    el("td", { class: "small muted" }, ""));
}

function renderRuns(data) {
  const rows = [
    ...data.runs.map((r) => ({ at: r.scheduled_at, node: () => runRow(r) })),
    ...data.planned.map((p) => ({ at: p.scheduled_at, node: () => plannedRow(p) })),
  ].sort((a, b) => a.at.localeCompare(b.at));
  const box = document.getElementById("day-runs");
  if (!rows.length) {
    box.replaceChildren(el("p", { class: "muted" }, "この日の Jenkins の実行はありません。"));
    return;
  }
  const body = [];
  // 今日なら、今の時刻の位置に線を入れる
  const isToday = data.date === ymd(new Date());
  const nowIso = new Date().toISOString();
  let nowShown = !isToday;
  for (const row of rows) {
    if (!nowShown && row.at > nowIso) {
      body.push(el("tr", { class: "now-line" }, el("td", { colspan: 6 }, `現在 ${timeOf(nowIso)}`)));
      nowShown = true;
    }
    body.push(row.node());
  }
  if (!nowShown) body.push(el("tr", { class: "now-line" }, el("td", { colspan: 6 }, `現在 ${timeOf(nowIso)}`)));
  box.replaceChildren(el("table", { class: "table day-table" },
    el("thead", {}, el("tr", {}, ["時刻", "アイテム", "スケジュール", "状態", "ビルド", "理由"].map((h) => el("th", {}, h)))),
    el("tbody", {}, body)));
}

function renderMemos(data) {
  const box = document.getElementById("day-memos");
  if (!data.memos.length) {
    box.replaceChildren(el("p", { class: "muted" }, "この日の予定・メモはありません。"));
    return;
  }
  box.replaceChildren(el("ul", { class: "day-memos" }, data.memos.map((m) => {
    const period = m.start_date === m.end_date ? "" : `${fmtDate(m.start_date)} 〜 ${m.end_date ? fmtDate(m.end_date) : ""}`;
    return el("li", {},
      el("div", { class: "day-memo-head" },
        el("span", { class: "swatch", style: `background:${targets.get(m.target_id)?.color || "#8a94a6"}` }),
        el("b", {}, m.label || "（見出しなし）"),
        el("span", { class: "muted small" }, ` ${m.target_name}`),
        period ? el("span", { class: "muted small day-period" }, period) : null),
      m.note ? el("div", { class: "day-memo-note" }, m.note) : null);
  })));
}

async function load() {
  const day = currentDay();
  const d = parseYmd(day);
  const isToday = day === ymd(new Date());
  document.getElementById("day-title").replaceChildren(
    `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日（${WD[d.getDay()]}）`,
    isToday ? el("span", { class: "today-tag" }, "今日") : null);
  document.title = `${isToday ? "今日" : `${d.getMonth() + 1}/${d.getDate()}`}の予定 - Jenkins Scheduler`;
  try {
    const [ts, data] = await Promise.all([api("GET", "/api/targets"), api("GET", `/api/agenda?date=${day}`)]);
    if (day !== currentDay()) return; // 読み込み中に日付が変わった
    targets = new Map(ts.map((t) => [t.id, t]));
    renderRuns(data);
    renderMemos(data);
  } catch (e) {
    toast(e.message, "error");
  }
}

const initial = new URLSearchParams(location.search).get("date");
input.value = /^\d{4}-\d{2}-\d{2}$/.test(initial || "") ? initial : ymd(new Date());
input.onchange = () => input.value && setDay(input.value);
document.getElementById("day-prev").onclick = () => setDay(ymd(addDays(parseYmd(currentDay()), -1)));
document.getElementById("day-next").onclick = () => setDay(ymd(addDays(parseYmd(currentDay()), 1)));
document.getElementById("day-today").onclick = () => setDay(ymd(new Date()));
ready.then(load);
// 他の人の変更や実行結果を反映する
setInterval(() => !document.hidden && load(), 15000);
