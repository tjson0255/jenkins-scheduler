/* レーン管理画面（仕様書 11.3） */
"use strict";

// ログイン中の利用者（権限）を読み込んでから画面を作る
const ready = renderHeader("/targets");

let categories = [];
let targets = [];
let schedulesByTarget = new Map(); // レーンごとのスケジューラ・予定（キャンセル済みは除く）

async function load() {
  let schedules;
  [categories, targets, schedules] = await Promise.all([api("GET", "/api/categories"), api("GET", "/api/targets"), api("GET", "/api/schedules")]);
  schedulesByTarget = new Map();
  for (const s of schedules) {
    if (!schedulesByTarget.has(s.target_id)) schedulesByTarget.set(s.target_id, []);
    schedulesByTarget.get(s.target_id).push(s);
  }
  renderTargets();
  renderScheduleList();
  renderCategories();
}

async function patchTarget(t, body, quiet) {
  try {
    const saved = await api("PATCH", `/api/targets/${t.id}`, { ...body, revision: t.revision });
    t.revision = saved.revision; // 続けて変えても自分の変更とぶつからないように（画面で変化が見えるので通知は出さない）
    await load();
  } catch (e) {
    toast(e.message, "error");
    if (isConflict(e)) load(); // 他の人の変更を読み込み直す
  }
}

function schemaStatus(t) {
  if (t.schema_error) return el("span", { class: "status-err", title: t.schema_error }, "⛔ " + t.schema_error);
  if (!t.schema_hash) return el("span", { class: "muted" }, "未取得");
  const parts = [el("span", { class: "status-ok", title: `hash ${t.schema_hash}` }, `✔ ${t.param_count ?? 0} 項目`)];
  const ic = t.issue_counts || {};
  if (ic.error) parts.push(el("span", { class: "status-err" }, ` エラー${ic.error}`));
  if (ic.warning) parts.push(el("span", { class: "status-warn" }, ` 警告${ic.warning}`));
  return el("span", {}, parts);
}

let itemFilter = ""; // 絞り込むカテゴリの id（空ならすべて）

function renderItemTools() {
  if (itemFilter && !categories.some((c) => String(c.id) === itemFilter)) itemFilter = "";
  const filter = el("select", { "aria-label": "カテゴリで絞り込む" },
    el("option", { value: "" }, "すべてのカテゴリ"),
    categories.map((c) => el("option", { value: c.id, selected: String(c.id) === itemFilter }, c.name)));
  filter.addEventListener("change", () => {
    itemFilter = filter.value;
    renderTargets();
  });
  const add = can.memo() ? el("button", { class: "btn primary", onclick: openRegisterDialog }, "＋ 追加") : null;
  document.getElementById("item-tools").replaceChildren(filter, add || "");
}

function renderTargets() {
  renderItemTools();
  const box = document.getElementById("target-list");
  const last = targets.map((t) => t.last_synced_at).filter(Boolean).sort().pop();
  document.getElementById("sync-info").textContent = last ? `最終取得 ${fmtDateTime(last, true)}（5分おきに自動取得）` : "";
  const tbody = el("tbody");
  for (const c of categories) {
    if (itemFilter && String(c.id) !== itemFilter) continue;
    const list = targets.filter((t) => t.category_id === c.id).sort((a, b) => a.sort_order - b.sort_order || a.id - b.id);
    list.forEach((t, i) => tbody.append(targetRow(t, list, i)));
  }
  box.replaceChildren(
    el("div", { class: "table-wrap" }, el("table", { class: "table targets-table" },
      el("thead", {}, el("tr", {},
        ["カテゴリ", "色", "表示名", "種類", "ジョブ", "有効", "前回ビルド実行中", "スケジューラ", ""].map((h) =>
          el("th", h === "ジョブ" ? { title: "Jenkins のジョブ1つにつき、レーンは1つです" } : {}, h)))),
      tbody))
  );
}

