/* 実行結果（行=Jenkins のレーン、列=日付。その日の結果の色でマスを塗る） */
"use strict";

const ready = renderHeader("/history");

// 1日に複数の回があるときは、いちばん気にすべき結果の色にする（上ほど優先）
const SEVERITY = ["failure", "aborted", "holding", "missed", "unstable", "running", "queued", "success", "skipped", "cancelled", "scheduled"];
const EXECUTED = ["success", "unstable", "failure", "aborted"];
const LEGEND = [
  ["scheduled", "予定"], ["holding", "保留"], ["skipped", "スキップ"], ["missed", "見逃し"],
  ["running", "キュー/実行中"], ["success", "成功"], ["unstable", "不安定"], ["failure", "失敗"], ["aborted", "中断"],
];

const st = { end: startOfToday(), days: 28, cat: "", categories: [], targets: [], runs: [] };

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

function cellTooltip(t, day, runs) {
  return [`${t.display_name} ${fmtDate(day)}`, ...runs.map((r) => `${timeOf(r.scheduled_at)} ${RUN_STATUS_LABEL[r.status] || r.status}・${RUN_SIDE[r.status] === "jenkins" ? "Jenkins 側" : "ツール側"}${r.build_number ? ` #${r.build_number}` : ""}`)].join("\n");
}

function openDayModal(t, day, runs) {
  const rows = runs.map((r) => el("tr", {},
    el("td", { class: "mono" }, timeOf(r.scheduled_at)),
    el("td", {}, r.schedule_id
      ? el("a", { href: `/?date=${day}#schedule=${r.schedule_id}&run=${r.id}`, title: "タイムラインで実行履歴を開く" }, r.schedule_title || "スケジューラ")
      : r.retry_of_id ? "再実行" : "即時実行"),
    el("td", { title: runStatusHint(r.status) }, el("span", { class: `legend-dot rs-${r.status}` }), " ", RUN_STATUS_LABEL[r.status] || r.status),
    el("td", {}, r.build_url ? el("a", { href: r.build_url, target: "_blank", rel: "noopener" }, `#${r.build_number}`) : ""),
    el("td", { class: "small muted" }, r.reason || "")));
  openModal(`${t.display_name}　${fmtDate(day)}`, el("table", { class: "table small" },
    el("thead", {}, el("tr", {}, ["時刻", "スケジューラ", "状態", "ビルド", "詳細"].map((h) => el("th", {}, h)))),
    el("tbody", {}, rows)), [el("button", { class: "btn", onclick: closeModal }, "閉じる")]);
}

function render() {
  const days = dayList();
  const today = ymd(new Date());
  const first = days[0];
  const last = days[days.length - 1];
  document.getElementById("hist-range").textContent = `${fmtDate(ymd(first))} 〜 ${fmtDate(ymd(last))}`;

  // レーンごと・日ごとに run をまとめる
  const byCell = new Map();
  for (const r of st.runs) {
    const k = `${r.target_id}|${ymd(new Date(r.scheduled_at))}`;
    if (!byCell.has(k)) byCell.set(k, []);
    byCell.get(k).push(r);
  }
  for (const list of byCell.values()) list.sort((a, b) => a.scheduled_at.localeCompare(b.scheduled_at));

  const lanes = [];
  for (const c of st.categories) {
    if (st.cat && String(c.id) !== st.cat) continue;
    lanes.push(...st.targets.filter((t) => t.category_id === c.id && t.kind !== "memo").sort((a, b) => a.sort_order - b.sort_order || a.id - b.id));
  }

  const head = el("tr", {},
    el("th", { class: "hist-lane" }, "レーン"),
    days.map((d) => {
      const k = ymd(d);
      const cls = ["hist-day", d.getDay() === 0 ? "sun" : d.getDay() === 6 ? "sat" : "", k === today ? "today" : "", d.getDate() === 1 ? "month-start" : ""].filter(Boolean).join(" ");
      return el("th", { class: cls, title: fmtDate(k) }, el("div", {}, `${d.getDate()}`), el("div", { class: "hist-wd" }, WD[d.getDay()]));
    }),
    el("th", { class: "hist-sum" }, "成功 / 実行"));

  const body = lanes.map((t) => {
    let ok = 0;
    let done = 0;
    const cells = days.map((d) => {
      const k = ymd(d);
      const runs = byCell.get(`${t.id}|${k}`) || [];
      for (const r of runs) {
        if (EXECUTED.includes(r.status)) done++;
        if (r.status === "success") ok++;
      }
      const cls = ["hist-cell", d.getDay() === 0 ? "sun" : d.getDay() === 6 ? "sat" : "", k === today ? "today" : "", d.getDate() === 1 ? "month-start" : ""].filter(Boolean).join(" ");
      if (!runs.length) return el("td", { class: cls });
      const w = worst(runs);
      return el("td", { class: cls },
        el("button", {
          type: "button",
          class: `hist-mark rs-${w}`,
          title: cellTooltip(t, k, runs) + "\n\n押すと詳細",
          onclick: () => openDayModal(t, k, runs),
        }, runs.length > 1 ? String(runs.length) : ""));
    });
    const rate = done ? Math.round((ok / done) * 100) : null;
    return el("tr", {},
      el("th", { class: "hist-lane", scope: "row" },
        el("span", { class: "swatch inline", style: `background:${t.color || "#8a94a6"}` }), t.display_name),
      cells,
      el("td", { class: `hist-sum${rate !== null && rate < 100 ? " has-fail" : ""}` }, done ? `${ok} / ${done}` : "—"));
  });

  const box = document.getElementById("hist-grid");
  const prev = box.querySelector(".hist-wrap");
  const keep = prev && st.keepScroll ? prev.scrollLeft : null;
  box.replaceChildren(lanes.length
    ? el("div", { class: "table-wrap hist-wrap" }, el("table", { class: "hist-table" }, el("thead", {}, head), el("tbody", {}, body)))
    : el("p", { class: "muted" }, "Jenkins のレーンがありません。"));
  // 新しい日付が右端なので、最初は右端（直近）を見せる。自動更新のときは見ていた位置のまま
  const wrap = box.querySelector(".hist-wrap");
  if (wrap) wrap.scrollLeft = keep ?? wrap.scrollWidth;
  st.keepScroll = false;
}

function renderControls() {
  const cat = document.getElementById("hist-cat");
  cat.replaceChildren(
    el("option", { value: "" }, "すべてのカテゴリ"),
    st.categories.map((c) => el("option", { value: c.id, selected: String(c.id) === st.cat }, c.name)));
  // 問題がこのツール側にあるか、Jenkins 側にあるかで分けて並べる
  const item = ([k, label]) => el("span", { class: "hist-legend-item", title: runStatusHint(k) }, el("span", { class: `hist-mark small rs-${k}` }), label);
  document.getElementById("hist-legend").replaceChildren(
    el("span", { class: "legend-side", title: SIDE_HINT.tool }, "ツール側"),
    ...LEGEND.filter(([k]) => RUN_SIDE[k] === "tool").map(item),
    el("span", { class: "legend-sep" }),
    el("span", { class: "legend-side", title: SIDE_HINT.jenkins }, "Jenkins 側"),
    ...LEGEND.filter(([k]) => RUN_SIDE[k] === "jenkins").map(item),
    el("span", { class: "muted small" }, "1日に複数回あるときは、いちばん気にすべき結果の色と回数を出します"));
}

async function load() {
  const days = dayList();
  const from = ymd(days[0]);
  const to = ymd(days[days.length - 1]);
  try {
    const [categories, targets, runs] = await Promise.all([
      api("GET", "/api/categories"),
      api("GET", "/api/targets"),
      api("GET", `/api/runs?from=${from}&to=${to}&limit=10000`),
    ]);
    st.categories = categories;
    st.targets = targets;
    st.runs = runs;
    renderControls();
    render();
  } catch (e) {
    toast(e.message, "error");
  }
}

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
document.getElementById("hist-cat").onchange = (e) => {
  st.cat = e.target.value;
  render();
};
ready.then(load);
setInterval(() => {
  if (document.hidden) return;
  st.keepScroll = true;
  load();
}, 60000);
