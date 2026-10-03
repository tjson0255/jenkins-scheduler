/* 1日の予定（日付と開始時刻を選んで、そこから24時間の予定と Jenkins の実行を一覧にする） */
"use strict";

const ready = renderHeader("/day");
let selectedDay = null; // 選んでいる日付（YYYY-MM-DD）
let calMonth = null; // カレンダーに出している月（1日の Date）
const startInput = document.getElementById("day-start");
const PLANNED_LABEL = { draft: "ドラフト（キックされない）", active: "予定", paused: "一時停止中（キックされない）" };
const DEFAULT_START = "17:00";
let targets = new Map();

function currentDay() {
  return selectedDay || currentWindowDay();
}

function currentStart() {
  return /^\d{2}:\d{2}$/.test(startInput.value) ? startInput.value : DEFAULT_START;
}

/** 選んだ日付の開始時刻（ブラウザの時刻） */
function windowStart(day, start) {
  const [h, m] = start.split(":").map(Number);
  const d = parseYmd(day);
  d.setHours(h, m, 0, 0);
  return d;
}

/** 今の時刻を含む「1日」の日付（開始が 17:00 なら、17:00 より前は前の日） */
function currentWindowDay() {
  const now = new Date();
  const today = ymd(now);
  return now < windowStart(today, currentStart()) ? ymd(addDays(now, -1)) : today;
}

function updateUrl() {
  const url = new URL(location.href);
  if (currentDay() === currentWindowDay()) url.searchParams.delete("date");
  else url.searchParams.set("date", currentDay());
  url.searchParams.delete("start"); // 開始時刻はブラウザに保存する
  history.replaceState(null, "", url);
}

function setDay(s) {
  selectedDay = s;
  calMonth = startOfMonth(parseYmd(s));
  updateUrl();
  load();
}

/** タイムラインを開いて、スケジュール（またはアイテム）の詳細を出すリンク先 */
function detailHref(hash) {
  return `/?date=${currentDay()}#${hash}`;
}

function categoryCell(targetId) {
  return el("td", { class: "muted" }, targets.get(targetId)?.category_name || "");
}

function itemCell(targetId, name, hash) {
  const t = targets.get(targetId);
  return el("td", {},
    el("span", { class: "swatch", style: `background:${t?.color || "#8a94a6"}` }),
    el("a", { href: detailHref(hash), title: "実行履歴を開く" }, name || t?.display_name || `#${targetId}`));
}

/** スケジュールの欄。押すとタイムラインでそのスケジュールの詳細（基本情報）を開く */
function scheduleCell(scheduleId, title) {
  if (!scheduleId) return el("td", {}, title);
  return el("td", {}, el("a", { href: detailHref(`schedule=${scheduleId}&tab=basic`), title: "スケジュールの詳細を開く" }, title));
}

