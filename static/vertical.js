/* 縦表示: 行=日付、列=アイテム（カテゴリごとにまとめ、折りたたみ可）
 *
 * vis-timeline は縦向きに対応していないので、表（table）で描く。
 * state / visibleTargets / openPanel などは app.js のものを使う（関数は呼び出し時に解決される）。
 */
"use strict";

const LANE_W = 9; // スケジュール1本分の縦帯の幅(px)
const V_MAX_RUNS_PER_CELL = 4;
const vState = { collapsed: new Set(), scrollToToday: true, drag: null, wired: false };

try {
  for (const id of JSON.parse(localStorage.getItem("vCollapsed") || "[]")) vState.collapsed.add(id);
} catch (_) {}

/** カテゴリの折りたたみ状態を変えて記憶する（横表示・縦表示で共通） */
function vSetCollapsed(catId, collapsed) {
  collapsed ? vState.collapsed.add(catId) : vState.collapsed.delete(catId);
  try {
    localStorage.setItem("vCollapsed", JSON.stringify([...vState.collapsed]));
  } catch (_) {}
  updateCollapseButtons();
}

/** すべてのカテゴリをまとめて折りたたむ／展開する（横表示・縦表示で共通） */
function setAllCollapsed(collapsed) {
  vState.collapsed = new Set(collapsed ? state.categories.map((c) => c.id) : []);
  try {
    localStorage.setItem("vCollapsed", JSON.stringify([...vState.collapsed]));
  } catch (_) {}
  render();
}

/* ---- まとめて開閉する1つのボタン（タイムラインの左上・縦表示の「日付」欄） ---- */
function allExpanded() {
  const used = new Set(state.targets.map((t) => t.category_id));
  return state.categories.filter((c) => used.has(c.id)).every((c) => !vState.collapsed.has(c.id));
}

/** 全部開いていればすべて閉じ、1つでも閉じていればすべて開く */
function toggleAllCollapsed() {
  setAllCollapsed(allExpanded());
}

function collapseToggleHtml() {
  const open = allExpanded();
  return `<button type="button" class="collapse-toggle" data-action="toggle-all" title="${open ? "すべてのカテゴリを折りたたむ" : "すべてのカテゴリを展開する"}">${open ? "⊟ 折りたたむ" : "⊞ 展開"}</button>`;
}

function updateCollapseButtons() {
  const open = allExpanded();
  document.querySelectorAll(".collapse-toggle").forEach((b) => {
    b.textContent = open ? "⊟ 折りたたむ" : "⊞ 展開";
    b.title = open ? "すべてのカテゴリを折りたたむ" : "すべてのカテゴリを展開する";
  });
}

/* ------------------------------------------------------------------ 表示範囲の操作 */
function vSetDays(days) {
  days = Math.min(182, Math.max(7, Math.round(days)));
  const center = addDays(state.v.start, Math.floor(state.v.days / 2));
  state.v.days = days;
  state.v.start = addDays(center, -Math.floor(days / 2));
  loadData();
}

function vShift(frac) {
  state.v.start = addDays(state.v.start, Math.round(state.v.days * frac));
  loadData();
}

function vGoToday() {
  state.v.start = addDays(startOfDay(new Date()), -Math.round(state.v.days * 0.25));
  vState.scrollToToday = true;
  loadData();
}

/* ------------------------------------------------------------------ 描画 */
function vScheduleSpans(schedules, days) {
  // 表示範囲内の行番号 [s, e] に切り出し、重なるものは別レーンに並べる
  const start = state.v.start;
  const spans = [];
  for (const s of schedules) {
    const si = Math.round((parseYmd(s.start_date) - start) / 86400000);
    const ei = s.end_date ? Math.round((parseYmd(s.end_date) - start) / 86400000) : Infinity;
    if (ei < 0 || si > days - 1) continue;
    spans.push({ s, si, ei, from: Math.max(0, si), to: Math.min(days - 1, ei) });
  }
  spans.sort((a, b) => a.si - b.si || a.s.id - b.s.id);
  const laneEnds = [];
  for (const sp of spans) {
    let lane = laneEnds.findIndex((end) => end < sp.from);
    if (lane === -1) lane = laneEnds.length;
    laneEnds[lane] = sp.to;
    sp.lane = lane;
  }
  return { spans, lanes: laneEnds.length };
}