function targetRow(t, siblings, index) {
  const name = el("input", { type: "text", value: t.display_name });
  name.addEventListener("change", () => name.value.trim() && patchTarget(t, { display_name: name.value.trim() }));
  const color = colorPicker(t.color, (c) => patchTarget(t, { color: c }, true));
  const cat = el("select", {}, categories.map((c) => el("option", { value: c.id, selected: c.id === t.category_id }, c.name)));
  cat.addEventListener("change", () => patchTarget(t, { category_id: Number(cat.value) }));
  const enabled = el("input", { type: "checkbox", checked: t.enabled, title: "オフにすると、このレーンの run はスキップされます" });
  enabled.addEventListener("change", () => patchTarget(t, { enabled: enabled.checked }));
  const overlap = el("select", {},
    el("option", { value: "skip", selected: t.overlap_policy === "skip" }, "スキップ"),
    el("option", { value: "queue", selected: t.overlap_policy === "queue" }, "キューに積む"));
  overlap.addEventListener("change", () => patchTarget(t, { overlap_policy: overlap.value }));

  const move = async (dir) => {
    const list = siblings.slice();
    const j = index + dir;
    if (j < 0 || j >= list.length) return;
    [list[index], list[j]] = [list[j], list[index]];
    try {
      await Promise.all(list.map((x, k) => api("PATCH", `/api/targets/${x.id}`, { sort_order: k, revision: x.revision })));
      await load();
    } catch (e) {
      toast(e.message, "error");
      load();
    }
  };
  const memo = t.kind === "memo";
  // パラメータ定義・警告・今すぐ実行はスケジューラごとの情報なので、スケジューラ一覧に置く
  const na = () => el("span", { class: "muted" }, "—");
  const row = el("tr", {},
    el("td", {}, cat),
    el("td", {}, color),
    el("td", {}, name),
    el("td", {}, memo ? el("span", { class: "kind-tag memo" }, "テキスト") : el("span", { class: "kind-tag jenkins" }, "Jenkins ジョブ")),
    el("td", { class: "mono small" }, memo ? na() : t.job_path),
    el("td", {}, memo ? na() : enabled),
    el("td", {}, memo ? na() : overlap),
    el("td", {}, el("button", {
      class: "link-btn small", "data-keep": "1",
      title: memo ? "このレーンの予定を表示" : "このレーンのスケジューラを表示",
      onclick: () => showSchedulersOf(t.id),
    }, `${memo ? "予定" : "スケジューラ"} ${(schedulesByTarget.get(t.id) || []).length}件 ›`)),
    el("td", { class: "row" },
      el("button", { class: "btn small", title: "上へ", disabled: index === 0, onclick: () => move(-1) }, "↑"),
      el("button", { class: "btn small", title: "下へ", disabled: index === siblings.length - 1, onclick: () => move(1) }, "↓"),
      !can.editItem(t) ? null : el("button", {
        class: "btn small danger",
        onclick: async () => {
          if (!(await confirmDialog("レーンの削除", memo ? `${t.display_name}（テキスト）を削除します。\nこのレーンの予定もすべて削除されます。` : `${t.display_name}（${t.job_path}）を削除します。\nこのレーンのスケジューラと run 履歴も削除されます。`, "削除", true))) return;
          try {
            await api("DELETE", `/api/targets/${t.id}`);
            toast("削除しました");
            load();
          } catch (e) {
            toast(e.message, "error");
          }
        },
      }, "削除")));
  if (!can.editItem(t)) {
    // Jenkins レーンの設定は管理者だけ。並び替え（↑↓）は残す
    const ops = row.lastElementChild;
    lockForm(row);
    ops.querySelectorAll("button").forEach((b) => (b.disabled = b.textContent === "↑" ? index === 0 : b.textContent === "↓" ? index === siblings.length - 1 : b.disabled));
    if (!can.memo()) ops.replaceChildren();
  }
  return row;
}

/* ---- スケジューラ一覧（すべてのレーンのスケジューラ・予定）と追加 ---- */
let scheduleFilter = ""; // 絞り込むレーン（"t:<レーン id>"）。"" ならすべて

function matchesScheduleFilter(t) {
  return !scheduleFilter || scheduleFilter === `t:${t.id}`;
}

