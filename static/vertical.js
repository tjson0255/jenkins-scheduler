/* 縦表示: 行=日付、列=レーン（カテゴリごとにまとめ、折りたたみ可）
 *
 * vis-timeline は縦向きに対応していないので、表（table）で描く。
 * state / visibleTargets / openPanel などは app.js のものを使う（関数は呼び出し時に解決される）。
 */
"use strict";

const LANE_W = 9; // スケジューラ1本分の縦帯の幅(px)
const V_MAX_RUNS_PER_CELL = 4;
// focusDay: 日付をクリックして選んだ日（null なら今日）。その行にスケジューラの見出しを出す
const vState = { collapsed: new Set(), scrollToToday: true, drag: null, wired: false, focusDay: null };

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
  vState.focusDay = null;
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
  const tip = [t.kind === "memo" ? "テキスト" : t.job_path, ...itemAlertMessages(t), "クリックで予定一覧"].join("\n");
  return `<th class="v-item-head${t.enabled ? "" : " disabled"}${lvl ? " alert-" + lvl : ""}" data-item="${t.id}" title="${esc(tip)}">
    <span class="swatch" style="background:${esc(t.color || "#8a94a6")}"></span><span class="v-item-name">${esc(t.display_name)}</span>${t.kind === "memo" ? '<span class="memo-icon" title="テキスト">📝</span>' : ""}${warn.join("")}
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
    // ラベルは開始日に出す。途中から見ても分かるよう、表示範囲の先頭行と選んだ日（既定は今日）の行にも「↑」付きで出す。
    // 選んだ日の行は、その日に動くもの（有効で、臨時のスケジューラに止められていないもの）だけにする
    const onFocus = day === vState.focus;
    const runsThatDay = s.mode === "memo" || (s.status === "active" && !stoppingScheduleOn(s, info.lane || [], day));
    if (onFocus ? runsThatDay : isStart || dayIdx === sp.from) {
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
    return `<span class="v-run rs-${runShownStatus(r)}" data-run="${r.id}" title="${runTooltip(r).replace(/<br>/g, "&#10;")}">${pad(d.getHours())}:${pad(d.getMinutes())}</span>`;
  });
  if (runs.length > V_MAX_RUNS_PER_CELL) chips.push(`<span class="v-more" data-item="${t.id}">+${runs.length - V_MAX_RUNS_PER_CELL}</span>`);
  const pad0 = info.lanes * LANE_W + 6;
  const lockTip = can.editItem(t) ? "" : ` title="${t.kind === "memo" ? "このレーンに予定を追加する権限がありません" : "Jenkins レーンへのスケジューラの追加は管理者のみです"}"`;
  return `<td class="v-cell${t.enabled ? "" : " disabled"}${can.editItem(t) ? "" : " locked"}" data-t="${t.id}" data-i="${dayIdx}"${lockTip}>
    ${stripes.join("")}
    <div class="v-content" style="padding-left:${pad0}px">${labels.join("")}${chips.length ? `<div class="v-runs">${chips.join("")}</div>` : ""}</div>
  </td>`;
}