function vItemHead(t) {
  const warn = [];
  if (t.schema_error) warn.push(`<span class="warn-icon err" title="${esc(t.schema_error)}">⛔</span>`);
  if (t.timer_trigger_detected) warn.push(`<span class="warn-icon" title="Jenkins 側の cron が残っています">⏰</span>`);
  const ic = t.issue_counts || {};
  if (ic.error) warn.push(`<span class="warn-icon err" title="パラメータ定義のエラー ${ic.error} 件">●</span>`);
  else if (ic.warning) warn.push(`<span class="warn-icon warn" title="パラメータ定義の警告 ${ic.warning} 件">●</span>`);
  const lvl = itemAlertLevel(t);
  const tip = [t.kind === "memo" ? "予定" : t.job_path, ...itemAlertMessages(t), "クリックで予定一覧"].join("\n");
  return `<th class="v-item-head${t.enabled ? "" : " disabled"}${lvl ? " alert-" + lvl : ""}" data-item="${t.id}" title="${esc(tip)}">
    <span class="swatch" style="background:${esc(t.color || "#8a94a6")}"></span><span class="v-item-name">${esc(t.display_name)}</span>${t.kind === "memo" ? '<span class="memo-icon" title="予定">📝</span>' : ""}${warn.join("")}
  </th>`;
}

function vCell(t, dayIdx, day, info, runsByDay, showRuns) {
  const color = esc(t.color || "#8a94a6");
  const stripes = [];
  const labels = [];
  for (const sp of info.spans) {
    if (dayIdx < sp.from || dayIdx > sp.to) continue;
    const s = sp.s;
    const isStart = dayIdx === sp.si;
    const isEnd = dayIdx === sp.ei;
    const cls = `v-stripe st-${s.status}${s.mode === "memo" ? " memo" : ""}${isStart ? " is-start" : ""}${isEnd ? " is-end" : ""}${scheduleAlertLevel(s) ? " has-" + scheduleAlertLevel(s) : ""}`;
    stripes.push(`<div class="${cls}" data-sched="${s.id}" style="left:${sp.lane * LANE_W + 2}px;--c:${color}" title="${esc(scheduleTitle(s))}"></div>`);
    // ラベルは開始日に出す。途中から見ても分かるよう、表示範囲の先頭行と今日の行にも「↑」付きで出す
    if (isStart || dayIdx === sp.from || day === vState.today) {
      const lvl = scheduleAlertLevel(s);
      const badge = lvl ? `<span class="badge-dot ${lvl}">!</span>` : "";
      const st = s.status !== "active" && s.mode !== "memo" ? `<span class="st-tag">${SCHEDULE_STATUS_LABEL[s.status]}</span>` : "";
      const cont = !isStart ? "↑ " : "";
      labels.push(`<span class="v-label st-${s.status}${s.mode === "memo" ? " memo" : ""}${lvl ? " has-" + lvl : ""}" data-sched="${s.id}" style="--c:${color}" title="${scheduleTooltip(s).replace(/<br>/g, "&#10;")}">${badge}${st}${cont}${esc(scheduleTitle(s))}${s.end_date ? "" : " ∞"}</span>`);
    }
  }
  const runs = showRuns ? runsByDay.get(`${t.id}|${day}`) || [] : [];
  const chips = runs.slice(0, V_MAX_RUNS_PER_CELL).map((r) => {
    const d = new Date(r.scheduled_at);
    return `<span class="v-run rs-${r.status}" data-run="${r.id}" title="${runTooltip(r).replace(/<br>/g, "&#10;")}">${pad(d.getHours())}:${pad(d.getMinutes())}</span>`;
  });
  if (runs.length > V_MAX_RUNS_PER_CELL) chips.push(`<span class="v-more" data-item="${t.id}">+${runs.length - V_MAX_RUNS_PER_CELL}</span>`);
  const pad0 = info.lanes * LANE_W + 6;
  return `<td class="v-cell${t.enabled ? "" : " disabled"}" data-t="${t.id}" data-i="${dayIdx}">
    ${stripes.join("")}
    <div class="v-content" style="padding-left:${pad0}px">${labels.join("")}${chips.length ? `<div class="v-runs">${chips.join("")}</div>` : ""}</div>
  </td>`;
}

