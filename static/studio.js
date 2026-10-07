// Studio entry: sheets, session controls, the finished-session view and start-up.
import { listMics } from "/static/captions.js";
import { MaterialTools } from "/static/studio-materials.js";
import { $, S, clientId, fetchApi, isCompleted, save, store } from "/static/studio-state.js";
import { ensureSession, renderAnalysis, renderPreview } from "/static/studio-setup.js";
import { recLines, writeupRunning } from "/static/studio-record.js";
import { renderInputs, soundCheck, stopCheck } from "/static/studio-audio.js";
import { applyContext } from "/static/studio-settings.js";
import { cap } from "/static/studio-live.js";
import { logSys } from "/static/studio-log.js";

// each optional setting is one row with its current value; it is edited in a sheet over the preview
function renderSummary() {
  const people = (store.people || []).filter((p) => p.name?.trim()).length, terms = store.terms.length;
  $("peopleCount").textContent = people ? `${people}명` : "없음";
  $("termsCount").textContent = terms ? `${terms}개` : "없음";
  const skills = store.skills.map((n) => S.skillDefs.find((d) => d.name === n)?.title || n);
  const active = [...skills, ...(people ? [`발표자 ${people}명`] : []), ...(terms ? [`용어 ${terms}개`] : [])];
  $("advValue").textContent = active.length ? active.join(", ") : "기본";
  $("advValue").title = active.join(", ");
}
let sheetTrigger = null;
export function closeSheets(restoreFocus = true) {
  if (!$("sheetRehearsal").hidden) materialTools.stopRehearsal();
  document.querySelectorAll(".sheet").forEach((sh) => (sh.hidden = !(isCompleted() && sh.id === "sheetRecent")));
  document.querySelectorAll("[data-sheet]").forEach((b) => b.setAttribute("aria-expanded", "false"));
  if (restoreFocus) sheetTrigger?.focus({ preventScroll: true });
  sheetTrigger = null;
}
function openSheet(trigger) {
  const sh = $(trigger.dataset.sheet), wasOpen = !sh.hidden;
  closeSheets(false);
  if (wasOpen) return;
  sheetTrigger = trigger;
  sh.hidden = false;
  trigger.setAttribute("aria-expanded", "true");
  sh.querySelector("[data-close]").focus({ preventScroll: true });
}
document.querySelectorAll(".sheet").forEach((sh) => {
  const title = sh.querySelector("header b");
  title.id ||= `${sh.id}Title`;
  sh.setAttribute("role", "dialog");
  sh.setAttribute("aria-labelledby", title.id);
});
document.querySelectorAll("[data-sheet]").forEach((b) => {
  b.setAttribute("aria-controls", b.dataset.sheet);
  b.setAttribute("aria-expanded", "false");
  b.onclick = () => openSheet(b);
});
document.querySelectorAll("[data-close]").forEach((b) => (b.onclick = () => closeSheets()));
$("briefCorrect").onclick = () => openSheet(document.querySelector('[data-sheet="adv"]'));
addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("setup").hidden) closeSheets();
});
$("setup").addEventListener("input", renderSummary);
$("setup").addEventListener("change", renderSummary);
$("setup").addEventListener("click", () => setTimeout(renderSummary));
setInterval(renderSummary, 1500); // deck uploads and suggestions finish asynchronously
renderInputs();
renderPreview();
renderSummary();
listMics().then((m) => { S.mics = m; renderInputs(); soundCheck(); });
export function updateSessionControls() {
  const phase = S.sessionState.phase || "prepared";
  $("newSession").hidden = phase !== "ended";
  $("start").hidden = phase === "ended";
  $("start").textContent = phase === "paused" || phase === "live" ? "이어서 하기" : "통역 시작";
  $("rehearse").disabled = phase === "live";
  if (phase === "paused") { $("pause").disabled = false; $("pause").textContent = "이어서 하기"; }
  else if (phase === "live") { $("pause").disabled = false; $("pause").textContent = "잠시 멈춤"; }
  updateCompletion();
}
export function updateCompletion() {
  const completed = isCompleted();
  const wasCompleted = $("workspace").classList.contains("is-complete");
  $("workspace").classList.toggle("is-complete", completed);
  $("completed").hidden = !completed;
  $("preparation").hidden = $("preparationOptions").hidden = $("pv").hidden = completed;
  $("newSession").hidden = !completed;
  $("newSession").disabled = S.finishing;
  $("recordPanelTitle").textContent = completed ? "정리 문서" : "방금 끝난 세션";
  const downloads = completed ? $("completedDownloads") : $("recordDownloadsHome");
  const manage = completed ? $("completedManage") : $("recordManageHome");
  if ($("recordDownloads").parentElement !== downloads) downloads.append($("recordDownloads"));
  if ($("recordManage").parentElement !== manage) manage.append($("recordManage"));
  $("writeupEmpty").hidden = !completed || !!store.writeup;
  $("writeupEmpty").textContent = recLines
    ? "발표 요약이나 회의록을 선택하면 방금 끝난 통역을 정리해 보여드립니다."
    : S.finishing ? "마지막 자막을 기록에 담고 있습니다." : "저장된 자막이 없어 정리할 내용이 없습니다.";
  $("wuStart").disabled = S.finishing || writeupRunning || !recLines;
  if (completed) {
    stopCheck();
    $("completedTitle").textContent = S.finishing ? "통역을 마무리하고 있습니다" : "통역을 마쳤습니다";
    $("completedMeta").textContent = S.finishing ? "마지막 자막 정리 중" : $("recSize").textContent;
    $("recent").hidden = true;
    $("sheetRecent").hidden = false;
    $("sheetRecent").setAttribute("role", "region");
  } else {
    $("sheetRecent").setAttribute("role", "dialog");
    if (wasCompleted) $("sheetRecent").hidden = true;
  }
}
$("pause").onclick = async () => {
  try {
    if (cap.paused) await cap.resume();
    else await cap.pause();
  } catch (e) { logSys(e.message); $("state").textContent = e.message; }
};
$("focusMode").onclick = () => cap.setPage(S.deck?.id || null, S.page, S.focus === "qa" ? "presentation" : "qa");
$("newSession").onclick = async () => {
  if (recLines && !confirm("기록을 내려받았나요? 새 세션을 준비하면 현재 기록과 자료를 지웁니다.")) return;
  try {
    const r = await fetchApi("/api/session/new", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: S.sessionState.session_id }) });
    if (!r.ok) throw new Error(await r.text());
    delete store.writeup; delete store.wuLines; save();
    location.reload();
  } catch (e) { $("completedError").textContent = e.message; }
};
export const materialTools = new MaterialTools({
  $, api: fetchApi, clientId, applyContext: async () => { await ensureSession(); stopCheck(); return applyContext(); },
  onDeckAnalysis: (analysis) => { if (S.deck) S.deck.analysis = analysis; renderAnalysis(analysis); },
  open: (id) => {
    const trigger = document.activeElement;
    closeSheets(false); sheetTrigger = trigger;
    $(id).hidden = false; $(id).querySelector("[data-close]").focus({ preventScroll: true });
  },
  prepareAudio: () => {
    const value = $("input").value;
    if (value === "screen") return { kind: "screen" };
    if (value === "file") {
      const file = $("audioFile").files[0];
      if (!file) throw new Error("확인할 녹음 파일을 선택하세요.");
      return { kind: "file", file, monitor: false };
    }
    return { kind: "device", deviceId: value.slice(4) || undefined, raw: !$("dsp").checked };
  },
});
