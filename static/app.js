/* タイムライン画面（仕様書 11.1 / 11.2） */
"use strict";

const RUN_POINT_MAX_DAYS = 31; // これより広い表示範囲では run の点を描かず件数サマリーにする
// 凡例の項目ごとに、run の丸を表示・非表示できる
const RUN_GROUPS = [
  ["scheduled", "予定", ["scheduled"]],
  ["holding", "保留", ["holding"]],
  ["running", "キュー/実行中", ["queued", "running"]],
  ["success", "成功", ["success"]],
  ["unstable", "不安定", ["unstable"]],
  ["failure", "失敗/中断", ["failure", "aborted"]],
  ["missed", "スキップ/見逃し", ["skipped", "missed", "cancelled"]],
];
const state = {
  categories: [],
  targets: [],
  schedules: [],
  runs: [],
  loadedRange: null,
  dragging: false,
  panelScheduleId: null,
  panelTab: "basic",
  layout: "horizontal", // horizontal: 横軸=日付 / vertical: 行=日付・列=アイテム（vertical.js）
  runsOn: true, // 実行状況（run の丸）を表示するか
  hiddenRunGroups: new Set(), // 凡例で非表示にした状態
  v: { start: null, days: 28 }, // 縦表示の表示範囲
};

const groups = new vis.DataSet();
const items = new vis.DataSet();
let timeline;

// ログイン中の利用者（権限）を読み込んでから画面を作る
renderHeader("/").then(init);

function init() {
  try {
    state.layout = localStorage.getItem("layout") === "vertical" ? "vertical" : "horizontal";
    state.runsOn = localStorage.getItem("runsOn") !== "0";
    state.hiddenRunGroups = new Set(JSON.parse(localStorage.getItem("hiddenRunGroups") || "[]"));
  } catch (_) {}

  // ?date=YYYY-MM-DD で開くと、その日を中心に表示する（1日の予定からのリンク）
  const asked = new URLSearchParams(location.search).get("date");
  const today = /^\d{4}-\d{2}-\d{2}$/.test(asked || "") ? parseYmd(asked) : startOfDay(new Date());
  state.v.start = addDays(today, -7);
  const options = {
    locale: "ja",
    start: addDays(today, -14),
    end: addDays(today, 14),
    orientation: { axis: "top", item: "top" },
    stack: true,
    stackSubgroups: true,
    groupOrder: "order",
    zoomKey: "ctrlKey",
    zoomMin: 1000 * 60 * 60 * 6,
    zoomMax: 1000 * 60 * 60 * 24 * 400,
    verticalScroll: true,
    maxHeight: Math.max(300, window.innerHeight - 150),
    margin: { item: { horizontal: 2, vertical: 4 }, axis: 4 },
    selectable: true,
    multiselect: false,
    showCurrentTime: true,
    showWeekScale: true,
    editable: { updateTime: true, updateGroup: false, add: false, remove: false, overrideItems: false },
    snap: (date) => roundToDay(date),
    onMove: onItemMove,
    tooltip: { followMouse: true, overflowMethod: "cap" },
    format: { minorLabels: minorLabel, majorLabels: majorLabel },
    groupTemplate: groupTemplate,
    template: itemTemplate,
    xss: { disabled: true }, // テンプレート側で esc() 済み
  };
  timeline = new vis.Timeline(document.getElementById("timeline"), items, groups, options);

  timeline.on("rangechanged", debounce(() => state.layout === "horizontal" && !state.dragging && loadData(), 250));
  timeline.on("click", onTimelineClick);
  // カテゴリの折りたたみは vis がグループのデータ（showNested）を書き換えて行う。その結果を記憶する
  groups.on("update", (_e, props) => {
    for (const id of props.items) {
      const g = groups.get(id);
      if (g && g.kind === "category") {
        const catId = Number(String(id).slice(1));
        const collapsed = g.showNested === false;
        if (collapsed !== vState.collapsed.has(catId)) vSetCollapsed(catId, collapsed);
      }
    }
  });
  setupDragCreate();

  // ツールバーは横表示・縦表示で共通。縦表示のときは vertical.js 側の表示範囲を動かす
  const vertical = () => state.layout === "vertical";
  document.getElementById("btn-today").onclick = () => (vertical() ? vGoToday() : timeline.moveTo(new Date()));
  document.getElementById("btn-prev").onclick = () => (vertical() ? vShift(-0.5) : shiftWindow(-0.5));
  document.getElementById("btn-next").onclick = () => (vertical() ? vShift(0.5) : shiftWindow(0.5));
  document.getElementById("btn-zoom-in").onclick = () => (vertical() ? vSetDays(Math.round(state.v.days / 2)) : timeline.zoomIn(0.4));
  document.getElementById("btn-zoom-out").onclick = () => (vertical() ? vSetDays(state.v.days * 2) : timeline.zoomOut(0.4));
  document.querySelectorAll("[data-range]").forEach((b) => {
    b.onclick = () => {
      const days = Number(b.dataset.range);
      if (vertical()) return vSetDays(days);
      const w = timeline.getWindow();
      const center = new Date((w.start.getTime() + w.end.getTime()) / 2);
      timeline.setWindow(addDays(startOfDay(center), -Math.floor(days / 2)), addDays(startOfDay(center), Math.ceil(days / 2)));
    };
  });
  // カテゴリをまとめて開閉するボタンは、名前の列の上（タイムラインの左上）に置く
  document.getElementById("timeline").append(el("button", { type: "button", class: "collapse-toggle tl-corner", onclick: toggleAllCollapsed }));
  // 凡例の表示・非表示
  const legendToggle = document.getElementById("legend-toggle");
  let legendOn = true;
  try {
    legendOn = localStorage.getItem("legendOn") !== "0";
  } catch (_) {}
  const applyLegend = () => (document.getElementById("legend").hidden = !legendOn);
  legendToggle.checked = legendOn;
  legendToggle.onchange = () => {
    legendOn = legendToggle.checked;
    try {
      localStorage.setItem("legendOn", legendOn ? "1" : "0");
    } catch (_) {}
    applyLegend();
  };
  applyLegend();
  const runsToggle = document.getElementById("runs-toggle");
  runsToggle.checked = state.runsOn;
  runsToggle.onchange = () => {
    state.runsOn = runsToggle.checked;
    saveRunPrefs();
    renderLegend();
    loadData(); // 表示しないときは run を読み込まない
  };
  const toggle = document.getElementById("layout-toggle");
  toggle.checked = vertical();
  toggle.onchange = () => setLayout(toggle.checked ? "vertical" : "horizontal");
  applyLayout();
  renderLegend();
  loadData();
  openFromHash();
  window.addEventListener("hashchange", openFromHash);
  setInterval(() => !state.dragging && loadData(true), 15000);
}

/* ------------------------------------------------------------------ 時刻ヘルパ */
function startOfDay(d) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}
function roundToDay(date) {
  const d = new Date(date);
  const s = startOfDay(d);
  return d.getHours() >= 12 ? addDays(s, 1) : s;
}
/** 今の表示範囲（横表示は vis-timeline、縦表示は state.v） */
function viewWindow() {
  if (state.layout === "vertical") return { start: state.v.start, end: addDays(state.v.start, state.v.days) };
  return timeline.getWindow();
}
function windowDays() {
  const w = viewWindow();
  return (w.end - w.start) / 86400000;
}