/** 押したカテゴリのレーン・押したレーンのスケジューラに絞り込んで、そこまでスクロールする */
function showItemsOf(categoryId) {
  itemFilter = String(categoryId);
  renderTargets();
  document.getElementById("sec-items").scrollIntoView({ behavior: "smooth", block: "start" });
}

function showSchedulersOf(targetId) {
  scheduleFilter = `t:${targetId}`;
  renderScheduleList();
  document.getElementById("sec-schedulers").scrollIntoView({ behavior: "smooth", block: "start" });
}

/** 括弧を使わない日付（例: 2026/9/28） */
function plainDate(ymdStr) {
  const d = parseYmd(ymdStr);
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`;
}

function scheduleRule(s) {
  if (s.mode === "memo") return "予定";
  if (s.mode === "once") {
    if (!s.once_at) return "1回";
    const d = new Date(s.once_at);
    return `1回 ${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  // 「6時間ごと（00分）」のような括弧は使わずに出す
  return (s.cron_summary || s.cron_expr || "").replace(/（([^）]*)）/g, " $1");
}

function scheduleTitleOf(s) {
  if (s.label) return s.label;
  if (s.mode === "memo") return (s.note || "").split("\n")[0].trim() || "（件名なし）";
  return scheduleRule(s);
}

/** カテゴリ順・レーン順に並べたレーン */
function orderedTargets() {
  const out = [];
  for (const c of categories) {
    out.push(...targets.filter((t) => t.category_id === c.id).sort((a, b) => a.sort_order - b.sort_order || a.id - b.id));
  }
  return out;
}

function renderScheduleList() {
  const box = document.getElementById("schedule-list");
  const ordered = orderedTargets();
  if (scheduleFilter && !ordered.some(matchesScheduleFilter)) scheduleFilter = "";

  const filter = el("select", { "aria-label": "レーンで絞り込む" },
    el("option", { value: "" }, "すべてのレーン"),
    ordered.map((t) => el("option", { value: `t:${t.id}`, selected: scheduleFilter === `t:${t.id}` }, t.display_name)));
  filter.addEventListener("change", () => {
    scheduleFilter = filter.value;
    renderScheduleList();
  });
  const canAdd = ordered.some((t) => can.editItem(t));
  const addBtn = canAdd ? el("button", { class: "btn primary", onclick: () => openAddSchedule(ordered) }, "＋ 追加") : null;
  const ended = el("input", { type: "checkbox", checked: showEndedSchedules() });
  ended.onchange = () => {
    setShowEndedSchedules(ended.checked);
    renderScheduleList();
  };
  const tools = document.getElementById("schedule-tools");
  tools.replaceChildren(filter, el("label", { class: "check small" }, ended, " 終了したものも表示"), addBtn || "");

  const rows = [];
  for (const t of ordered) {
    if (!matchesScheduleFilter(t)) continue;
    const list = (schedulesByTarget.get(t.id) || [])
      .filter((s) => showEndedSchedules() || !isEndedSchedule(s))
      .sort((a, b) => a.start_date.localeCompare(b.start_date) || a.id - b.id);
    list.forEach((s) => {
      const memo = s.mode === "memo";
      const lvl = scheduleAlertLevel(s) || (memo ? null : t.schema_error ? "error" : t.timer_trigger_detected ? "warning" : null);
      rows.push(el("tr", { class: lvl ? "alert-" + lvl : "" },
        el("td", {}, el("span", { class: "swatch inline", style: `background:${t.color || "#8a94a6"}` }), t.display_name),
        el("td", {}, scheduleLink(s, "basic", { title: "詳細を開く" }, scheduleTitleOf(s)),
          ...suppressionTags(s, schedulesByTarget.get(t.id) || [])),
        el("td", {}, scheduleRule(s)),
        el("td", {}, `${plainDate(s.start_date)} 〜 ${s.end_date ? plainDate(s.end_date) : "無期限"}`),
        el("td", {}, memo ? "" : statusChip(s.status)),
        el("td", { class: "small" }, memo ? "" : scheduleWarnings(s, t)),
        el("td", {}, !memo && can.admin() && ["draft", "active", "paused"].includes(s.status)
          ? el("button", { class: "btn small", title: "このスケジューラのパラメータで今すぐキックする", onclick: () => runNowSchedule(t, s) }, "今すぐ実行")
          : "")));
    });
  }
  box.replaceChildren(rows.length
    ? el("div", { class: "table-wrap" }, el("table", { class: "table schedule-table" },
        el("thead", {}, el("tr", {}, ["レーン", "件名", "実行規則", "期間", "状態", "警告", ""].map((h) => el("th", {}, h)))),
        el("tbody", {}, rows)))
    : el("p", { class: "muted" }, "スケジューラはありません。"));
}

/** そのスケジューラの警告をまとめて出す。
 *  パラメータの確認結果（Jenkins の最新のパラメータ定義と照らし合わせたもの）と、
 *  予定どおり動かない原因（保留・ジョブ・Jenkins 側の cron・レーンが無効）。何も無ければ「なし」 */
function scheduleWarnings(s, t) {
  const out = [];
  const issues = ["draft", "active", "paused"].includes(s.status) ? (s.issues || []).filter((i) => i.level !== "info") : [];
  if (issues.length) {
    const err = issues.filter((i) => i.level === "error").length;
    const warn = issues.length - err;
    const label = [err ? `エラー${err}` : null, warn ? `警告${warn}` : null].filter(Boolean).join("・");
    out.push(scheduleLink(s, "params", {
      class: err ? "status-err" : "status-warn",
      title: issues.map((i) => i.message).join("\n") + "\n\n押すとパラメータの画面を開きます（確かめて保存すると警告が消えます）",
    }, `${err ? "⛔" : "⚠"} パラメータ ${label}`));
  }
  if (s.holding_count) out.push(el("span", { class: "status-err", title: "キックされずに止まっている run があります" }, `⛔ 保留${s.holding_count}`));
  if (t.schema_error) out.push(el("span", { class: "status-err", title: t.schema_error }, "⛔ ジョブ"));
  if (t.timer_trigger_detected) out.push(el("span", { class: "status-warn", title: "Jenkins 側の cron が残っています（二重実行の恐れ）" }, "⏰ cron 残存"));
  if (!t.enabled) out.push(el("span", { class: "muted", title: "レーンが無効なので run はスキップされます" }, "レーン無効"));
  return out.length ? el("span", { class: "warn-list" }, out) : el("span", { class: "status-ok" }, "✔ なし");
}

/** スケジューラの詳細を、この画面のモーダルで開くリンク（Ctrl・⌘ を押しながらなら、タイムラインを別のタブで開く） */
function scheduleLink(s, tab, attrs, text) {
  return el("a", {
    ...attrs,
    href: `/?date=${s.start_date}#schedule=${s.id}&tab=${tab}`,
    onclick: (e) => {
      if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
      e.preventDefault();
      openScheduleModal(s, tab);
    },
  }, text);
}

/** タイムライン画面の詳細パネルを、埋め込み表示（?embed=1）でモーダルの中に出す。閉じたら一覧を読み直す */
function openScheduleModal(s, tab) {
  const frame = el("iframe", { class: "schedule-frame", src: `/?embed=1&date=${s.start_date}#schedule=${s.id}&tab=${tab}`, title: "スケジューラの詳細" });
  const modal = openModal(scheduleTitleOf(s), frame, []);
  modal.querySelector(".modal").classList.add("modal-wide");
  modal.querySelector(".modal-footer").remove();
  onModalClose = () => load(); // 詳細で変えた内容（パラメータの保存など）を一覧に反映する
}

// 埋め込み表示のパネルが閉じられたら、モーダルも閉じる
window.addEventListener("message", (e) => {
  if (e.origin === location.origin && e.data && e.data.type === "schedule-panel-closed" && document.getElementById("modal")) closeModal();
});

async function runNowSchedule(t, s) {
  if (!(await confirmDialog("今すぐ実行", `${t.display_name}「${scheduleTitleOf(s)}」を、このスケジューラのパラメータで今すぐキックします。`, "キックする"))) return;
  try {
    const r = await api("POST", `/api/targets/${t.id}/run-now`, { schedule_id: s.id });
    toast(`キックしました: ${RUN_STATUS_LABEL[r.status]}${r.reason ? "（" + r.reason + "）" : ""}`, ["holding", "skipped"].includes(r.status) ? "error" : "");
    load();
  } catch (e) {
    toast(e.message, "error");
  }
}

/** 追加ダイアログ。レーンを選ぶと、Jenkins ならスケジューラ、テキストのレーンなら予定の入力欄にする。
 *  追加できないレーン（管理者ログインしていないときの Jenkins レーン）も、選べない形で並べる */
function openAddSchedule(ordered) {
  const editable = ordered.filter((t) => can.editItem(t));
  const first = editable.find((t) => scheduleFilter === `t:${t.id}`) || editable[0];
  const pick = el("select", {}, categories.map((c) => {
    const items = ordered.filter((t) => t.category_id === c.id);
    return items.length
      ? el("optgroup", { label: c.name }, items.map((t) => {
          const ok = can.editItem(t);
          return el("option", { value: t.id, disabled: !ok, selected: t === first }, ok ? t.display_name : `${t.display_name}（管理者のみ）`);
        }))
      : null;
  }));
  const note = editable.length < ordered.length
    ? readonlyNote("Jenkins のレーンへのスケジューラの追加は管理者のみです")
    : null;
  const area = el("div");
  const buttons = el("div", { class: "row" });
  let form = null;
  const save = async (extra, message) => {
    const t = editable.find((x) => String(x.id) === pick.value);
    try {
      await api("POST", "/api/schedules", { ...form.value(), target_id: t.id, ...extra });
      closeModal();
      toast(message);
      load();
    } catch (e) {
      toast(e.message, "error");
    }
  };
  const build = () => {
    const t = editable.find((x) => String(x.id) === pick.value);
    const today = new Date();
    if (t.kind === "memo") {
      form = memoForm({ target_id: t.id, start_date: ymd(today), end_date: ymd(today) });
      buttons.replaceChildren(
        el("button", { class: "btn", onclick: closeModal }, "やめる"),
        el("button", { class: "btn primary", onclick: () => save({}, "予定を追加しました") }, "保存"));
    } else {
      form = scheduleForm({ target_id: t.id, start_date: ymd(today), end_date: ymd(addDays(today, 6)), mode: "cron", cron_expr: "0 3 * * *" });
      buttons.replaceChildren(
        el("button", { class: "btn", onclick: closeModal }, "やめる"),
        el("button", { class: "btn", onclick: () => save({ activate: false }, "ドラフトとして保存しました") }, "ドラフトで保存"),
        el("button", { class: "btn primary", onclick: () => save({ activate: true }, "スケジューラを作成して有効化しました") }, "保存して有効化"));
    }
    area.replaceChildren(form.root);
  };
  pick.addEventListener("change", build);
  build();
  openModal("スケジューラ・予定の追加", el("div", { class: "form" }, note, el("label", { class: "field" }, el("span", {}, "レーン"), pick), area), [buttons]);
}

let registerState = { results: [], open: false };
/** レーンの追加ダイアログ（テキスト / Jenkins ジョブ） */
function openRegisterDialog() {
  registerState = { results: [], open: false };
  const box = el("div", { class: "form", id: "register-form" });
  const kindMemo = el("input", { type: "radio", name: "kind", value: "memo", checked: true });
  const kindJenkins = el("input", { type: "radio", name: "kind", value: "jenkins", disabled: !can.admin() });
  const isMemo = () => kindMemo.checked;
  const q = el("input", { type: "text", placeholder: "ジョブ名で検索（例: release）", name: "q" });
  const jobPath = el("input", { type: "text", placeholder: "release/core-pipeline", name: "job_path", class: "mono" });
  const displayName = el("input", { type: "text", placeholder: "未入力ならジョブ名", name: "display_name" });
  const cat = el("select", { name: "category" }, categories.map((c) => el("option", { value: c.id, selected: String(c.id) === itemFilter }, c.name)));
  const color = colorPicker("#4e79a7");
  const overlap = el("select", { name: "overlap" }, el("option", { value: "skip" }, "前回ビルド実行中はスキップ"), el("option", { value: "queue" }, "キューに積む"));
  const results = el("div", { class: "job-results", id: "job-results", hidden: true });
  // 候補をクリックしても検索欄のフォーカスが外れない（＝一覧が閉じない）ようにする
  results.addEventListener("mousedown", (e) => e.preventDefault());

  const search = debounce(async () => {
    try {
      registerState.results = await api("GET", `/api/jenkins/jobs?q=${encodeURIComponent(q.value)}`);
    } catch (e) {
      registerState.results = [];
      results.replaceChildren(el("div", { class: "error-text" }, e.message));
      return;
    }
    renderResults();
  }, 300);
  const openList = () => {
    registerState.open = true;
    search();
  };
  q.addEventListener("input", openList);
  q.addEventListener("focus", openList);
  // フォーカスが外れたとき・Esc で一覧を閉じる
  q.addEventListener("blur", closeResults);
  q.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeResults();
  });

  const submit = el("button", {
    class: "btn primary",
    onclick: async () => {
      const body = isMemo()
        ? { kind: "memo", display_name: displayName.value.trim(), category_id: Number(cat.value), color: color.value }
        : { kind: "jenkins", job_path: jobPath.value.trim(), display_name: displayName.value.trim() || null, category_id: Number(cat.value), color: color.value, overlap_policy: overlap.value };
      if (isMemo() && !body.display_name) return toast("名前を入力してください", "error");
      if (!isMemo() && !body.job_path) return toast("ジョブのパスを入力してください", "error");
      try {
        await api("POST", "/api/targets", body);
        closeModal();
        toast("登録しました");
        await load();
      } catch (e) {
        toast(e.message, "error");
      }
    },
  }, "登録（Jenkins で存在とパラメータを確認）");

  const field = (label, input) => el("label", { class: "field" }, el("span", {}, label), input);
  // Jenkins レーンのときだけ使う欄
  const searchField = field("Jenkins のジョブを検索", q);
  const jobField = field("ジョブのパス", jobPath);
  const overlapRow = el("div", { class: "row wrap" }, field("前回ビルドが実行中/キュー中のとき", overlap));
  const nameLabel = el("span", {}, "表示名");
  const kindHint = el("p", { class: "muted small" });
  const renderKind = () => {
    const memo = isMemo();
    for (const e of [searchField, results, jobField, overlapRow]) e.hidden = memo;
    if (!memo) results.hidden = !registerState.open || !registerState.results.length;
    nameLabel.textContent = memo ? "名前（必須）" : "表示名";
    displayName.placeholder = memo ? "" : "未入力ならジョブ名";
    submit.textContent = memo ? "登録" : "登録（Jenkins で存在とパラメータを確認）";
    kindHint.textContent = memo
      ? "Jenkins には接続しません。タイムラインにメモや計画を書き込むための行です。"
      : "Jenkins のジョブを定時にキックする行です。Jenkins のジョブ1つにつき、レーンは1つです（日によって件名やパラメータを変えるときは、そのレーンにスケジューラを追加します）。登録時に Jenkins でジョブの存在とパラメータを確認します。";
    checkDuplicate();
  };
  // すでにレーンがあるジョブなら、その場で知らせて登録できないようにする
  const dupNote = el("p", { class: "dup-note", hidden: true });
  const checkDuplicate = () => {
    const path = jobPath.value.trim().replace(/^\/+|\/+$/g, "");
    const dup = !isMemo() && path ? targets.find((t) => t.job_path === path) : null;
    dupNote.hidden = !dup;
    if (dup) dupNote.textContent = `このジョブのレーンはすでにあります（「${dup.display_name}」）。Jenkins のジョブ1つにつきレーンは1つです。日によって件名やパラメータを変えるときは、そのレーンにスケジューラを追加してください。`;
    submit.disabled = !!dup;
  };
  jobPath.addEventListener("input", checkDuplicate);
  kindJenkins.addEventListener("change", renderKind);
  kindMemo.addEventListener("change", renderKind);
  box.append(
    el("div", { class: "row wrap" },
      el("span", { class: "muted small" }, "種類"),
      el("label", { class: "check" }, kindMemo, " テキスト"),
      el("label", { class: "check", title: can.admin() ? "" : "Jenkins レーンの登録には管理者ログインが必要です" }, kindJenkins, " Jenkins ジョブ")),
    kindHint,
    searchField,
    results,
    el("div", { class: "row wrap" }, jobField, el("label", { class: "field" }, nameLabel, displayName), field("カテゴリ", cat), field("色", color)),
    dupNote,
    overlapRow
  );
  renderKind();
  box._jobPath = jobPath;
  box._displayName = displayName;
  openModal("レーンの追加", box, [el("button", { class: "btn", onclick: closeModal }, "やめる"), submit]);
}

