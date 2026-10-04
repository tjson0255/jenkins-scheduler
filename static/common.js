/* 共通ユーティリティ（ビルド工程なし・素の JS） */
"use strict";

const WD = ["日", "月", "火", "水", "木", "金", "土"];

// 条件付きの子要素を `cond ? node : null` で書けるよう、replaceChildren / append は null を無視する
for (const proto of [Element.prototype, DocumentFragment.prototype]) {
  for (const name of ["replaceChildren", "append"]) {
    const orig = proto[name];
    proto[name] = function (...kids) {
      return orig.apply(this, kids.flat().filter((k) => k !== null && k !== undefined && k !== false));
    };
  }
}

const RUN_STATUS_LABEL = {
  scheduled: "予定",
  holding: "保留",
  queued: "キュー",
  running: "実行中",
  success: "成功",
  unstable: "不安定",
  failure: "失敗",
  aborted: "中断",
  skipped: "スキップ",
  missed: "見逃し",
  cancelled: "キャンセル",
};

// 状態が「このツール側」と「Jenkins 側」のどちらのものか（問題がどちらにあるかを見分けるため）
const RUN_SIDE = {
  scheduled: "tool", holding: "tool", skipped: "tool", missed: "tool", cancelled: "tool",
  queued: "jenkins", running: "jenkins", success: "jenkins", unstable: "jenkins", failure: "jenkins", aborted: "jenkins",
};
const SIDE_HINT = {
  tool: "ツール側: まだキックしていない、またはこのツールがキックしなかった・止めたもの。原因はこのツールの設定やパラメータ（保留・エラーは Jenkins のパラメータ定義の変更が原因のこともあります）",
  jenkins: "Jenkins 側: Jenkins がキックを受け付けた後の、ビルドそのものの状態・結果。原因はジョブやビルドの中身",
};
const RUN_STATUS_HINT = {
  scheduled: "まだキックしていない",
  holding: "予定時刻にキックしようとしたが、問題があってこのツールが止めた",
  skipped: "このツールがキックしなかった（前回のビルドが実行中・手動でスキップ・この回だけ変更 など）",
  missed: "ツールが止まっていたなどで、猶予時間内にキックできなかった",
  cancelled: "キックせずに取りやめた",
  queued: "Jenkins がキックを受け付け、キューで待っている",
  running: "Jenkins でビルド中",
  success: "Jenkins のビルドが成功した",
  unstable: "Jenkins のビルドは最後まで通ったが、テストの一部失敗などがある",
  failure: "Jenkins のビルドが失敗した",
  aborted: "Jenkins のビルドが途中で止められた",
};
/** 状態の説明（ツール側か Jenkins 側かを先頭に付ける） */
function runStatusHint(status) {
  const side = RUN_SIDE[status] === "jenkins" ? "Jenkins 側" : "ツール側";
  return `${side}: ${RUN_STATUS_HINT[status] || ""}`;
}

const SCHEDULE_STATUS_LABEL = {
  draft: "ドラフト",
  active: "有効",
  paused: "一時停止",
  ended: "終了",
  cancelled: "キャンセル",
};

