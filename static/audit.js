/* ログ画面（仕様書 11.4 の監査ログ） */
"use strict";

renderHeader("/audit");

const TYPE_LABEL = { schedule: "スケジューラ", run: "run", target: "アイテム", category: "カテゴリ", system: "システム" };
const filters = ["f-type", "f-target", "f-action", "f-from", "f-to"].map((id) => document.getElementById(id));

// URL の ?type=&target= で絞り込んで開けるようにする
const params = new URLSearchParams(location.search);
if (params.get("type")) filters[0].value = params.get("type");
if (params.get("target")) filters[1].value = params.get("target");

async function load() {
  const q = new URLSearchParams();
  const [type, target, action, from, to] = filters.map((f) => f.value.trim());
  if (type) q.set("type", type);
  if (target) q.set("target", target);
  if (action) q.set("action", action);
  if (from) q.set("from", from);
  if (to) q.set("to", to);
  let rows;
  try {
    rows = await api("GET", `/api/audit?${q}`);
  } catch (e) {
    toast(e.message, "error");
    return;
  }
  const box = document.getElementById("audit-list");
  if (!rows.length) {
    box.replaceChildren(el("p", { class: "muted" }, "該当するログはありません。"));
    return;
  }
  box.replaceChildren(
    el("table", { class: "table small audit-table" },
      el("thead", {}, el("tr", {}, ["日時", "実行者", "操作", "対象", "詳細"].map((h) => el("th", {}, h)))),
      el("tbody", {}, rows.map((a) =>
        el("tr", {},
          el("td", { style: { whiteSpace: "nowrap" } }, fmtDateTime(a.at, true)),
          el("td", {}, a.actor),
          el("td", { class: "mono" }, a.action),
          el("td", {}, a.target_type ? `${TYPE_LABEL[a.target_type] || a.target_type}${a.target_id ? " #" + a.target_id : ""}` : ""),
          el("td", {}, a.detail ? el("details", { class: "json" }, el("summary", {}, summarize(a.detail)), el("pre", {}, JSON.stringify(a.detail, null, 2))) : "")))))
  );
}

function summarize(d) {
  const s = JSON.stringify(d);
  return s.length > 80 ? s.slice(0, 80) + "…" : s;
}

filters.forEach((f) => f.addEventListener("change", load));
filters[2].addEventListener("input", debounce(load, 300));
load();