function renderResults() {
  const box = document.getElementById("job-results");
  if (!box) return;
  const registered = new Set(targets.map((t) => t.job_path));
  const form = document.getElementById("register-form");
  box.replaceChildren(
    ...registerState.results.map((j) =>
      el("div", {
        class: registered.has(j.path) ? "registered" : "",
        title: registered.has(j.path) ? "このジョブのレーンはすでにあります（ジョブ1つにつきレーンは1つ）" : j.url || "",
        onclick: () => {
          if (registered.has(j.path)) return;
          form._jobPath.value = j.path;
          if (!form._displayName.value) form._displayName.placeholder = j.name;
          closeResults();
          form._jobPath.focus();
        },
      }, j.path, registered.has(j.path) ? `　レーンあり「${targets.find((t) => t.job_path === j.path)?.display_name || ""}」` : "")
    )
  );
  box.hidden = !registerState.open || !registerState.results.length;
}

function closeResults() {
  registerState.open = false;
  renderResults();
}

function renderCategories() {
  const box = document.getElementById("category-list");
  const sorted = categories.slice().sort((a, b) => a.sort_order - b.sort_order || a.id - b.id);
  const move = async (i, dir) => {
    const list = sorted.slice();
    const j = i + dir;
    if (j < 0 || j >= list.length) return;
    [list[i], list[j]] = [list[j], list[i]];
    try {
      await Promise.all(list.map((c, k) => api("PATCH", `/api/categories/${c.id}`, { sort_order: k })));
      load();
    } catch (e) {
      toast(e.message, "error");
    }
  };
  const rows = sorted.map((c, i) => {
    const name = el("input", { type: "text", value: c.name });
    name.addEventListener("change", async () => {
      try {
        await api("PATCH", `/api/categories/${c.id}`, { name: name.value.trim() });
        load();
      } catch (e) {
        toast(e.message, "error");
      }
    });
    return el("div", { class: "cat-row" },
      name,
      el("button", { class: "link-btn small", title: "このカテゴリのレーンを表示", onclick: () => showItemsOf(c.id) }, `レーン ${c.target_count}件 ›`),
      el("button", { class: "btn small", disabled: i === 0, onclick: () => move(i, -1) }, "↑"),
      el("button", { class: "btn small", disabled: i === sorted.length - 1, onclick: () => move(i, 1) }, "↓"),
      el("button", {
        class: "btn small danger",
        disabled: c.target_count > 0,
        title: c.target_count > 0 ? "所属レーンがあると削除できません" : "",
        onclick: async () => {
          try {
            await api("DELETE", `/api/categories/${c.id}`);
            load();
          } catch (e) {
            toast(e.message, "error");
          }
        },
      }, "削除"));
  });
  const newName = el("input", { type: "text", placeholder: "新しいカテゴリ名" });
  box.replaceChildren(
    ...rows,
    el("div", { class: "cat-row" }, newName, el("button", {
      class: "btn",
      onclick: async () => {
        if (!newName.value.trim()) return;
        try {
          await api("POST", "/api/categories", { name: newName.value.trim() });
          load();
        } catch (e) {
          toast(e.message, "error");
        }
      },
    }, "追加"))
  );
}