async function api(method, path, body) {
  // CSRF 対策: 変更系の API はこのヘッダーが無いとサーバーが拒否する
  const opts = { method, headers: { "X-Requested-With": "jenkins-scheduler" } };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (res.status === 204) return null;
  let data = null;
  const text = await res.text();
  try {
    data = text ? JSON.parse(text) : null;
  } catch (_) {
    data = text;
  }
  if (res.status === 401 && path !== "/api/auth/login" && location.pathname !== "/login") {
    // ログインが切れた: ログイン画面へ（戻り先を付ける）
    location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
  }
  if (!res.ok) {
    const err = new Error(errorMessage(data) || `HTTP ${res.status}`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

/** 同時編集で他の人が先に保存していた（409 conflict）か */
function isConflict(e) {
  return !!(e && e.status === 409 && e.data && e.data.detail && e.data.detail.code === "conflict");
}

function errorMessage(data) {
  if (!data) return "";
  const d = data.detail !== undefined ? data.detail : data;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((x) => `${(x.loc || []).slice(1).join(".")}: ${x.msg}`).join("\n");
  if (d && d.message) {
    const extra = (d.issues || []).map((i) => "・" + i.message).join("\n");
    return d.message + (extra ? "\n" + extra : "") + (d.reason ? "\n" + d.reason : "");
  }
  return JSON.stringify(d);
}

function el(tag, attrs, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(e.style, v);
    else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
    else if (k === "html") e.innerHTML = v;
    else if (v === true) e.setAttribute(k, "");
    else e.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    e.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return e;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function pad(n) {
  return String(n).padStart(2, "0");
}

/** Date → 'YYYY-MM-DD'（ブラウザのローカル時刻） */
function ymd(d) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** 'YYYY-MM-DD' → ローカル 0:00 の Date */
function parseYmd(s) {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function addDays(d, n) {
  const x = new Date(d);
  x.setDate(x.getDate() + n);
  return x;
}

function fmtDateTime(iso, withYear) {
  if (!iso) return "";
  const d = new Date(iso);
  const date = withYear ? `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}` : `${d.getMonth() + 1}/${d.getDate()}`;
  return `${date}(${WD[d.getDay()]}) ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fmtDate(s) {
  if (!s) return "";
  const d = parseYmd(s);
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}(${WD[d.getDay()]})`;
}

/** ISO → datetime-local 入力用 'YYYY-MM-DDTHH:MM' */
function toLocalInput(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return `${ymd(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

let toastTimer = null;
function toast(msg, kind) {
  let box = document.getElementById("toast");
  if (!box) {
    box = el("div", { id: "toast" });
    document.body.append(box);
  }
  box.textContent = msg;
  box.className = "show " + (kind || "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (box.className = ""), kind === "error" ? 8000 : 3500);
}

function debounce(fn, ms) {
  let t = null;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

function statusChip(status, map) {
  return el("span", { class: `chip st-${status}` }, (map || SCHEDULE_STATUS_LABEL)[status] || status);
}

function runChip(status) {
  return el("span", { class: `chip rs-${status}`, title: runStatusHint(status) }, RUN_STATUS_LABEL[status] || status);
}

/** レーンの警告レベル（行の網掛けに使う）: "error" | "warning" | null */
function itemAlertLevel(t) {
  if (!t || t.kind === "memo") return null;
  const ic = t.issue_counts || {};
  if (t.schema_error || ic.error || t.holding_count) return "error";
  if (t.timer_trigger_detected || ic.warning) return "warning";
  return null;
}

function itemAlertMessages(t) {
  const ic = (t && t.issue_counts) || {};
  return [
    t && t.holding_count ? `保留中の run ${t.holding_count} 件（キックされずに止まっています）` : null,
    t.schema_error,
    ic.error ? `パラメータ定義のエラー ${ic.error} 件（キックされず保留になります）` : null,
    t.timer_trigger_detected ? "Jenkins 側の cron が残っています（二重実行の恐れ）" : null,
    ic.warning ? `パラメータ定義の警告 ${ic.warning} 件（要確認）` : null,
  ].filter(Boolean);
}

function issueList(issues) {
  if (!issues || !issues.length) return null;
  return el(
    "ul",
    { class: "issues" },
    issues.map((i) => el("li", { class: `issue-${i.level}` }, el("b", {}, { error: "⛔ エラー", warning: "⚠ 警告", info: "ℹ 情報" }[i.level] || i.level), " ", i.message))
  );
}

/* ---- モーダル ---- */
function openModal(title, body, buttons) {
  closeModal();
  const footer = el("div", { class: "modal-footer" });
  const modal = el(
    "div",
    { class: "modal-backdrop", id: "modal", onmousedown: (e) => e.target.id === "modal" && closeModal() },
    el("div", { class: "modal", role: "dialog", "aria-modal": "true" },
      el("div", { class: "modal-header" }, el("h2", {}, title), el("button", { class: "icon-btn", title: "閉じる", onclick: closeModal }, "×")),
      el("div", { class: "modal-body" }, body),
      footer)
  );
  for (const b of buttons || []) footer.append(b);
  document.body.append(modal);
  document.addEventListener("keydown", modalEsc);
  return modal;
}

function modalEsc(e) {
  if (e.key === "Escape") closeModal();
}

let onModalClose = null;
function closeModal() {
  const m = document.getElementById("modal");
  if (m) m.remove();
  document.removeEventListener("keydown", modalEsc);
  if (onModalClose) {
    const f = onModalClose;
    onModalClose = null;
    f();
  }
}

function confirmDialog(title, message, okLabel, danger) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (v) => {
      if (done) return;
      done = true;
      onModalClose = null;
      closeModal();
      resolve(v);
    };
    openModal(title, el("p", { class: "pre" }, message), [
      el("button", { class: "btn", onclick: () => finish(false) }, "やめる"),
      el("button", { class: danger ? "btn danger" : "btn primary", onclick: () => finish(true) }, okLabel || "OK"),
    ]);
    onModalClose = () => {
      if (!done) {
        done = true;
        resolve(false);
      }
    };
  });
}

/* ---- ログイン中の利用者と権限 ---- */
let ME = null;
const can = {
  /** 1. フルコントロール */
  admin: () => !ME || ME.can.admin,
  /** 2. 予定を編集できる（フルコントロールも含む） */
  memo: () => !ME || ME.can.edit_memo,
  /** そのレーンの予定を編集できるか */
  editItem: (t) => can.admin() || (!!t && t.kind === "memo" && can.memo()),
};

async function loadMe() {
  try {
    ME = await api("GET", "/api/auth/me");
  } catch (_) {
    ME = null;
  }
  if (ME) document.body.classList.add("role-" + ME.role);
  return ME;
}

/** 編集できない人向けに、フォームの入力欄をまとめて無効にする */
/** レーンにスケジューラ・予定を追加できないときの説明（ドラッグやダブルクリックしても何も起きないと分かりにくいので出す） */
function explainCannotAdd(t) {
  if (!t) return;
  if (t.kind !== "memo" && !can.admin()) toast("Jenkins レーンにスケジューラを追加するには、管理者ログインが必要です（画面右上の「管理者ログイン」から）", "error");
  else toast("このレーンに予定を追加する権限がありません", "error");
}

function lockForm(root) {
  root.querySelectorAll("input, select, textarea, .color-btn").forEach((e) => (e.disabled = true));
  return root;
}

/* ---- 色の選択（代表的な色のプリセット＋その他の色） ---- */
// タイムラインで見分けやすい配色（Tableau 10）
const COLOR_PRESETS = [
  ["#4e79a7", "青"], ["#f28e2b", "オレンジ"], ["#e15759", "赤"], ["#76b7b2", "青緑"], ["#59a14f", "緑"],
  ["#edc948", "黄"], ["#b07aa1", "紫"], ["#ff9da7", "ピンク"], ["#9c755f", "茶"], ["#8a94a6", "グレー"],
];

/**
 * 色を選ぶ部品。押すとプリセットの見本が出て、「その他の色…」で RGB も指定できる。
 * 戻り値の要素の .value で今の色を読める。onChange は色が変わったときに呼ばれる。
 */
function colorPicker(value, onChange) {
  let current = (value || "#8a94a6").toLowerCase();
  const native = el("input", { type: "color", value: current, class: "color-native" });
  const swatch = el("span", { class: "color-swatch" });
  const btn = el("button", { type: "button", class: "color-btn", title: "色を選ぶ" }, swatch);
  const pop = el("div", { class: "color-pop", hidden: true, role: "dialog", "aria-label": "色を選ぶ" });
  const root = el("span", { class: "color-picker" }, btn, pop);

  const close = () => {
    pop.hidden = true;
    document.removeEventListener("mousedown", outside, true);
    document.removeEventListener("keydown", onKey, true);
    window.removeEventListener("scroll", close, true);
    window.removeEventListener("resize", close);
  };
  const outside = (e) => !root.contains(e.target) && close();
  const onKey = (e) => e.key === "Escape" && close();
  const render = () =>
    pop.replaceChildren(
      el("div", { class: "color-chips" }, COLOR_PRESETS.map(([c, name]) =>
        el("button", {
          type: "button",
          class: "color-chip" + (c === current ? " selected" : ""),
          style: { background: c },
          title: name,
          "aria-label": name,
          onclick: () => {
            set(c, true);
            close();
          },
        }))),
      el("label", { class: "color-other" }, native, "その他の色…"));
  const set = (c, fire) => {
    current = c.toLowerCase();
    swatch.style.background = current;
    native.value = current;
    btn.title = (COLOR_PRESETS.find(([p]) => p === current) || [, current])[1] + "（押すと変更）";
    render();
    if (fire && onChange) onChange(current);
  };
  native.addEventListener("change", () => {
    set(native.value, true);
    close();
  });
  btn.addEventListener("click", () => {
    if (btn.disabled) return;
    if (!pop.hidden) return close();
    // 表のスクロール枠で切れないよう、画面に対して位置を決める
    const r = btn.getBoundingClientRect();
    pop.style.left = `${Math.min(r.left, window.innerWidth - 230)}px`;
    pop.style.top = `${r.bottom + 4}px`;
    pop.hidden = false;
    document.addEventListener("mousedown", outside, true);
    document.addEventListener("keydown", onKey, true);
    // 画面に固定して出しているので、スクロールしたら閉じる（ボタンから離れて残らないように）
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
  });
  set(current, false);
  Object.defineProperty(root, "value", { get: () => current });
  return root;
}

function adminLoginUrl() {
  return `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
}

function readonlyNote(text) {
  if (ME && ME.auth_mode === "shared_admin") {
    return el("p", { class: "readonly-note" }, `🔒 ${text || "閲覧のみです"}。変更するには `, el("a", { href: adminLoginUrl() }, "管理者ログイン"), " してください。");
  }
  return el("p", { class: "readonly-note" }, `🔒 ${text || "閲覧のみです"}（権限: ${ME ? ME.role_label : ""}）`);
}

/* ---- ヘッダー（ナビとヘルス表示） ---- */
function renderHeader(active) {
  const nav = [
    ["/day", "1日の予定"],
    ["/", "タイムライン"],
    ["/history", "実行結果"],
    ["/targets", "アイテム"],
    ["/audit", "ログ"],
    ["/help", "ヘルプ"],
  ];
  const header = el(
    "header",
    { class: "topbar" },
    el("div", { class: "brand" }, "Jenkins Scheduler"),
    el("nav", {}, nav.map(([href, label]) => el("a", { href, class: href === active ? "active" : "" }, label))),
    el("button", { class: "holding-badge", id: "holding-badge", hidden: true, title: "キックされずに止まっている run の一覧を開く", onclick: openHoldingModal }, ""),
    el("div", { class: "health", id: "health" }, "…")
  );
  document.body.prepend(header);
  refreshHealth();
  refreshHolding();
  setInterval(refreshHealth, 30000);
  setInterval(refreshHolding, 15000);
  return loadMe().then((me) => {
    if (me && me.demo) {
      // 公開デモの案内
      const d = me.demo;
      header.after(el("div", { class: "demo-banner" },
        el("b", {}, "デモ版"), "です。Jenkins は架空のジョブ（モック）で、データは", `${d.reset_hours}時間ごとに初期化されます。`,
        d.admin_password ? el("span", {}, " 管理者で試す: ユーザー ", el("code", {}, d.admin_username), " ／ パスワード ", el("code", {}, d.admin_password)) : null));
    }
    if (me && me.auth_mode === "shared_admin") {
      // ログインなし（予定の編集まで）⇄ 共有の管理者アカウント（すべて）を切り替える
      header.append(me.can.admin
        ? el("div", { class: "userbox" },
            el("span", { class: "role admin" }, "管理者"),
            el("button", {
              onclick: async () => {
                try {
                  await api("POST", "/api/auth/logout");
                } catch (_) {}
                location.reload();
              },
            }, "ログアウト"))
        : el("div", { class: "userbox" },
            el("a", { class: "login-link", href: adminLoginUrl(), title: "Jenkins のスケジューラなどを操作するには管理者でログインします" }, "管理者ログイン")));
    } else if (me && (me.auth_mode === "ldap" || me.auth_mode === "mock")) {
      header.append(el("div", { class: "userbox" },
        el("span", { title: me.username }, me.display_name),
        el("span", { class: "role" }, me.role_label),
        el("button", {
          onclick: async () => {
            try {
              await api("POST", "/api/auth/logout");
            } catch (_) {}
            location.href = "/login";
          },
        }, "ログアウト")));
    }
    return me;
  });
}

/* ---- 保留（holding）: 画面上部のバッジと一覧 ---- */
async function refreshHolding() {
  const badge = document.getElementById("holding-badge");
  if (!badge) return;
  try {
    const { holding } = await api("GET", "/api/runs/holding-count");
    badge.hidden = !holding;
    badge.textContent = `⛔ 保留 ${holding}件`;
  } catch (_) {}
}

/** 保留の run の表。onChanged は保留解除・スキップの後に呼ばれる */
function holdingTable(runs, onChanged, opts) {
  opts = opts || {};
  if (!runs.length) return el("p", { class: "muted" }, "保留中の run はありません。");
  const act = (label, path, okMsg, confirmLabel) => {
    // 保留解除はその場でキックするので、1回目のクリックで確認の表示に変え、2回目で実行する
    const b = el("button", { class: "btn small" + (confirmLabel ? " primary" : "") }, label);
    let armed = false;
    b.onclick = async () => {
      if (confirmLabel && !armed) {
        armed = true;
        b.textContent = confirmLabel;
        b.classList.add("danger-armed");
        setTimeout(() => {
          armed = false;
          b.textContent = label;
          b.classList.remove("danger-armed");
        }, 4000);
        return;
      }
      b.disabled = true;
      try {
        const r = await api("POST", path);
        toast(`${okMsg}: ${RUN_STATUS_LABEL[r.status] || r.status}`);
      } catch (e) {
        toast(e.message, "error");
      }
      refreshHolding();
      if (typeof loadData === "function") loadData(true);
      onChanged && onChanged();
    };
    return b;
  };
  return el("table", { class: "table small holding-table" },
    el("thead", {}, el("tr", {}, ["予定日時", opts.hideItem ? null : "レーン", "スケジューラ", "詳細", ""].filter(Boolean).map((h) => el("th", {}, h)))),
    el("tbody", {}, runs.map((r) =>
      el("tr", {},
        el("td", { style: { whiteSpace: "nowrap" } }, fmtDateTime(r.scheduled_at, true)),
        opts.hideItem ? null : el("td", {}, r.target_name || `#${r.target_id}`),
        el("td", {}, r.schedule_id ? el("a", { href: `/#schedule=${r.schedule_id}` }, r.schedule_title || `#${r.schedule_id}`) : el("span", { class: "muted" }, "即時実行")),
        el("td", { class: "pre holding-reason" }, r.reason || ""),
        el("td", { class: "row" },
          can.admin() ? act("保留解除", `/api/runs/${r.id}/release-hold`, "保留解除", "今すぐキックする") : null,
          can.admin() ? act("スキップ", `/api/runs/${r.id}/skip`, "スキップしました") : null)))));
}

async function openHoldingModal() {
  const body = el("div", {}, el("p", { class: "muted" }, "読み込み中…"));
  const load = async () => {
    try {
      const runs = await api("GET", "/api/runs?status=holding&order=desc&limit=500");
      body.replaceChildren(
        el("p", { class: "muted small" },
          "予定時刻にキックしようとしたが、問題があって止めた run です。原因（詳細欄）を直してから「保留解除」を押すと、その場でキックします（予定時刻からどれだけ遅れていても実行されます）。実行しない場合は「スキップ」します。"),
        holdingTable(runs, load)
      );
    } catch (e) {
      body.replaceChildren(el("div", { class: "error-text" }, e.message));
    }
  };
  openModal("保留中の run", body, [el("button", { class: "btn", onclick: closeModal }, "閉じる")]);
  document.querySelector("#modal .modal").classList.add("wide");
  load();
}

function scheduleAlertMessages(s) {
  return [
    s.holding_count ? `保留中の run ${s.holding_count} 件（キックされずに止まっています）` : null,
    ...(s.issues || []).filter((i) => i.level !== "info").map((i) => i.message),
  ].filter(Boolean);
}

/** 予定の警告レベル。保留中の run があればエラー扱い（ビルドが止まっている） */
function scheduleAlertLevel(s) {
  if (!s || s.mode === "memo") return null;
  if (s.holding_count) return "error";
  return s.issue_level || null;
}

async function refreshHealth() {
  const box = document.getElementById("health");
  if (!box) return;
  try {
    const res = await fetch("/api/health");
    const h = await res.json();
    box.replaceChildren(
      h.jenkins_mock ? el("span", { class: "badge mock", title: "JENKINS_MOCK=true" }, "MOCK") : null,
      el("span", { class: "dot " + (h.jenkins === "ok" ? "ok" : "ng"), title: h.jenkins }, ""),
      "Jenkins ",
      el("span", { class: "dot " + (h.dispatcher === "ok" ? "ok" : "ng") }, ""),
      "dispatcher " + (h.dispatcher_last_tick ? fmtDateTime(h.dispatcher_last_tick).split(" ")[1] : "停止")
    );
    box.title = JSON.stringify(h, null, 1);
  } catch (e) {
    box.textContent = "ヘルス取得失敗";
  }
}