function timeOf(iso) {
  const d = new Date(iso);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** 時刻の欄。選んだ日付の翌日にかかる分は「翌」を付ける */
function timeCell(iso) {
  const nextDay = ymd(new Date(iso)) !== currentDay();
  return el("td", { class: "mono" }, nextDay ? el("span", { class: "next-day" }, "翌 ") : null, timeOf(iso));
}

function runRow(r) {
  // ドラフト・一時停止中のスケジュールの未実行の run はキックされない
  if (r.status === "scheduled" && ["draft", "paused"].includes(r.schedule_status)) {
    return plannedRow(r);
  }
  return el("tr", { class: `rs-row-${r.status}` },
    timeCell(r.scheduled_at),
    categoryCell(r.target_id),
    // スケジュールの run はその実行履歴の行へ、即時実行など（スケジュールなし）はアイテムの予定一覧へ
    itemCell(r.target_id, r.target_name, r.schedule_id ? `schedule=${r.schedule_id}&run=${r.id}` : `item=${r.target_id}`),
    scheduleCell(r.schedule_id, r.schedule_title || (r.retry_of_id ? "再実行" : "即時実行")),
    el("td", {}, el("span", { class: `legend-dot rs-${r.status}` }), " ", RUN_STATUS_LABEL[r.status] || r.status),
    el("td", {}, r.build_url ? el("a", { href: r.build_url, target: "_blank", rel: "noopener" }, `#${r.build_number}`) : r.build_number ? `#${r.build_number}` : ""),
    el("td", { class: "small muted" }, r.reason || ""));
}

function plannedRow(p) {
  const off = p.schedule_status !== "active";
  return el("tr", { class: off ? "planned off" : "planned" },
    timeCell(p.scheduled_at),
    categoryCell(p.target_id),
    itemCell(p.target_id, p.target_name, p.id ? `schedule=${p.schedule_id}&run=${p.id}` : `schedule=${p.schedule_id}&tab=basic`),
    scheduleCell(p.schedule_id, p.schedule_title),
    el("td", {}, el("span", { class: "legend-dot rs-scheduled" }), " ", PLANNED_LABEL[p.schedule_status] || p.schedule_status),
    el("td", {}, ""),
    el("td", { class: "small muted" }, ""));
}

function renderRuns(data, isCurrent) {
  const rows = [
    ...data.runs.map((r) => ({ at: r.scheduled_at, node: () => runRow(r) })),
    ...data.planned.map((p) => ({ at: p.scheduled_at, node: () => plannedRow(p) })),
  ].sort((a, b) => a.at.localeCompare(b.at));
  const box = document.getElementById("day-runs");
  if (!rows.length) {
    box.replaceChildren(el("p", { class: "muted" }, "この範囲の Jenkins の実行はありません。"));
    return;
  }
  const body = [];
  // 今の時刻を含む範囲なら、今の時刻の位置に線を入れる
  const nowIso = new Date().toISOString();
  let nowShown = !isCurrent;
  for (const row of rows) {
    if (!nowShown && row.at > nowIso) {
      body.push(el("tr", { class: "now-line" }, el("td", { colspan: 7 }, `現在 ${timeOf(nowIso)}`)));
      nowShown = true;
    }
    body.push(row.node());
  }
  if (!nowShown) body.push(el("tr", { class: "now-line" }, el("td", { colspan: 7 }, `現在 ${timeOf(nowIso)}`)));
  box.replaceChildren(el("table", { class: "table day-table" },
    el("thead", {}, el("tr", {}, ["時刻", "カテゴリ", "アイテム", "スケジュール", "状態", "ビルド", "詳細"].map((h) => el("th", {}, h)))),
    el("tbody", {}, body)));
}

function renderMemos(data) {
  const box = document.getElementById("day-memos");
  if (!data.memos.length) {
    box.replaceChildren(el("p", { class: "muted" }, "この範囲の予定はありません。"));
    return;
  }
  box.replaceChildren(el("ul", { class: "day-memos" }, data.memos.map((m) => {
    const period = m.start_date === m.end_date ? fmtDate(m.start_date) : `${fmtDate(m.start_date)} 〜 ${m.end_date ? fmtDate(m.end_date) : ""}`;
    return el("li", {},
      el("div", { class: "day-memo-head" },
        el("span", { class: "swatch", style: `background:${targets.get(m.target_id)?.color || "#8a94a6"}` }),
        el("a", { href: detailHref(`schedule=${m.schedule_id}`), class: "day-memo-title", title: "予定の詳細を開く" }, m.label || "（見出しなし）"),
        el("span", { class: "muted small" }, ` ${m.target_name}`),
        el("span", { class: "muted small day-period" }, period)),
      m.note ? el("div", { class: "day-memo-note" }, m.note) : null);
  })));
}

function fmtMd(d) {
  return `${d.getMonth() + 1}月${d.getDate()}日（${WD[d.getDay()]}）`;
}

/* ---- 常に表示するカレンダー ---- */
function startOfMonth(d) {
  return new Date(d.getFullYear(), d.getMonth(), 1);
}

let memoDaysCache = { key: "", days: new Set() };

/** その月で予定（予定のアイテムに書いたもの）がある日 */
async function memoDays(first, last) {
  const key = `${ymd(first)}|${ymd(last)}`;
  if (memoDaysCache.key === key) return memoDaysCache.days;
  const days = new Set();
  try {
    const ss = await api("GET", `/api/schedules?from=${ymd(first)}&to=${ymd(last)}`);
    for (const s of ss.filter((x) => x.mode === "memo")) {
      for (let d = parseYmd(s.start_date > ymd(first) ? s.start_date : ymd(first)); ; d = addDays(d, 1)) {
        const k = ymd(d);
        if (k > ymd(last) || (s.end_date && k > s.end_date)) break;
        days.add(k);
      }
    }
  } catch (_) {
    return days; // 印が出ないだけなので、エラーは出さない
  }
  memoDaysCache = { key, days };
  return days;
}

async function renderCalendar() {
  const box = document.getElementById("day-cal");
  const first = calMonth;
  const gridStart = addDays(first, -first.getDay()); // 日曜はじまり
  const gridEnd = addDays(gridStart, 41);
  const marks = await memoDays(gridStart, gridEnd);
  const today = ymd(new Date());
  const cells = [];
  for (let i = 0; i < 42; i++) {
    const d = addDays(gridStart, i);
    const k = ymd(d);
    const cls = ["cal-day", d.getMonth() !== first.getMonth() ? "other" : "", d.getDay() === 0 ? "sun" : d.getDay() === 6 ? "sat" : "",
      k === today ? "today" : "", k === currentDay() ? "selected" : "", marks.has(k) ? "has-memo" : ""].filter(Boolean).join(" ");
    cells.push(el("button", { type: "button", class: cls, title: marks.has(k) ? "予定あり" : "", onclick: () => setDay(k) }, String(d.getDate())));
  }
  const move = (n) => {
    calMonth = new Date(calMonth.getFullYear(), calMonth.getMonth() + n, 1);
    renderCalendar();
  };
  box.replaceChildren(
    el("div", { class: "cal-head" },
      el("button", { type: "button", class: "cal-nav", title: "前の月", onclick: () => move(-1) }, "‹"),
      el("span", { class: "cal-title" }, `${first.getFullYear()}年${first.getMonth() + 1}月`),
      el("button", { type: "button", class: "cal-nav", title: "次の月", onclick: () => move(1) }, "›")),
    el("div", { class: "cal-grid" },
      WD.map((w, i) => el("span", { class: `cal-wd${i === 0 ? " sun" : i === 6 ? " sat" : ""}` }, w)),
      cells));
}

async function load() {
  const day = currentDay();
  memoDaysCache.key = ""; // 他の人が足した予定の印も出るよう、読み込みのたびに取り直す
  renderCalendar();
  const start = currentStart();
  const from = windowStart(day, start);
  const to = addDays(from, 1);
  const now = new Date();
  const isCurrent = from <= now && now < to;
  document.getElementById("day-title").replaceChildren(
    `${from.getFullYear()}年${fmtMd(from)}`,
    isCurrent ? el("span", { class: "today-tag" }, "今日") : null);
  document.getElementById("day-range").textContent =
    start === "00:00" ? "0:00 〜 24:00" : `${fmtMd(from)} ${start} 〜 ${fmtMd(to)} ${start}`;
  document.title = `${isCurrent ? "今日" : `${from.getMonth() + 1}/${from.getDate()}`}の予定 - Jenkins Scheduler`;
  try {
    const [ts, data] = await Promise.all([
      api("GET", "/api/targets"),
      api("GET", `/api/agenda?date=${day}&start=${encodeURIComponent(start)}`),
    ]);
    if (day !== currentDay() || start !== currentStart()) return; // 読み込み中に条件が変わった
    targets = new Map(ts.map((t) => [t.id, t]));
    renderMemos(data);
    renderRuns(data, isCurrent);
  } catch (e) {
    toast(e.message, "error");
  }
}

// 開始時刻はブラウザごとに保存する（他の人には影響しない）。URL の ?start= があればそれを使う
const params = new URLSearchParams(location.search);
let savedStart = null;
try {
  savedStart = localStorage.getItem("dayStart");
} catch (_) {}
const askedStart = params.get("start");
startInput.value = [askedStart, savedStart].find((v) => /^\d{2}:\d{2}$/.test(v || "")) || DEFAULT_START;
const askedDay = params.get("date");
selectedDay = /^\d{4}-\d{2}-\d{2}$/.test(askedDay || "") ? askedDay : currentWindowDay();
calMonth = startOfMonth(parseYmd(selectedDay));

startInput.onchange = () => {
  if (!startInput.value) startInput.value = DEFAULT_START;
  try {
    localStorage.setItem("dayStart", currentStart());
  } catch (_) {}
  updateUrl();
  load();
};
document.getElementById("day-prev").onclick = () => setDay(ymd(addDays(parseYmd(currentDay()), -1)));
document.getElementById("day-next").onclick = () => setDay(ymd(addDays(parseYmd(currentDay()), 1)));
document.getElementById("day-today").onclick = () => setDay(currentWindowDay());
ready.then(load);
// 他の人の変更や実行結果を反映する
setInterval(() => !document.hidden && load(), 15000);
