// Setup screen: session, slides upload and document analysis, caption preview.
import { waitForUpload } from "/static/studio-materials.js";
import { $, S, fetchApi, save, store } from "/static/studio-state.js";
import { renderPeople, renderTerms } from "/static/studio-settings.js";
import { cap, size } from "/static/studio-live.js";
import { closeSheets, materialTools, updateSessionControls } from "/static/studio.js";

// ---------------- setup ----------------
async function loadSession() {
  const response = await fetchApi("/api/session");
  if (!response.ok) throw new Error("세션에 연결하지 못했습니다. 새로 고쳐 주세요.");
  const s = await response.json();
  S.sessionState = s;
  S.focus = s.focus;
  store.people = s.people; store.terms = s.session_terms;
  cap.sessionId = s.session_id;
  S.eventPeople = s.people;
  renderPeople(); renderTerms(); updateSessionControls();
  $("costNote").hidden = !s.hosted;
  $("recHtml").hidden = !s.document_formats?.includes("html");
}
const sessionReady = loadSession().catch(() => null);
export async function ensureSession() {
  await sessionReady;
  if (!S.sessionState.session_id) await loadSession();
}
fetchApi("/api/deck").then((r) => r.json()).then((d) => d && showDeck(d)).catch(() => {});
// slides
const drop = $("drop");
drop.onclick = () => $("deckFile").click();
$("deckFile").onchange = () => $("deckFile").files[0] && upload($("deckFile").files[0]);
// a PDF can be dropped anywhere on the setup screen
$("setup").ondragover = (e) => e.preventDefault();
$("setup").ondrop = (e) => { e.preventDefault(); e.dataTransfer.files[0] && upload(e.dataTransfer.files[0]); };
$("deckChange").onclick = () => $("deckFile").click();
$("deckRemove").onclick = async () => {
  const r = await fetchApi("/api/deck", { method: "DELETE" }).catch(() => null);
  if (!r?.ok) return ($("deckError").textContent = "자료를 지우지 못했습니다. 다시 시도해 주세요.");
  S.uploadVersion++;
  clearTimeout(S.analysisTimer);
  S.deck = null;
  $("deckInfo").hidden = true;
  $("deckActions").hidden = true;
  $("analysisStatus").hidden = true;
  $("deckError").textContent = "";
  closeSheets(false);
  drop.hidden = false;
  renderPreview();
};
async function upload(file) {
  if (S.uploading) return;
  $("deckError").textContent = "";
  if (!/\.pdf$/i.test(file.name)) {
    $("deckError").textContent = "PowerPoint 파일은 PDF로 저장해서 올려 주세요.";
    return;
  }
  if (file.size > 40 * 1024 * 1024) {
    $("deckError").textContent = "40MB 이하의 PDF를 올려 주세요.";
    return;
  }
  S.uploading = true;
  $("stepDeck").setAttribute("aria-busy", "true");
  [drop, $("deckChange"), $("deckRemove"), $("analysisRetry")].forEach(b => b.disabled = true);
  const version = ++S.uploadVersion;
  drop.querySelector("b").textContent = "읽는 중";
  if (S.deck) $("analysisState").textContent = "새 자료 읽는 중";
  const fd = new FormData();
  fd.append("file", file, file.name);
  try {
    await ensureSession();
    const r = await fetchApi("/api/deck", { method: "POST", body: fd });
    if (version !== S.uploadVersion) return;
    if (!r.ok) throw new Error(await r.text());
    const doc = await waitForUpload(fetchApi, await r.json(), {
      isCurrent: () => version === S.uploadVersion,
      onProgress: seconds => (drop.querySelector("b").textContent = `읽는 중, ${seconds}초`),
    });
    if (!doc || version !== S.uploadVersion) return;
    showDeck(doc);
  } catch (e) {
    if (version === S.uploadVersion) {
      $("deckError").textContent = e.message || "자료를 올리지 못했습니다.";
      if (S.deck) renderAnalysis(S.deck.analysis || { state: "unavailable" });
    }
  } finally {
    if (version === S.uploadVersion) {
      drop.querySelector("b").textContent = "PDF 올리기";
      $("deckFile").value = "";
      S.uploading = false;
      $("stepDeck").removeAttribute("aria-busy");
      [drop, $("deckChange"), $("deckRemove"), $("analysisRetry")].forEach(b => b.disabled = false);
    }
  }
}
// caption preview: same classes as the live view, sized as on a 1920 px wide screen
export function renderPreview() {
  const st = $("pvStage");
  st.classList.toggle("nodeck", !S.deck);
  st.classList.toggle("overlay", !!S.deck && store.capPos === "overlay");
  st.classList.toggle("light", store.theme === "light");
  $("pvCap").classList.toggle("light", store.theme === "light");
  $("pvSlide").hidden = !S.deck;
  if (S.deck) $("pvSlide").src = S.deck.pages[0].url;
  const base = S.deck ? 0.9 : 1.3;
  $("pvCap").style.setProperty("--cap-scale", (base * store.size * st.clientWidth / 1920).toFixed(3));
  $("pvCap").classList.toggle("no-en", !store.src);
  $("pvSize").textContent = `${Math.round(store.size * 100)}%`;
  $("pvSrc").checked = store.src;
  const bg = store.theme === "light" ? "light" : S.deck && store.capPos === "overlay" ? "overlay" : "dark";
  document.querySelectorAll("#pvBg button").forEach((b) => {
    b.classList.toggle("on", b.dataset.bg === bg);
    b.setAttribute("aria-pressed", String(b.dataset.bg === bg));
    if (b.dataset.bg === "overlay") b.disabled = !S.deck;
  });
}
document.querySelectorAll("#pvBg button").forEach((b) => (b.onclick = () => {
  store.theme = b.dataset.bg === "light" ? "light" : "dark";
  if (b.dataset.bg !== "light") store.capPos = b.dataset.bg === "overlay" ? "overlay" : "below";
  save(); renderPreview();
}));
$("pvSmaller").onclick = () => size(-0.1);
$("pvBigger").onclick = () => size(0.1);
$("pvSrc").onchange = () => { store.src = $("pvSrc").checked; save(); renderPreview(); };
new ResizeObserver(renderPreview).observe($("pvStage"));
function showDeck(d) {
  clearTimeout(S.analysisTimer);
  if (S.deck?.id !== d.id) closeSheets(false);
  S.deck = d;
  S.page = 0;
  drop.hidden = true;
  $("deckInfo").hidden = false;
  $("deckActions").hidden = false;
  $("deckThumb").src = d.pages[0].url;
  $("deckName").textContent = d.name;
  $("deckName").title = d.name;
  $("deckPages").textContent = `${d.pages.length}장`;
  d.pages.slice(0, 3).forEach((p) => (new Image().src = p.url)); // bound prefetch for long decks
  renderPreview();
  renderAnalysis(d.analysis || { state: "unavailable" });
  pollAnalysis(d.id);
}
export function renderAnalysis(j) {
  $("analysisStatus").hidden = !S.deck;
  $("analysisStatus").dataset.state = j.state;
  const state = j.state === "done" ? "자료 맥락 반영됨"
    : j.stage || (j.state === "unavailable" ? "현재 서버는 슬라이드 맥락만 반영합니다." : "자료 분석 중");
  if ($("analysisState").textContent !== state) $("analysisState").textContent = state;
  $("analysisView").hidden = !["done", "partial"].includes(j.state);
  $("analysisRetry").hidden = !["error", "empty", "partial"].includes(j.state);
  $("briefSummary").textContent = j.summary || "";
  $("briefNote").textContent = (j.note || "") + (j.failed_pages?.length ? ` 읽지 못한 쪽: ${j.failed_pages.join(", ")}.` : "");
  for (const [field, key, detail, target] of [["people", "name", "role", "briefPeople"], ["terms", "term", "meaning", "briefTerms"]]) {
    const items = j[field] || [];
    $(target + "Section").hidden = !items.length;
    $(target).replaceChildren(...items.map((item) => {
      const li = document.createElement("li"), name = document.createElement("b"), refs = document.createElement("span"), desc = document.createElement("p");
      name.textContent = item[key];
      refs.className = "brief-pages";
      refs.textContent = `${item.pages.join(", ")}쪽`;
      const edit = document.createElement("button");
      edit.className = "btn small"; edit.textContent = "근거와 보정";
      edit.onclick = () => materialTools.evidence(S.deck, j, field, item);
      desc.textContent = item[detail] || "";
      li.append(name, refs, desc, edit);
      return li;
    }));
  }
  for (const excluded of j.excluded || []) {
    const li = document.createElement("li"), restore = document.createElement("button");
    restore.className = "btn small"; restore.textContent = `${excluded.label} 다시 참고`;
    restore.onclick = async () => {
      try { renderAnalysis(await materialTools.edit(S.deck, j, { id: excluded.id, restore: true })); }
      catch (e) { $("briefNote").textContent = e.message; }
    };
    li.append(restore); $("briefTerms").append(li); $("briefTermsSection").hidden = false;
  }
}
async function pollAnalysis(id) {
  clearTimeout(S.analysisTimer);
  const r = await fetchApi("/api/deck/analysis").catch(() => null);
  if (S.deck?.id !== id) return;
  if (r?.status === 404) return; // an older local server can still preview the updated static UI
  if (!r?.ok) {
    $("analysisState").textContent = "분석 상태 다시 확인 중";
    S.analysisTimer = setTimeout(() => pollAnalysis(id), 3000);
    return;
  }
  const j = await r.json();
  if (S.deck?.id !== id) return;
  if (!j || j.deck_id !== id) {
    closeSheets(false);
    $("analysisState").textContent = "다른 창에서 자료가 바뀌었습니다. 새로 고쳐 주세요.";
    $("analysisView").hidden = $("analysisRetry").hidden = true;
    return;
  }
  S.deck.analysis = j;
  renderAnalysis(j);
  if (j.state === "running") S.analysisTimer = setTimeout(() => pollAnalysis(id), 2000);
}
$("analysisRetry").onclick = async () => {
  const id = S.deck?.id;
  if (!id) return;
  $("analysisRetry").disabled = true;
  try {
    const r = await fetchApi("/api/deck/analysis", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ deck_id: id }) });
    if (!r.ok) throw new Error(await r.text());
    if (S.deck?.id === id) { renderAnalysis(await r.json()); pollAnalysis(id); }
  } catch (e) {
    if (S.deck?.id === id) $("analysisState").textContent = e.message || "분석을 다시 시작하지 못했습니다.";
  } finally {
    $("analysisRetry").disabled = false;
  }
};