document.getElementById("btn-sync").onclick = async () => {
  try {
    const res = await api("POST", "/api/targets/sync");
    const errs = res.filter((r) => r.error);
    toast(`${res.length} 件のレーンを Jenkins から取り直しました${errs.length ? `（エラー ${errs.length} 件）` : ""}`, errs.length ? "error" : "");
    load();
  } catch (e) {
    toast(e.message, "error");
  }
};

ready.then(() => {
  if (!can.admin()) {
    // Jenkins レーンの設定・再取得は管理者だけ
    document.getElementById("btn-sync").hidden = true;
    if (can.memo()) {
      document.getElementById("target-list").before(readonlyNote("テキストのレーンとカテゴリは編集できます。Jenkins レーンの設定は閲覧と並び替えのみです"));
    } else {
      document.getElementById("category-list").closest("section").hidden = true;
      document.getElementById("target-list").before(readonlyNote("レーンの設定は閲覧のみです"));
    }
  }
  return load();
}).catch((e) => toast(e.message, "error"));

/* ---- バックアップ ---- */
async function loadBackups() {
  const box = document.getElementById("backup-list");
  let b;
  try {
    b = await api("GET", "/api/backups");
  } catch (e) {
    box.replaceChildren(el("div", { class: "error-text" }, e.message));
    return;
  }
  const kb = (n) => (n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);
  box.replaceChildren(
    el("p", { class: "muted small" },
      b.enabled ? `毎日 ${b.time} に自動で取得し、${b.keep} 世代残します。` : "自動バックアップは無効です（BACKUP_ENABLED=false）。",
      " 保存先は ", b.dir_configured ? ".env の BACKUP_DIR で指定した場所です。" : "既定の場所です（.env の BACKUP_DIR が未設定）。",
      el("br"), "保存先・時刻・世代数は .env の BACKUP_DIR / BACKUP_TIME / BACKUP_KEEP で変更し、ツールを再起動すると反映されます。",
      el("br"), "scheduler-*.db は復元用（run 履歴・ログを含む）、settings-*.json はレーン・スケジューラ・パラメータの内容を読める形で書き出したものです。Jenkins のトークンを含む .env はバックアップしません。"),
    b.files.length
      ? el("table", { class: "table small" },
          el("thead", {}, el("tr", {}, ["ファイル", "サイズ", "作成日時", ""].map((h) => el("th", {}, h)))),
          el("tbody", {}, b.files.map((f) => el("tr", {},
            el("td", { class: "mono" }, f.name), el("td", {}, kb(f.size)), el("td", {}, fmtDateTime(f.modified_at, true)),
            el("td", {}, f.name.endsWith(".db") ? el("button", { class: "btn small danger", onclick: () => restoreBackup(f) }, "この時点に戻す") : null)))))
      : el("p", { class: "muted" }, "まだバックアップはありません。")
  );
}