function setLayout(layout) {
  if (layout === state.layout) return;
  if (layout === "vertical") {
    // 横表示で見ていた範囲を引き継ぐ
    const w = timeline.getWindow();
    state.v.start = startOfDay(w.start);
    state.v.days = Math.min(182, Math.max(7, Math.round((w.end - w.start) / 86400000)));
  }
  state.layout = layout;
  try {
    localStorage.setItem("layout", layout);
  } catch (_) {}
  applyLayout();
  if (layout === "horizontal") {
    // 非表示の間は幅が 0 として扱われているので、先に描き直してから範囲を合わせる
    timeline.redraw();
    timeline.setWindow(state.v.start, addDays(state.v.start, state.v.days), { animation: false });
  }
  render(); // 読み込みを待たずに、手元のデータと今の折りたたみ状態で描き直す
  loadData();
}

function applyLayout() {
  const v = state.layout === "vertical";
  document.getElementById("timeline").hidden = v;
  document.getElementById("vgrid").hidden = !v;
}
function shiftWindow(frac) {
  const w = timeline.getWindow();
  const delta = (w.end - w.start) * frac;
  timeline.setWindow(new Date(w.start.getTime() + delta), new Date(w.end.getTime() + delta));
}
function m2d(m) {
  return m && m.toDate ? m.toDate() : new Date(m);
}
function minorLabel(date, scale) {
  const d = m2d(date);
  switch (scale) {
    case "millisecond":
    case "second":
    case "minute":
      return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    case "hour":
      return `${d.getHours()}時`;
    case "weekday":
    case "day":
      return `${d.getDate()}(${WD[d.getDay()]})`;
    case "week":
      return `${d.getMonth() + 1}/${d.getDate()}`;
    case "month":
      return `${d.getMonth() + 1}月`;
    case "year":
      return `${d.getFullYear()}年`;
  }
  return "";
}
function majorLabel(date, scale) {
  const d = m2d(date);
  switch (scale) {
    case "millisecond":
    case "second":
    case "minute":
    case "hour":
      return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日(${WD[d.getDay()]})`;
    case "weekday":
    case "day":
    case "week":
      return `${d.getFullYear()}年${d.getMonth() + 1}月`;
    case "month":
      return `${d.getFullYear()}年`;
  }
  return "";
}

/* ------------------------------------------------------------------ データ読み込み */
async function loadData(quiet) {
  const w = viewWindow();
  const span = w.end - w.start;
  const from = new Date(w.start.getTime() - span * 0.5);
  const to = new Date(w.end.getTime() + span * 0.5);
  const q = `from=${ymd(from)}&to=${ymd(to)}`;
  try {
    const wantRuns = state.runsOn && windowDays() <= RUN_POINT_MAX_DAYS;
    const [categories, targets, schedules, runs] = await Promise.all([
      api("GET", "/api/categories"),
      api("GET", "/api/targets"),
      api("GET", `/api/schedules?${q}`),
      wantRuns ? api("GET", `/api/runs?${q}&limit=5000`) : Promise.resolve([]),
    ]);
    Object.assign(state, { categories, targets, schedules, runs, loadedRange: { from, to } });
    render();
    if (state.panelScheduleId && quiet) refreshPanelLight();
  } catch (e) {
    if (!quiet) toast("読み込みに失敗しました: " + e.message, "error");
  }
}

/* ------------------------------------------------------------------ 描画 */
/** 表示するアイテム（すべてのアイテムをいつも表示する） */
function visibleTargets() {
  return state.targets;
}

function render() {
  if (state.layout === "vertical") return renderVertical();
  const vt = visibleTargets();
  const visibleIds = new Set(vt.map((t) => t.id));
  const newGroups = [];
  state.categories.forEach((c, ci) => {
    const children = vt.filter((t) => t.category_id === c.id);
    if (!children.length) return;
    newGroups.push({
      id: "c" + c.id,
      kind: "category",
      name: c.name,
      count: children.length,
      order: ci * 1000,
      nestedGroups: children.map((t) => "t" + t.id),
      // 折りたたみ状態は縦表示と共通（vertical.js の vState.collapsed、ブラウザに記憶）
      showNested: !vState.collapsed.has(c.id),
      className: "grp-category",
    });
    children.forEach((t, ti) => {
      newGroups.push({
        id: "t" + t.id,
        kind: "target",
        target: t,
        order: ci * 1000 + ti + 1,
        className: "grp-target" + (t.enabled ? "" : " disabled") + (itemAlertLevel(t) ? " alert-" + itemAlertLevel(t) : ""),
        treeLevel: 2,
        // vis は折りたたみ時に子の行へ visible:false を書き込むので、展開状態に合わせて毎回指定し直す
        visible: !vState.collapsed.has(c.id),
        // バーは段組み、run の点は1行に重ねて表示する
        subgroupStack: { s: true, r: false },
        subgroupOrder: (a, b) => a.subgroupOrder - b.subgroupOrder,
      });
    });
  });
  syncDataSet(groups, newGroups);

  const w = timeline.getWindow();
  const farEnd = addDays(state.loadedRange ? state.loadedRange.to : w.end, 30);
  const showRuns = windowDays() <= RUN_POINT_MAX_DAYS;
  const targetById = Object.fromEntries(state.targets.map((t) => [t.id, t]));
  const newItems = [];

  // 土日の背景
  for (let d = startOfDay(state.loadedRange ? state.loadedRange.from : w.start); d < (state.loadedRange ? state.loadedRange.to : w.end); d = addDays(d, 1)) {
    if (d.getDay() === 0 || d.getDay() === 6) {
      newItems.push({ id: "bg" + ymd(d), type: "background", start: d, end: addDays(d, 1), className: d.getDay() === 0 ? "bg-sun" : "bg-sat", editable: false });
    }
  }

  for (const s of state.schedules) {
    if (!visibleIds.has(s.target_id)) continue;
    const t = targetById[s.target_id];
    const start = parseYmd(s.start_date);
    const end = s.end_date ? addDays(parseYmd(s.end_date), 1) : new Date(Math.max(farEnd, addDays(start, 1)));
    const editable = ["draft", "active", "paused"].includes(s.status) && can.editItem(t);
    newItems.push({
      id: "s" + s.id,
      kind: "schedule",
      schedule: s,
      target: t,
      group: "t" + s.target_id,
      subgroup: "s",
      subgroupOrder: 0,
      start,
      end,
      type: "range",
      showRuns,
      className: `sched st-${s.status}${s.mode === "memo" ? " memo" + (s.end_date && s.end_date < ymd(new Date()) ? " past" : "") : ""}${scheduleAlertLevel(s) ? " has-" + scheduleAlertLevel(s) : ""}${s.end_date ? "" : " infinite"}${state.panelScheduleId === s.id ? " is-open" : ""}`,
      style: t && t.color ? `--c:${t.color}` : "",
      editable: editable ? { updateTime: true, updateGroup: false, remove: false } : false,
      title: scheduleTooltip(s),
    });
  }
  if (showRuns) {
    for (const r of state.runs) {
      if (!visibleIds.has(r.target_id) || !runVisible(r)) continue;
      newItems.push({
        id: "r" + r.id,
        kind: "run",
        run: r,
        group: "t" + r.target_id,
        subgroup: "r",
        subgroupOrder: 1,
        start: new Date(r.scheduled_at),
        type: "point",
        className: `run rs-${r.status}`,
        editable: false,
        title: runTooltip(r),
      });
    }
  }
  document.getElementById("summary-note").textContent = runNote(showRuns);
  syncDataSet(items, newItems);
  updateCollapseButtons();
}

function syncDataSet(ds, list) {
  const ids = new Set(list.map((x) => x.id));
  const remove = ds.getIds().filter((id) => !ids.has(id));
  if (remove.length) ds.remove(remove);
  ds.update(list);
}

function groupTemplate(g) {
  if (!g) return "";
  if (g.kind === "category") return `<span class="grp-cat-name">${esc(g.name)}</span>`;
  const t = g.target;
  if (t.kind === "memo") {
    return `<div class="grp-target-inner" title="予定">
      <span class="swatch" style="background:${esc(t.color || "#8a94a6")}"></span>
      <span class="grp-name">${esc(t.display_name)}</span><span class="memo-icon">📝</span>
    </div>`;
  }
  const warn = [];
  if (t.schema_error) warn.push(`<span class="warn-icon err" title="${esc(t.schema_error)}">⛔</span>`);
  if (t.timer_trigger_detected) warn.push(`<span class="warn-icon" title="Jenkins 側の cron が残っています">⏰</span>`);
  if (t.issue_counts && t.issue_counts.error) warn.push(`<span class="warn-icon err" title="パラメータ定義のエラー ${t.issue_counts.error} 件">●</span>`);
  else if (t.issue_counts && t.issue_counts.warning) warn.push(`<span class="warn-icon warn" title="パラメータ定義の警告 ${t.issue_counts.warning} 件">●</span>`);
  const alerts = itemAlertMessages(t);
  return `<div class="grp-target-inner" title="${esc([t.job_path + (t.enabled ? "" : "（無効）"), ...alerts].join("\n"))}">
    <span class="swatch" style="background:${esc(t.color || "#8a94a6")}"></span>
    <span class="grp-name">${esc(t.display_name)}</span>${warn.join("")}
  </div>`;
}

function itemTemplate(item) {
  if (item && item.id === "__new") return esc(item.content);
  if (!item || item.kind !== "schedule") return "";
  const s = item.schedule;
  if (s.mode === "memo") {
    const line = firstLine(s.note);
    const sub = s.label && line ? ` <span class="sub">${esc(line)}</span>` : "";
    return `<span class="ttl">${esc(scheduleTitle(s))}</span>${sub}${s.end_date ? "" : ' <span class="inf">∞</span>'}`;
  }
  const title = s.label || (s.mode === "cron" ? s.cron_summary || s.cron_expr : "1回: " + fmtDateTime(s.once_at));
  const sub = s.label && s.mode === "cron" ? ` <span class="sub">${esc(s.cron_summary || s.cron_expr)}</span>` : "";
  const lvl = scheduleAlertLevel(s);
  const badge = lvl ? `<span class="badge-dot ${lvl}" title="${esc(scheduleAlertMessages(s).join("\n"))}">!</span>` : "";
  const hold = s.holding_count ? `<span class="hold-tag">保留${s.holding_count}</span>` : "";
  let counts = "";
  if (!item.showRuns) {
    const c = s.run_counts || {};
    const parts = [];
    const ok = (c.success || 0) + (c.unstable || 0);
    if (c.scheduled) parts.push(`予定${c.scheduled}`);
    if (ok) parts.push(`成功${ok}`);
    if (c.failure) parts.push(`<b class="red">失敗${c.failure}</b>`);
    if (c.holding) parts.push(`<b class="orange">保留${c.holding}</b>`);
    if ((c.missed || 0) + (c.skipped || 0)) parts.push(`見逃し/スキップ${(c.missed || 0) + (c.skipped || 0)}`);
    if (parts.length) counts = ` <span class="counts">${parts.join(" ")}</span>`;
  }
  const status = s.status !== "active" ? `<span class="st-tag">${SCHEDULE_STATUS_LABEL[s.status]}</span>` : "";
  return `${badge}${hold}${status}<span class="ttl">${esc(title)}</span>${sub}${s.end_date ? "" : ' <span class="inf">∞</span>'}${counts}`;
}

function scheduleTooltip(s) {
  if (s.mode === "memo") {
    const memo = [scheduleTitle(s), `${fmtDate(s.start_date)} 〜 ${s.end_date ? fmtDate(s.end_date) : "無期限"}`, s.label ? s.note || "" : (s.note || "").split("\n").slice(1).join("\n")];
    return esc(memo.filter(Boolean).join("\n")).replace(/\n/g, "<br>");
  }
  const lines = [
    `${s.label || "(ラベルなし)"} [${SCHEDULE_STATUS_LABEL[s.status]}]`,
    `${fmtDate(s.start_date)} 〜 ${s.end_date ? fmtDate(s.end_date) : "無期限"}`,
    s.mode === "cron" ? `${s.cron_summary}（${s.cron_expr}）` : `1回: ${fmtDateTime(s.once_at, true)}`,
    s.next_run_at ? `次回: ${fmtDateTime(s.next_run_at, true)}` : "",
    ...scheduleAlertMessages(s).map((m) => "⚠ " + m),
  ];
  return esc(lines.filter(Boolean).join("\n")).replace(/\n/g, "<br>");
}

function runTooltip(r) {
  const lines = [`${fmtDateTime(r.scheduled_at, true)} ${RUN_STATUS_LABEL[r.status]}`, r.build_number ? `#${r.build_number}` : "", r.reason || ""];
  return esc(lines.filter(Boolean).join("\n")).replace(/\n/g, "<br>");
}

/** その run の丸を表示するか（実行状況のスイッチと、凡例での状態ごとの切り替え） */
function runVisible(r) {
  if (!state.runsOn) return false;
  const g = RUN_GROUPS.find(([, , statuses]) => statuses.includes(r.status));
  return !g || !state.hiddenRunGroups.has(g[0]);
}

function runNote(withinRange) {
  if (!state.runsOn) return "";
  if (!withinRange) return `表示範囲が ${RUN_POINT_MAX_DAYS} 日を超えているため、run は件数サマリーのみ表示しています。`;
  if (state.hiddenRunGroups.size) {
    const names = RUN_GROUPS.filter(([k]) => state.hiddenRunGroups.has(k)).map(([, label]) => label);
    return `凡例で非表示にしている状態: ${names.join("、")}（凡例を押すと戻ります）`;
  }
  return "";
}

function saveRunPrefs() {
  try {
    localStorage.setItem("runsOn", state.runsOn ? "1" : "0");
    localStorage.setItem("hiddenRunGroups", JSON.stringify([...state.hiddenRunGroups]));
  } catch (_) {}
}

function renderLegend() {
  document.getElementById("legend").replaceChildren(
    el("span", { class: "legend-item" }, el("span", { class: "legend-bar draft" }), "ドラフト"),
    el("span", { class: "legend-item", title: "要確認（パラメータ定義の変更、Jenkins 側の cron の残存など）" }, el("span", { class: "legend-bar hatch-warning" }), "警告"),
    el("span", { class: "legend-item", title: "キックされない（パラメータ定義のエラー、ジョブが無い、保留中の run がある）" }, el("span", { class: "legend-bar hatch-error" }), "エラー"),
    el("span", { class: "legend-sep" }),
    ...RUN_GROUPS.map(([key, label]) => {
      const off = state.hiddenRunGroups.has(key);
      return el("button", {
        type: "button",
        class: `legend-item legend-toggle${off ? " off" : ""}`,
        "aria-pressed": String(!off),
        disabled: !state.runsOn,
        title: state.runsOn ? `${label}の丸を${off ? "表示する" : "隠す"}` : "実行状況がオフです",
        onclick: () => {
          off ? state.hiddenRunGroups.delete(key) : state.hiddenRunGroups.add(key);
          saveRunPrefs();
          renderLegend();
          render();
        },
      }, el("span", { class: `legend-dot rs-${key}` }), label);
    })
  );
}

/* ------------------------------------------------------------------ 操作 */
function onTimelineClick(props) {
  if (props.what === "group-label" && props.group) {
    const gid = String(props.group);
    // アイテム行は予定一覧を開く
    if (gid.startsWith("t")) openItemPanel(Number(gid.slice(1)));
    return;
  }
  if (!props.item) return;
  const it = items.get(props.item);
  if (!it) return;
  if (it.kind === "schedule") openPanel(it.schedule.id, "basic");
  else if (it.kind === "run") {
    if (it.run.schedule_id) openPanel(it.run.schedule_id, "runs", it.run.id);
    else showRunModal(it.run);
  }
}

async function onItemMove(item, callback) {
  const s = item.schedule;
  const newStart = startOfDay(item.start);
  const newEndExcl = startOfDay(item.end);
  const origStart = parseYmd(s.start_date);
  const payload = {};
  if (newStart.getTime() !== origStart.getTime()) payload.start_date = ymd(newStart);
  if (s.end_date) {
    const endIncl = addDays(newEndExcl, -1);
    if (endIncl < newStart) return callback(null);
    if (ymd(endIncl) !== s.end_date) payload.end_date = ymd(endIncl);
  } else if (!payload.start_date) {
    // 無期限バーの右端をリサイズ → 終了日を設定する
    const endIncl = addDays(newEndExcl, -1);
    if (endIncl < newStart) return callback(null);
    const ok = await confirmDialog("終了日の設定", `無期限のスケジュールに終了日 ${fmtDate(ymd(endIncl))} を設定しますか？`, "設定する");
    if (!ok) return callback(null);
    payload.end_date = ymd(endIncl);
  }
  if (!Object.keys(payload).length) return callback(null);
  try {
    await api("PATCH", `/api/schedules/${s.id}`, { ...payload, revision: s.revision });
    callback(item);
    toast("期間を変更しました（未実行の run を再生成しました）");
    await loadData();
    if (state.panelScheduleId === s.id) openPanel(s.id, state.panelTab);
  } catch (e) {
    callback(null);
    toast(e.message, "error");
    if (isConflict(e)) loadData(); // 他の人の変更を読み込み直す
  }
}

function setupDragCreate() {
  const container = document.getElementById("timeline");
  let drag = null;

  container.addEventListener(
    "pointerdown",
    (e) => {
      if (e.button !== 0 || e.shiftKey || e.ctrlKey || e.metaKey) return;
      const props = timeline.getEventProperties(e);
      if (props.what !== "background" || !props.group || !String(props.group).startsWith("t") || props.item) return;
      if (!can.editItem(state.targets.find((x) => "t" + x.id === String(props.group)))) return;
      e.stopPropagation();
      e.preventDefault();
      drag = { group: props.group, anchor: startOfDay(props.time), x: e.clientX, moved: false };
      state.dragging = true;
      window.addEventListener("pointermove", onMove, true);
      window.addEventListener("pointerup", onUp, true);
    },
    true
  );

  function onMove(e) {
    if (!drag) return;
    if (Math.abs(e.clientX - drag.x) > 5) drag.moved = true;
    if (!drag.moved) return;
    const t = startOfDay(timeline.getEventProperties(e).time);
    const start = t < drag.anchor ? t : drag.anchor;
    const end = addDays(t < drag.anchor ? drag.anchor : t, 1);
    drag.start = start;
    drag.end = end;
    items.update({ id: "__new", group: drag.group, subgroup: "s", subgroupOrder: 0, start, end, type: "range", className: "sched new-range", content: `${fmtDate(ymd(start))} 〜 ${fmtDate(ymd(addDays(end, -1)))}`, editable: false });
  }

  function onUp() {
    window.removeEventListener("pointermove", onMove, true);
    window.removeEventListener("pointerup", onUp, true);
    const d = drag;
    drag = null;
    state.dragging = false;
    if (!d || !d.moved || !d.start) {
      items.remove("__new");
      return;
    }
    const targetId = Number(String(d.group).slice(1));
    openCreateDialog(targetId, ymd(d.start), ymd(addDays(d.end, -1)));
  }
}

function openCreateDialog(targetId, startDate, endDate) {
  const target = state.targets.find((t) => t.id === targetId);
  if (!can.editItem(target)) return toast("このアイテムに予定を追加する権限がありません", "error");
  if (target && target.kind === "memo") return openMemoDialog(targetId, startDate, endDate);
  const form = scheduleForm(
    { target_id: targetId, start_date: startDate, end_date: endDate, mode: "cron", cron_expr: "0 3 * * *" },
    { targets: state.targets.filter((t) => t.kind !== "memo") }
  );
  const submit = async (activate) => {
    const v = form.value();
    try {
      const s = await api("POST", "/api/schedules", { ...v, activate });
      closeModal(); // 仮表示の範囲・縦表示の選択はダイアログを閉じたときの処理で片付ける
      toast(activate ? "スケジュールを作成して有効化しました" : "ドラフトとして保存しました");
      items.remove("__new");
      await loadData();
      openPanel(s.id, "basic");
    } catch (e) {
      toast(e.message, "error");
    }
  };
  openModal("スケジュールの作成", form.root, [
    el("button", { class: "btn", onclick: closeModal }, "やめる"),
    el("button", { class: "btn", onclick: () => submit(false) }, "ドラフトで保存"),
    el("button", { class: "btn primary", onclick: () => submit(true) }, "保存して有効化"),
  ]);
  onModalClose = () => items.remove("__new");
}

/* ------------------------------------------------------------------ 詳細パネル */
const panel = document.getElementById("panel");

function closePanel() {
  state.panelScheduleId = null;
  state.panelItemId = null;
  panel.classList.add("hidden");
  panel.replaceChildren();
  render();
}

async function openPanel(id, tab, focusRunId) {
  state.panelScheduleId = id;
  state.panelItemId = null;
  state.panelTab = tab || state.panelTab || "basic";
  panel.classList.remove("hidden");
  let s;
  try {
    s = await api("GET", `/api/schedules/${id}`);
  } catch (e) {
    toast(e.message, "error");
    return closePanel();
  }
  const t = state.targets.find((x) => x.id === s.target_id) || {};
  render();
  if (s.mode === "memo") return renderMemoPanel(s, t);

  const tabs = [
    ["basic", "基本情報"],
    ["params", "パラメータ"],
    ["runs", "実行履歴"],
  ];
  const body = el("div", { class: "panel-body" });
  const tabBar = el(
    "div",
    { class: "tabs" },
    tabs.map(([k, l]) =>
      el("button", { class: "tab" + (k === state.panelTab ? " active" : ""), onclick: () => openPanel(id, k) }, l, k === "params" && s.issue_level ? el("span", { class: `badge-dot ${s.issue_level}` }, "!") : null)
    )
  );

  panel.replaceChildren(
    el("div", { class: "panel-header" },
      el("div", {},
        el("a", { class: "small back-link", href: "#", title: "このアイテムの予定一覧", onclick: (e) => { e.preventDefault(); openItemPanel(s.target_id); } }, `← ${t.display_name || ""}（${t.job_path || ""}）の予定一覧`),
        el("h2", {}, s.label || (s.mode === "cron" ? s.cron_summary : "スケジュール #" + s.id), " ", statusChip(s.status))),
      el("button", { class: "icon-btn", title: "閉じる", onclick: closePanel }, "×")),
    can.admin() ? actionBar(s, t) : readonlyNote("このスケジュールは閲覧のみです"),
    s.holding_count
      ? el("div", { class: "holding-note" }, `⛔ 保留中の run が ${s.holding_count} 件あります（キックされずに止まっています）。`,
          el("a", { href: "#", onclick: (e) => { e.preventDefault(); openPanel(s.id, "runs"); } }, "実行履歴で確認"))
      : null,
    issueList((s.issues || []).filter((i) => i.level !== "info")),
    tabBar,
    body
  );
  if (state.panelTab === "basic") renderBasicTab(body, s);
  else if (state.panelTab === "params") renderParamsTab(body, s);
  else renderRunsTab(body, s, focusRunId);
}

async function refreshPanelLight() {
  // 実行履歴タブは自動更新する
  if (state.panelTab === "runs" && state.panelScheduleId && !document.getElementById("modal")) {
    const body = panel.querySelector(".panel-body");
    const s = state.schedules.find((x) => x.id === state.panelScheduleId);
    if (body && s) renderRunsTab(body, s);
  }
}

function actionBar(s, t) {
  const act = (label, path, opts) =>
    el("button", {
      class: "btn" + (opts && opts.cls ? " " + opts.cls : ""),
      onclick: async () => {
        if (opts && opts.confirm && !(await confirmDialog(label, opts.confirm, label, opts.cls === "danger"))) return;
        try {
          if (opts && opts.method === "DELETE") {
            await api("DELETE", path);
            toast("削除しました");
            await loadData();
            return closePanel();
          }
          await api("POST", path);
          toast(`${label}しました`);
          await loadData();
          openPanel(s.id, state.panelTab);
        } catch (e) {
          toast(e.message, "error");
        }
      },
    }, label);
  const bar = el("div", { class: "actions" });
  if (s.status === "draft") {
    bar.append(act("有効化", `/api/schedules/${s.id}/activate`, { cls: "primary", confirm: "有効化すると、以降の run は予定時刻に自動でキックされます。よろしいですか？" }));
  }
  if (s.status === "active") bar.append(act("一時停止", `/api/schedules/${s.id}/pause`));
  if (s.status === "paused") bar.append(act("再開", `/api/schedules/${s.id}/resume`, { cls: "primary", confirm: "再開します。一時停止中に予定時刻を過ぎた run は遅延時の扱い（missed_policy）に従います。" }));
  bar.append(
    el("button", { class: "btn", onclick: () => doDryRun(s) }, "ドライラン"),
    el("button", {
      class: "btn",
      onclick: async () => {
        if (!(await confirmDialog("今すぐ実行", `${t.display_name} を、このスケジュールのパラメータで今すぐキックします。`, "キックする"))) return;
        try {
          const r = await api("POST", `/api/targets/${s.target_id}/run-now`, { schedule_id: s.id });
          toast(`キックしました: ${RUN_STATUS_LABEL[r.status]}${r.reason ? "（" + r.reason + "）" : ""}`, r.status === "holding" || r.status === "skipped" ? "error" : "");
          loadData();
        } catch (e) {
          toast(e.message, "error");
        }
      },
    }, "今すぐ実行")
  );
  if (["draft", "active", "paused"].includes(s.status)) {
    bar.append(act("キャンセル", `/api/schedules/${s.id}/cancel`, { cls: "danger", confirm: "スケジュールをキャンセルします。未実行の run はすべてキャンセルされます（元に戻せません）。" }));
  }
  if (s.status === "draft") bar.append(act("削除", `/api/schedules/${s.id}`, { cls: "danger", method: "DELETE", confirm: "ドラフトを削除します。" }));
  return bar;
}

function renderBasicTab(body, s) {
  const editable = ["draft", "active", "paused", "ended"].includes(s.status);
  const form = scheduleForm(s);
  const info = el("dl", { class: "kv" },
    el("dt", {}, "次回"), el("dd", {}, s.next_run_at ? fmtDateTime(s.next_run_at, true) : "—"),
    el("dt", {}, "パラメータ上書き"), el("dd", {}, `${s.override_count} 件${s.params_pinned ? "（デフォルト値変更に追従しない）" : ""}`),
    el("dt", {}, "作成 / 更新"), el("dd", {}, `${fmtDateTime(s.created_at, true)} / ${fmtDateTime(s.updated_at, true)}`));
  const save = el("button", {
    class: "btn primary",
    disabled: !editable,
    onclick: async () => {
      try {
        await api("PATCH", `/api/schedules/${s.id}`, { ...form.value(), revision: s.revision });
        toast("保存しました（期間・規則を変えた場合は未実行の run を再生成しました）");
        await loadData();
        openPanel(s.id, "basic");
      } catch (e) {
        toast(e.message, "error");
        if (isConflict(e)) {
          await loadData();
          openPanel(s.id, "basic");
        }
      }
    },
  }, "保存");
  if (!can.admin()) {
    body.replaceChildren(info, lockForm(form.root));
    return;
  }
  body.replaceChildren(info, form.root, el("div", { class: "actions end" }, save));
}

async function renderParamsTab(body, s) {
  body.replaceChildren(el("div", { class: "muted" }, "Jenkins から最新のパラメータ定義を取得しています…"));
  let p;
  try {
    p = await api("GET", `/api/schedules/${s.id}/params`);
  } catch (e) {
    body.replaceChildren(el("div", { class: "error-text" }, e.message));
    return;
  }
  const rows = [];
  const inputs = {};
  for (const f of p.fields) {
    const overridden = f.override !== null && f.override !== undefined;
    const cb = el("input", { type: "checkbox", checked: overridden, title: "上書きする" });
    let input;
    const val = overridden ? f.override : f.pinned ?? f.default;
    if (f.kind === "boolean") {
      input = el("select", {}, el("option", { value: "true", selected: String(val) === "true" }, "true"), el("option", { value: "false", selected: String(val) !== "true" }, "false"));
    } else if (f.kind === "choice") {
      const choices = [...(f.choices || [])];
      if (overridden && !choices.includes(val) && !String(val).includes("{{")) choices.push(val);
      input = el("select", {}, choices.map((c) => el("option", { value: c, selected: c === val }, c === "" ? "（空）" : c)));
    } else if (f.kind === "text") {
      input = el("textarea", { rows: 3 }, val ?? "");
    } else {
      input = el("input", { type: f.kind === "password" ? "password" : "text", value: val ?? "" });
    }
    input.classList.add("param-input");
    const sync = () => input.classList.toggle("is-default", !cb.checked);
    input.addEventListener("input", () => {
      cb.checked = input.value !== f.default;
      sync();
    });
    input.addEventListener("change", () => {
      cb.checked = input.value !== f.default;
      sync();
    });
    cb.addEventListener("change", () => {
      if (!cb.checked) {
        if (input.tagName === "TEXTAREA") input.value = f.default;
        else input.value = f.default;
      }
      sync();
    });
    sync();
    inputs[f.name] = { cb, input };
    const fieldIssues = (p.issues || []).filter((i) => i.param === f.name && i.level !== "info");
    rows.push(
      el("div", { class: "param" + (fieldIssues.length ? " has-" + fieldIssues[0].level : "") },
        el("div", { class: "param-head" },
          el("label", { class: "check" }, cb, el("b", {}, f.name)),
          el("span", { class: "muted small" }, f.type.replace("ParameterDefinition", "")),
          f.description ? el("span", { class: "muted small" }, f.description) : null),
        input,
        el("div", { class: "param-foot muted small" },
          "デフォルト: ", el("code", {}, f.default === "" ? "（空）" : f.default),
          f.pinned !== null && f.pinned !== undefined ? el("span", {}, " ／ 控えたデフォルト値: ", el("code", {}, f.pinned)) : null,
          " ／ 展開後: ", el("code", { class: "preview" }, f.preview === "" ? "（空）" : f.preview)),
        issueList(fieldIssues))
    );
  }
  const orphan = Object.entries(p.orphan_overrides || {});
  const ctx = p.context;
  const vars = el("details", { class: "vars" },
    el("summary", {}, "使える変数"),
    el("table", { class: "table small" },
      el("tbody", {}, [
        ["{{schedule.label}}", ctx.schedule.label],
        ["{{schedule.start_date}}", ctx.schedule.start_date],
        ["{{schedule.end_date}}", ctx.schedule.end_date],
        ["{{run.scheduled_at}}", ctx.run.scheduled_at],
        ["{{run.date}}", ctx.run.date],
        ["{{target.job_path}}", ctx.target.job_path],
      ].map(([k, v]) => el("tr", {}, el("td", {}, el("code", {}, k)), el("td", {}, v || "（空）"))))),
    el("div", { class: "muted small" }, `プレビューは ${fmtDateTime(p.context_at, true)} の run として展開しています。`));

  const save = el("button", {
    class: "btn primary",
    onclick: async () => {
      const overrides = {};
      for (const [name, { cb, input }] of Object.entries(inputs)) if (cb.checked) overrides[name] = input.value;
      for (const [name, v] of orphan) if (keepOrphans.checked) overrides[name] = v;
      try {
        await api("PUT", `/api/schedules/${s.id}/params`, { overrides, revision: s.revision });
        toast("パラメータを保存しました");
        await loadData();
        openPanel(s.id, "params");
      } catch (e) {
        toast(e.message, "error");
        if (isConflict(e)) {
          await loadData();
          openPanel(s.id, "params");
        }
      }
    },
  }, "保存（Jenkins の現在の定義を確認済みにする）");
  const keepOrphans = el("input", { type: "checkbox", checked: true });
  body.replaceChildren(
    p.fresh ? null : el("p", { class: "muted small" },
      `Jenkins から最後に取得した定義を表示しています${p.fetched_at ? `（${fmtDateTime(p.fetched_at, true)}、5分ごとに自動取得）` : ""}。`),
    p.job_error ? el("div", { class: "error-text" }, p.job_error) : null,
    issueList(p.issues.filter((i) => !i.param || !p.fields.some((f) => f.name === i.param))),
    p.fields.length ? null : el("p", { class: "muted" }, "このジョブにはパラメータがありません（/build でキックします）。"),
    ...rows,
    orphan.length
      ? el("div", { class: "param has-warning" },
          el("b", {}, "Jenkins から削除されたパラメータの上書き値"),
          el("ul", {}, orphan.map(([k, v]) => el("li", {}, el("code", {}, k), " = ", el("code", {}, v)))),
          el("label", { class: "check" }, keepOrphans, " 上書き値を残す（送信はしません）"))
      : null,
    vars,
    can.admin() ? el("div", { class: "actions end" }, save) : null
  );
  if (!can.admin()) lockForm(body);
}

async function renderRunsTab(body, s, focusRunId) {
  let runs;
  try {
    runs = await api("GET", `/api/runs?schedule_id=${s.id}&limit=300`);
  } catch (e) {
    body.replaceChildren(el("div", { class: "error-text" }, e.message));
    return;
  }
  const sorted = runs.slice().sort((a, b) => {
    // 直近の予定を上に、それ以外は新しい順
    const pa = ["scheduled", "holding"].includes(a.status) ? 0 : 1;
    const pb = ["scheduled", "holding"].includes(b.status) ? 0 : 1;
    if (pa !== pb) return pa - pb;
    return pa === 0 ? new Date(a.scheduled_at) - new Date(b.scheduled_at) : new Date(b.scheduled_at) - new Date(a.scheduled_at);
  });
  const table = el("table", { class: "table runs" },
    el("thead", {}, el("tr", {}, el("th", {}, "予定日時"), el("th", {}, "状態"), el("th", {}, "理由 / ビルド"), el("th", {}, ""))),
    el("tbody", {}, sorted.map((r) => runRow(r, () => renderRunsTab(body, s)))));
  body.replaceChildren(runs.length ? table : el("p", { class: "muted" }, "run はまだありません。"));
  if (focusRunId) {
    const row = body.querySelector(`[data-run="${focusRunId}"]`);
    if (row) {
      row.classList.add("focus");
      row.scrollIntoView({ block: "center" });
    }
  }
}

function runRow(r, refresh) {
  const btn = (label, path, cls) =>
    el("button", {
      class: "btn small " + (cls || ""),
      onclick: async () => {
        try {
          const res = await api("POST", path);
          toast(`${label}: ${RUN_STATUS_LABEL[res.status] || ""}`);
          await loadData();
          refresh();
        } catch (e) {
          toast(e.message, "error");
          refresh();
        }
      },
    }, label);
  const ops = el("div", { class: "row" });
  if (!can.admin()) {
    ops.append(el("button", { class: "btn small", onclick: () => showRunModal(r) }, "詳細"));
  } else if (r.status === "holding") ops.append(btn("保留解除", `/api/runs/${r.id}/release-hold`, "primary"));
  if (can.admin()) {
    if (["scheduled", "holding"].includes(r.status)) ops.append(btn("スキップ", `/api/runs/${r.id}/skip`));
    if (["failure", "unstable", "aborted", "success"].includes(r.status) && r.params) ops.append(btn("再実行", `/api/runs/${r.id}/retry`));
    ops.append(el("button", { class: "btn small", onclick: () => showRunModal(r) }, "詳細"));
  }
  return el("tr", { "data-run": r.id },
    el("td", {}, fmtDateTime(r.scheduled_at, true)),
    el("td", {}, runChip(r.status)),
    el("td", { class: "small" },
      r.reason ? el("div", {}, r.reason) : null,
      r.build_url ? el("a", { href: r.build_url, target: "_blank", rel: "noopener" }, `#${r.build_number}`) : null),
    el("td", {}, ops));
}

function showRunModal(r) {
  openModal(`run #${r.id}`, el("div", {},
    el("dl", { class: "kv" },
      el("dt", {}, "予定日時"), el("dd", {}, fmtDateTime(r.scheduled_at, true)),
      el("dt", {}, "状態"), el("dd", {}, runChip(r.status)),
      el("dt", {}, "理由"), el("dd", { class: "pre" }, r.reason || "—"),
      el("dt", {}, "キック"), el("dd", {}, r.triggered_at ? fmtDateTime(r.triggered_at, true) : "—"),
      el("dt", {}, "終了"), el("dd", {}, r.finished_at ? fmtDateTime(r.finished_at, true) : "—"),
      el("dt", {}, "ビルド"), el("dd", {}, r.build_url ? el("a", { href: r.build_url, target: "_blank", rel: "noopener" }, `#${r.build_number}`) : "—"),
      el("dt", {}, "キューID"), el("dd", {}, r.queue_id ?? "—"),
      el("dt", {}, "定義のハッシュ"), el("dd", { class: "mono small" }, r.schema_hash ? r.schema_hash.slice(0, 12) : "—"),
      r.retry_of_id ? el("dt", {}, "再実行元") : null, r.retry_of_id ? el("dd", {}, `run #${r.retry_of_id}`) : null),
    el("h3", {}, "送信したパラメータ"),
    r.params && Object.keys(r.params).length
      ? el("table", { class: "table small" }, el("tbody", {}, Object.entries(r.params).map(([k, v]) => el("tr", {}, el("td", {}, el("code", {}, k)), el("td", { class: "pre" }, v === "" ? "（空）" : v)))))
      : el("p", { class: "muted" }, r.status === "scheduled" ? "まだ送信していません" : "なし")
  ), [el("button", { class: "btn", onclick: closeModal }, "閉じる")]);
}

async function doDryRun(s) {
  try {
    const d = await api("POST", `/api/schedules/${s.id}/dry-run`);
    openModal("ドライラン（キックしません）", el("div", {},
      el("p", { class: d.would_kick ? "ok-text" : "error-text" }, d.verdict),
      el("dl", { class: "kv" },
        el("dt", {}, "対象 run"), el("dd", {}, d.run_id ? `#${d.run_id} ${fmtDateTime(d.scheduled_at, true)}` : `（未実行の run なし。${fmtDateTime(d.scheduled_at, true)} として展開）`),
        el("dt", {}, "API"), el("dd", {}, el("code", {}, d.endpoint)),
        el("dt", {}, "ビルド中/キュー中"), el("dd", {}, d.busy ? "はい" : "いいえ")),
      issueList(d.issues),
      el("h3", {}, "展開後のパラメータ"),
      Object.keys(d.params).length
        ? el("table", { class: "table small" },
            el("thead", {}, el("tr", {}, el("th", {}, "名前"), el("th", {}, "値"), el("th", {}, "由来"))),
            el("tbody", {}, Object.entries(d.params).map(([k, v]) => el("tr", {}, el("td", {}, el("code", {}, k)), el("td", { class: "pre" }, v === "" ? "（空）" : v), el("td", { class: "muted" }, { override: "上書き", pinned: "控えたデフォルト値", default: "デフォルト" }[(d.detail[k] || {}).source] || "")))))
        : el("p", { class: "muted" }, "パラメータなし")
    ), [el("button", { class: "btn", onclick: closeModal }, "閉じる")]);
  } catch (e) {
    toast(e.message, "error");
  }
}

/* ------------------------------------------------------------------ アイテムの予定一覧 */
function firstLine(text) {
  return (text || "").split("\n").map((x) => x.trim()).find(Boolean) || "";
}

function scheduleTitle(s) {
  if (s.mode === "memo") return s.label || firstLine(s.note) || "メモ";
  return s.label || (s.mode === "cron" ? s.cron_summary || s.cron_expr : "1回");
}

function scheduleRule(s) {
  if (s.mode === "memo") return s.label ? firstLine(s.note) : "";
  if (s.mode === "cron") return `${s.cron_summary}${s.cron_summary !== s.cron_expr ? `（${s.cron_expr}）` : ""}`;
  return `1回: ${fmtDateTime(s.once_at, true)}`;
}

async function openItemPanel(targetId) {
  const t = state.targets.find((x) => x.id === targetId);
  if (!t) return;
  const memo = t.kind === "memo";
  state.panelItemId = targetId;
  state.panelScheduleId = null;
  panel.classList.remove("hidden");
  panel.scrollTop = 0;
  render();

  const holdingBox = el("div", { class: "holding-section", hidden: true });
  const scheduleBox = el("div", {}, el("p", { class: "muted" }, "読み込み中…"));
  const upcomingBox = el("div");
  const recentBox = el("div");

  const today = ymd(new Date());
  async function load() {
    let schedules, upcoming, recent, holding;
    try {
      [schedules, upcoming, recent, holding] = await Promise.all([
        api("GET", `/api/schedules?target_id=${t.id}`),
        memo ? [] : api("GET", `/api/runs?target=${t.id}&status=scheduled&from=${today}&order=asc&limit=10`),
        memo ? [] : api("GET", `/api/runs?target=${t.id}&status=queued,running,success,unstable,failure,aborted,skipped,missed,cancelled&order=desc&limit=10`),
        // 保留は予定時刻を過ぎて止まったものなので、日付で絞らずすべて出す
        memo ? [] : api("GET", `/api/runs?target=${t.id}&status=holding&order=desc&limit=200`),
      ]);
    } catch (e) {
      scheduleBox.replaceChildren(el("div", { class: "error-text" }, e.message));
      return;
    }
    if (state.panelItemId !== t.id) return;
    holdingBox.hidden = !holding.length;
    holdingBox.replaceChildren(el("h3", {}, `⛔ 保留中（${holding.length}件）`), holdingTable(holding, load, { hideItem: true }));
    // 進行中・これからのものを上に、終了したものを下に
    const rank = { active: 0, paused: 1, draft: 2, ended: 3, cancelled: 4 };
    const isPast = (s) => !!s.end_date && s.end_date < today;
    if (memo) {
      // 予定: これからの予定を日付順に、過ぎた予定は下に
      schedules.sort((a, b) => isPast(a) - isPast(b) || (isPast(a) ? (a.start_date < b.start_date ? 1 : -1) : a.start_date < b.start_date ? -1 : 1));
    } else {
      schedules.sort((a, b) => rank[a.status] - rank[b.status] || (a.start_date < b.start_date ? 1 : -1));
    }
    const byId = Object.fromEntries(schedules.map((s) => [s.id, s]));

    scheduleBox.replaceChildren(
      schedules.length
        ? el("div", { class: "sched-list" }, schedules.map((s) =>
            el("button", { class: `sched-row st-${s.status}${memo && isPast(s) ? " past" : ""}`, onclick: () => openPanel(s.id, "basic"), style: t.color ? `--c:${t.color}` : "" },
              el("div", { class: "sched-row-head" },
                scheduleAlertLevel(s) ? el("span", { class: `badge-dot ${scheduleAlertLevel(s)}`, title: scheduleAlertMessages(s).join("\n") }, "!") : null,
                s.holding_count ? el("span", { class: "hold-tag" }, `保留${s.holding_count}`) : null,
                el("b", {}, scheduleTitle(s)),
                memo ? (isPast(s) ? el("span", { class: "chip st-ended" }, "過去") : null) : statusChip(s.status)),
              el("div", { class: "muted small" },
                `${fmtDate(s.start_date)} 〜 ${s.end_date ? fmtDate(s.end_date) : "無期限"}`, scheduleRule(s) ? " ／ " : "", scheduleRule(s)),
              s.next_run_at ? el("div", { class: "small" }, "次回: ", fmtDateTime(s.next_run_at, true)) : null)))
        : el("p", { class: "muted" }, memo ? "予定はありません。タイムラインの行の空き部分をドラッグするか、上のボタンで追加できます。" : "スケジュールはありません。タイムラインの行の空き部分をドラッグするか、上のボタンで作成できます。")
    );

    const runTable = (runs, empty) =>
      runs.length
        ? el("table", { class: "table small" }, el("tbody", {}, runs.map((r) =>
            el("tr", { class: "clickable", onclick: () => (r.schedule_id ? openPanel(r.schedule_id, "runs", r.id) : showRunModal(r)) },
              el("td", { style: { whiteSpace: "nowrap" } }, fmtDateTime(r.scheduled_at, true)),
              el("td", {}, runChip(r.status)),
              el("td", { class: "muted" }, r.schedule_id ? scheduleTitle(byId[r.schedule_id] || { label: `#${r.schedule_id}` }) : r.reason || "即時実行"),
              el("td", {}, r.build_url ? el("a", { href: r.build_url, target: "_blank", rel: "noopener", onclick: (e) => e.stopPropagation() }, `#${r.build_number}`) : null)))))
        : el("p", { class: "muted" }, empty);
    upcomingBox.replaceChildren(runTable(upcoming, "予定されている run はありません。"));
    recentBox.replaceChildren(runTable(recent, "まだ実行していません。"));
  }

  const warn = [];
  if (t.schema_error) warn.push({ level: "error", message: t.schema_error });
  if (t.timer_trigger_detected) warn.push({ level: "warning", message: "Jenkins 側の cron が残っています（二重実行の恐れ）" });
  if (!t.enabled) warn.push({ level: "warning", message: "このアイテムは無効です（run はスキップされます）" });

  panel.replaceChildren(
    el("div", { class: "panel-header" },
      el("div", {},
        el("div", { class: "muted small mono" }, memo ? "予定" : t.job_path),
        el("h2", {}, el("span", { class: "swatch", style: { background: t.color || "#8a94a6", display: "inline-block", marginRight: "6px" } }), t.display_name)),
      el("button", { class: "icon-btn", title: "閉じる", onclick: closePanel }, "×")),
    el("div", { class: "actions" },
      can.editItem(t) ? el("button", {
        class: "btn primary",
        onclick: () => {
          const d = new Date();
          openCreateDialog(t.id, ymd(d), ymd(addDays(d, 6)));
        },
      }, memo ? "予定を追加" : "スケジュールを作成") : null,
      memo || !can.admin() ? null : el("button", {
        class: "btn",
        onclick: async () => {
          if (!(await confirmDialog("今すぐ実行", `${t.display_name}（${t.job_path}）を Jenkins のデフォルト値で今すぐキックします。`, "キックする"))) return;
          try {
            const r = await api("POST", `/api/targets/${t.id}/run-now`, {});
            toast(`キックしました: ${RUN_STATUS_LABEL[r.status]}${r.reason ? "（" + r.reason + "）" : ""}`, ["holding", "skipped"].includes(r.status) ? "error" : "");
            loadData();
            load();
          } catch (e) {
            toast(e.message, "error");
          }
        },
      }, "今すぐ実行"),
      el("a", { class: "btn", href: `/audit?type=target&target=${t.id}` }, "ログ")),
    issueList(warn),
    holdingBox,
    el("h3", {}, memo ? "予定" : "スケジュール"),
    scheduleBox,
    memo ? null : el("h3", {}, "今後の run（直近10件）"),
    memo ? null : upcomingBox,
    memo ? null : el("h3", {}, "最近の実行（直近10件）"),
    memo ? null : recentBox
  );
  load();
}

/* ------------------------------------------------------------------ 予定 */
function openMemoDialog(targetId, startDate, endDate) {
  const form = memoForm({ target_id: targetId, start_date: startDate, end_date: endDate }, { targets: state.targets.filter((t) => t.kind === "memo") });
  const save = async () => {
    try {
      const s = await api("POST", "/api/schedules", form.value());
      closeModal();
      toast("予定を追加しました");
      await loadData();
      openPanel(s.id);
    } catch (e) {
      toast(e.message, "error");
    }
  };
  openModal("予定の追加", form.root, [
    el("button", { class: "btn", onclick: closeModal }, "やめる"),
    el("button", { class: "btn primary", onclick: save }, "保存"),
  ]);
  onModalClose = () => items.remove("__new");
}

function renderMemoPanel(s, t) {
  const form = memoForm(s);
  const editable = can.memo();
  if (!editable) lockForm(form.root);
  panel.replaceChildren(
    el("div", { class: "panel-header" },
      el("div", {},
        el("a", { class: "small back-link", href: "#", onclick: (e) => { e.preventDefault(); openItemPanel(s.target_id); } }, `← ${t.display_name || ""} の予定一覧`),
        el("h2", {}, scheduleTitle(s), " ", el("span", { class: "kind-tag memo" }, "メモ"))),
      el("button", { class: "icon-btn", title: "閉じる", onclick: closePanel }, "×")),
    el("p", { class: "muted small" }, `作成 ${fmtDateTime(s.created_at, true)} ／ 更新 ${fmtDateTime(s.updated_at, true)}`),
    editable ? null : readonlyNote("この予定は閲覧のみです"),
    form.root,
    !editable ? null : el("div", { class: "actions end" },
      el("button", {
        class: "btn danger",
        onclick: async () => {
          if (!(await confirmDialog("削除", `「${scheduleTitle(s)}」を削除します。`, "削除", true))) return;
          try {
            await api("DELETE", `/api/schedules/${s.id}`);
            toast("削除しました");
            await loadData();
            openItemPanel(s.target_id);
          } catch (e) {
            toast(e.message, "error");
          }
        },
      }, "削除"),
      el("button", {
        class: "btn primary",
        onclick: async () => {
          try {
            await api("PATCH", `/api/schedules/${s.id}`, { ...form.value(), revision: s.revision });
            toast("保存しました");
            await loadData();
            openPanel(s.id);
          } catch (e) {
            toast(e.message, "error");
            if (isConflict(e)) {
              await loadData();
              openPanel(s.id);
            }
          }
        },
      }, "保存"))
  );
}

/** `/#schedule=ID` で開かれたら、そのスケジュールの実行履歴を開く（保留一覧のリンク用） */
function openFromHash() {
  const m = location.hash.match(/^#schedule=(\d+)$/);
  if (!m) return;
  closeModal();
  openPanel(Number(m[1]), "runs");
  history.replaceState(null, "", location.pathname + location.search);
}