function renderVertical() {
  const box = document.getElementById("vgrid");
  vWire(box);
  const days = state.v.days;
  const vt = visibleTargets();
  const showRuns = runsShown();
  document.getElementById("summary-note").textContent = runNote(showRuns);

  const cols = []; // {cat, items:[t]|null(collapsed)}
  for (const c of state.categories) {
    const children = vt.filter((t) => t.category_id === c.id);
    if (!children.length) continue;
    cols.push({ cat: c, items: children, collapsed: vState.collapsed.has(c.id) });
  }
  const spansByItem = new Map();
  for (const t of vt) {
    const lane = state.schedules.filter((s) => s.target_id === t.id);
    spansByItem.set(t.id, { ...vScheduleSpans(lane, days), lane });
  }
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
  vState.focus = vState.focusDay || todayStr;
  const rows = [];
  for (let i = 0; i < days; i++) {
    const d = addDays(state.v.start, i);
    const day = ymd(d);
    const dow = d.getDay();
    const showMonth = i === 0 || d.getDate() === 1;
    const cls = [dow === 0 ? "sun" : dow === 6 ? "sat" : "", day === todayStr ? "today" : "", day === vState.focus ? "focus" : "", d.getDate() === 1 ? "month-start" : ""].join(" ");
    const cells = cols.flatMap(({ cat, items, collapsed }) =>
      collapsed ? [vCollapsedCell(cat, items, day, runsByDay, showRuns)] : items.map((t) => vCell(t, i, day, spansByItem.get(t.id), runsByDay, showRuns))
    );
    rows.push(`<tr class="${cls}" data-day="${day}">
      <th class="v-date" scope="row" data-day="${day}" title="クリックでこの日のスケジューラを表示">${showMonth ? `<span class="v-month">${d.getFullYear()}/${d.getMonth() + 1}</span>` : ""}${d.getDate()}<span class="v-dow">(${WD[dow]})</span>${day === todayStr ? '<span class="v-today">今日</span>' : ""}</th>
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
    : '<p class="muted" style="padding:16px">表示するレーンがありません。アイテム画面で登録してください。</p>';

  if (vState.anchor) {
    // 前後の日付を読み足したときは、見ていた行を同じ位置に出す
    const row = box.querySelector(`tr[data-day="${vState.anchor.day}"]`);
    if (row) box.scrollTop = row.offsetTop - vState.anchor.offset;
    vState.anchor = null;
  } else if (vState.scrollToToday) {
    const row = box.querySelector("tr.focus");
    const headH = box.querySelector("thead") ? box.querySelector("thead").offsetHeight : 0;
    const need = row ? Math.max(0, row.offsetTop - headH) : 0;
    // 画面が縦に長くて、その日をいちばん上まで動かせないときは、先の日付を読み足してからにする
    if (row && need > box.scrollHeight - box.clientHeight && state.v.days < V_MAX_DAYS) {
      state.v.days = Math.min(state.v.days + V_STEP_DAYS, V_MAX_DAYS);
      loadData(true); // 読み終えたら、もう一度この処理でその日をいちばん上に出す
    } else {
      vState.scrollToToday = false;
      // 選んだ日（既定は今日）をいちばん上に出す。前の日は上にスクロールすると見られる
      box.scrollTop = need;
    }
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
/** 上端・下端の近くまでスクロールしたら、前後の日付を読み足す（見ている位置はそのまま）。
 *  重くならないよう、描く日数は V_MAX_DAYS までにして、反対側の端を落とす */
const V_STEP_DAYS = 28;
const V_MAX_DAYS = 182;
async function vMaybeExtend(box) {
  // 今日・選んだ日をいちばん上に出す処理が終わるまでは読み足さない（位置の基準がずれるため）
  if (vState.extending || vState.scrollToToday || state.layout !== "vertical" || vState.drag) return;
  const nearTop = box.scrollTop < 120;
  const nearBottom = box.scrollTop + box.clientHeight > box.scrollHeight - 120;
  if (!nearTop && !nearBottom) return;
  vState.extending = true;
  // いちばん上に見えている行を覚えておき、読み足した後も同じ位置に出す
  const headH = box.querySelector("thead") ? box.querySelector("thead").offsetHeight : 0;
  const anchor = [...box.querySelectorAll("tbody tr[data-day]")].find((r) => r.offsetTop + r.offsetHeight > box.scrollTop + headH);
  vState.anchor = anchor ? { day: anchor.dataset.day, offset: anchor.offsetTop - box.scrollTop } : null;
  if (nearTop) {
    state.v.start = addDays(state.v.start, -V_STEP_DAYS);
    state.v.days = Math.min(state.v.days + V_STEP_DAYS, V_MAX_DAYS);
  } else {
    state.v.days += V_STEP_DAYS;
    if (state.v.days > V_MAX_DAYS) {
      state.v.start = addDays(state.v.start, state.v.days - V_MAX_DAYS);
      state.v.days = V_MAX_DAYS;
    }
  }
  try {
    await loadData(true);
  } finally {
    vState.extending = false;
  }
}

function vWire(box) {
  if (vState.wired) return;
  vState.wired = true;
  box.addEventListener("scroll", debounce(() => vMaybeExtend(box), 80), { passive: true });

  box.addEventListener("click", (e) => {
    if (vState.justDragged) return;
    if (e.target.closest('[data-action="toggle-all"]')) return toggleAllCollapsed();
    const date = e.target.closest("th.v-date[data-day]");
    if (date) {
      // 選んだ日の行に、その日にかかっているスケジューラの見出しを出す（「今日」の印は今日のまま）
      vState.focusDay = date.dataset.day === ymd(new Date()) ? null : date.dataset.day;
      return renderVertical();
    }
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

  // 日付の列を上下にドラッグすると、表を上下にスクロールする（空きセルのドラッグは作成なので、日付の列で行う）
  box.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || !e.target.closest("th.v-date")) return;
    const y0 = e.clientY;
    const top0 = box.scrollTop;
    let moved = false;
    const move = (ev) => {
      if (Math.abs(ev.clientY - y0) > 4) moved = true;
      if (moved) box.scrollTop = top0 - (ev.clientY - y0);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", () => {
      window.removeEventListener("pointermove", move);
      if (moved) {
        // ドラッグした後の click（日付の選択）は無視する
        vState.justDragged = true;
        setTimeout(() => (vState.justDragged = false), 0);
      }
    }, { once: true });
  });

  // 空きセルを縦にドラッグして期間を選び、作成ダイアログを開く
  box.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    const cell = e.target.closest("td.v-cell");
    if (!cell || e.target.closest("[data-sched],[data-run],[data-item]")) return;
    // 追加できないレーンは何もしない（カーソルが「禁止」になり、マウスを乗せると理由が出る）
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