async function restoreBackup(f) {
  const ok = await confirmDialog(
    "バックアップの時点に戻す",
    `${fmtDateTime(f.modified_at, true)} のバックアップ（${f.name}）の時点に、すべてのデータを戻します。\n\n` +
      "・レーン、スケジューラ、パラメータ、実行履歴、ログがこの時点の内容に置き換わります\n" +
      "・戻す直前に今の状態を自動でバックアップするので、間違えた場合はそこから戻せます\n" +
      "・戻したあと、予定時刻を過ぎている未実行の run は、各スケジューラの「ツール停止などで予定時刻を過ぎた回」の設定に従って処理されます\n" +
      "・他の人が開いている画面は、再読み込みで新しい内容になります",
    "戻す",
    true
  );
  if (!ok) return;
  try {
    const r = await api("POST", `/api/backups/${encodeURIComponent(f.name)}/restore`);
    toast(`戻しました（戻す前の状態は ${r.backup_before_restore.find((n) => n.endsWith(".db"))} に保存）`);
    setTimeout(() => location.reload(), 1500);
  } catch (e) {
    toast(e.message, "error");
  }
}

document.getElementById("btn-backup").onclick = async () => {
  try {
    const r = await api("POST", "/api/backups");
    toast(`バックアップしました: ${r.created.join(", ")}${r.removed.length ? `（古い ${r.removed.length} 件を削除）` : ""}`);
    loadBackups();
  } catch (e) {
    toast(e.message, "error");
  }
};
// バックアップは管理者だけに表示する
ready.then(() => {
  if (!can.admin()) return;
  document.getElementById("backup-section").hidden = false;
  loadBackups();
});
