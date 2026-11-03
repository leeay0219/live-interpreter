// The log drawer: captions as they were shown, system lines, adding a term from a line, model cost.
import { t } from "/static/i18n.js";
import { $, fetchApi } from "/static/studio-state.js";
import { termForm } from "/static/studio-settings.js";

// ---------------- log ----------------
const LANG = { en: t("영"), ko: t("한"), ja: t("일"), zh: t("중") };
const SIDE = { self: t("나"), other: t("상대") }; // two-channel input (live.py SIDES)
const rows = new Map();
export function logCaption(m) {
  const key = m.id.split("#")[0];
  let el = rows.get(key);
  if (!el) {
    el = document.createElement("div");
    el.innerHTML = `<div class="t">${now()}&nbsp; ${SIDE[m.side] ? `${SIDE[m.side]}&nbsp; ` : ""}${LANG[m.src_lang] || m.src_lang}${LANG[m.tx_lang] || m.tx_lang}${m.ms ? `&nbsp; ${m.engine} ${m.ms}ms` : ""}</div><div class="s"></div><div class="x"></div>`;
    el.querySelector(".s").textContent = m.src;
    const add = document.createElement("button");
    add.className = "btn addterm";
    add.textContent = t("용어로 추가");
    add.title = t("이 줄에서 잘못 들린 말을 골라(드래그) 바른 표기와 함께 추가");
    add.onmousedown = (e) => e.preventDefault(); // keep the text selection
    add.onclick = () => openTermForm(el);
    el.prepend(add);
    rows.set(key, el);
    addLog(el);
  }
  const x = el.querySelector(".x");
  x.textContent += (x.textContent ? " " : "") + m.tx;
}
function openTermForm(row) {
  const sel = getSelection();
  const picked = sel && row.contains(sel.anchorNode) ? sel.toString().trim() : "";
  const box = $("logTerm");
  const f = termForm(picked || "", (term) => {
    box.replaceChildren();
    if (term) logSys(t(term.always ? "세션 용어 추가: {heard} 대신 {en}, 항상 바꿈. 다음 줄부터 적용" : "세션 용어 추가: {heard} 대신 {en}. 다음 줄부터 적용", term));
  });
  box.replaceChildren(f);
  (picked ? f.en : f.heard).focus();
}
export function logSys(text) {
  const el = document.createElement("div");
  el.className = "sys";
  el.textContent = `${now()}  ${text}`;
  addLog(el);
}
function addLog(el) {
  $("log").prepend(el);
  while ($("log").children.length > 300) $("log").lastChild.remove();
}
export async function showCost() {
  if ($("drawer").hidden) return;
  const u = await fetchApi("/api/usage").then((r) => r.json()).catch(() => null);
  if (u) $("cost").textContent = t("모델 비용 약 ${usd} ({models})", { usd: u.usd.toFixed(3), models: Object.entries(u.models).map(([m, x]) => t("{model} {n}회", { model: m.split("-")[0], n: x.calls })).join(", ") });
}
setInterval(showCost, 5000);
const now = () => new Date().toLocaleTimeString("ko-KR", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