function renderVertical() {
  const box = document.getElementById("vgrid");
  vWire(box);
  const days = state.v.days;
  const vt = visibleTargets();
  const showRuns = days <= RUN_POINT_MAX_DAYS;
  document.getElementById("summary-note").textContent = runNote(showRuns);

  const cols = []; // {cat, items:[t]|null(collapsed)}
  for (const c of state.categories) {
    const children = vt.filter((t) => t.category_id === c.id);
    if (!children.length) continue;
    cols.push({ cat: c, items: children, collapsed: vState.collapsed.has(c.id) });
  }
  const spansByItem = new Map();
  for (const t of vt) spansByItem.set(t.id, vScheduleSpans(state.schedules.filter((s) => s.target_id === t.id), days));
  const runsByDay = new Map();
  if (showRuns) {
    for (const r of state.runs) {
      if (!runVisible(r)) continue;
      const k = `${r.target_id}|${ymd(new Date(r.scheduled_at))}`;
      if (!runsByDay.has(k)) runsByDay.set(k, []);
      runsByDay.get(k).push(r);
    }
    for (const list of runsByDay.values()) list.sort((a, b) => new Date(a.scheduled_at) - new Date(b.scheduled_at));
  }

  // 折りたたんだカテゴリは見出し2段ぶんを1つのセルにする
  const head1 = cols.map(({ cat, items, collapsed }) =>
    collapsed
      ? `<th class="v-cat-head collapsed" rowspan="2" data-cat="${cat.id}" title="クリックで展開">▶ ${esc(cat.name)}</th>`
      : `<th class="v-cat-head" colspan="${items.length}" data-cat="${cat.id}" title="クリックで折りたたみ">▼ ${esc(cat.name)}</th>`
  );
  const head2 = cols.flatMap(({ items, collapsed }) => (collapsed ? [] : items.map(vItemHead)));

  const todayStr = ymd(new Date());
  vState.today = todayStr;
  const rows = [];
  for (let i = 0; i < days; i++) {
    const d = addDays(state.v.start, i);
    const day = ymd(d);
    const dow = d.getDay();
    const showMonth = i === 0 || d.getDate() === 1;
    const cls = [dow === 0 ? "sun" : dow === 6 ? "sat" : "", day === todayStr ? "today" : "", d.getDate() === 1 ? "month-start" : ""].join(" ");
    const cells = cols.flatMap(({ cat, items, collapsed }) =>
      collapsed ? [vCollapsedCell(cat, items, day, runsByDay, showRuns)] : items.map((t) => vCell(t, i, day, spansByItem.get(t.id), runsByDay, showRuns))
    );
    rows.push(`<tr class="${cls}" data-day="${day}">
      <th class="v-date" scope="row">${showMonth ? `<span class="v-month">${d.getFullYear()}/${d.getMonth() + 1}</span>` : ""}${d.getDate()}<span class="v-dow">(${WD[dow]})</span>${day === todayStr ? '<span class="v-today">今日</span>' : ""}</th>
      ${cells.join("")}
    </tr>`);
  }

  const scrollTop = box.scrollTop;
  const scrollLeft = box.scrollLeft;
  box.style.maxHeight = `${Math.max(300, window.innerHeight - 150)}px`;
  box.innerHTML = cols.length
    ? `<table class="v-table">
        <thead>
          <tr><th class="v-corner" rowspan="2">${collapseToggleHtml()}</th>${head1.join("")}</tr>
          <tr>${head2.join("")}</tr>
        </thead>
        <tbody>${rows.join("")}</tbody>
      </table>`
    : '<p class="muted" style="padding:16px">表示するアイテムがありません。アイテム画面で登録してください。</p>';

  if (vState.scrollToToday) {
    vState.scrollToToday = false;
    const row = box.querySelector("tr.today");
    const headH = box.querySelector("thead") ? box.querySelector("thead").offsetHeight : 0;
    box.scrollTop = row ? Math.max(0, row.offsetTop - headH - 40) : 0;
  } else {
    box.scrollTop = scrollTop;
  }
  box.scrollLeft = scrollLeft;
  vMarkSelection();
}

// 折りたたんだカテゴリの列: その日の run 件数を、いちばん注意が必要な状態の色で出す
const V_STATUS_RANK = ["failure", "holding", "missed", "unstable", "aborted", "running", "queued", "skipped", "success", "scheduled", "cancelled"];

function vCollapsedCell(cat, items, day, runsByDay, showRuns) {
  const runs = showRuns ? items.flatMap((t) => runsByDay.get(`${t.id}|${day}`) || []) : [];
  let body = "";
  if (runs.length) {
    const worst = runs.map((r) => r.status).sort((a, b) => V_STATUS_RANK.indexOf(a) - V_STATUS_RANK.indexOf(b))[0];
    const counts = {};
    for (const r of runs) counts[r.status] = (counts[r.status] || 0) + 1;
    const title = Object.entries(counts).map(([st, n]) => `${RUN_STATUS_LABEL[st] || st} ${n}`).join(" / ");
    body = `<span class="v-sum rs-${worst}" title="${esc(title)}">${runs.length}件</span>`;
  }
  return `<td class="v-collapsed-cell" data-cat="${cat.id}" title="クリックで「${esc(cat.name)}」を展開">${body}</td>`;
}

/* ------------------------------------------------------------------ 操作（イベントは委譲で1回だけ登録） */
function vWire(box) {
  if (vState.wired) return;
  vState.wired = true;

  box.addEventListener("click", (e) => {
    if (vState.justDragged) return;
    if (e.target.closest('[data-action="toggle-all"]')) return toggleAllCollapsed();
    const sched = e.target.closest("[data-sched]");
    if (sched) return openPanel(Number(sched.dataset.sched), "basic");
    const run = e.target.closest("[data-run]");
    if (run) {
      const r = state.runs.find((x) => x.id === Number(run.dataset.run));
      if (!r) return;
      return r.schedule_id ? openPanel(r.schedule_id, "runs", r.id) : showRunModal(r);
    }
    const item = e.target.closest("[data-item]");
    if (item) return openItemPanel(Number(item.dataset.item));
    const cat = e.target.closest("[data-cat]");
    if (cat) {
      const id = Number(cat.dataset.cat);
      vSetCollapsed(id, !vState.collapsed.has(id));
      renderVertical();
    }
  });

  box.addEventListener("dblclick", (e) => {
    const cell = e.target.closest("td.v-cell");
    if (!cell || e.target.closest("[data-sched],[data-run],[data-item]")) return;
    if (!can.editItem(state.targets.find((t) => t.id === Number(cell.dataset.t)))) return;
    const day = ymd(addDays(state.v.start, Number(cell.dataset.i)));
    openCreateDialog(Number(cell.dataset.t), day, day);
  });

  // 空きセルを縦にドラッグして期間を選び、作成ダイアログを開く
  box.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    const cell = e.target.closest("td.v-cell");
    if (!cell || e.target.closest("[data-sched],[data-run],[data-item]")) return;
    if (!can.editItem(state.targets.find((t) => t.id === Number(cell.dataset.t)))) return;
    e.preventDefault();
    vState.drag = { t: cell.dataset.t, a: Number(cell.dataset.i), b: Number(cell.dataset.i) };
    state.dragging = true;
    vMarkSelection();
    window.addEventListener("pointermove", vOnMove);
    window.addEventListener("pointerup", vOnUp, { once: true });
  });
}

function vOnMove(e) {
  const d = vState.drag;
  if (!d) return;
  const el0 = document.elementFromPoint(e.clientX, e.clientY);
  const cell = el0 && el0.closest("td.v-cell");
  if (!cell || cell.dataset.t !== d.t) return;
  const i = Number(cell.dataset.i);
  if (i !== d.b) {
    d.b = i;
    vMarkSelection();
  }
}

function vMarkSelection() {
  const box = document.getElementById("vgrid");
  box.querySelectorAll("td.v-cell.sel").forEach((c) => c.classList.remove("sel"));
  // ドラッグ中、または作成ダイアログを開いている間（vState.sel）は選択範囲を表示する
  const d = vState.drag || vState.sel;
  if (!d) return;
  const [lo, hi] = d.a <= d.b ? [d.a, d.b] : [d.b, d.a];
  box.querySelectorAll(`td.v-cell[data-t="${d.t}"]`).forEach((c) => {
    const i = Number(c.dataset.i);
    if (i >= lo && i <= hi) c.classList.add("sel");
  });
}

function vOnUp() {
  window.removeEventListener("pointermove", vOnMove);
  const d = vState.drag;
  vState.drag = null;
  state.dragging = false;
  if (!d || d.a === d.b) {
    vMarkSelection();
    return;
  }
  // ドラッグ直後の click で詳細が開かないようにする
  vState.justDragged = true;
  setTimeout(() => (vState.justDragged = false), 0);
  const [lo, hi] = d.a <= d.b ? [d.a, d.b] : [d.b, d.a];
  vState.sel = d;
  openCreateDialog(Number(d.t), ymd(addDays(state.v.start, lo)), ymd(addDays(state.v.start, hi)));
  const clear = onModalClose;
  onModalClose = () => {
    if (clear) clear();
    vState.sel = null;
    vMarkSelection();
  };
}
