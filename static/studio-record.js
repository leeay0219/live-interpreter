// The last session's record and its write-up.
import { mdToHtml } from "/static/studio-text.js";
import { $, S, fetchApi, isCompleted, save, store } from "/static/studio-state.js";
import { updateCompletion } from "/static/studio.js";

// the last session: the server keeps its finished lines in memory until the next Start, and writes them up as a
// background job (minutes for an hour). The job id is kept so a reload picks the job up again.
export let recLines = 0, wuTimer, writeupRunning = false;
export async function refreshRecord() {
  const r = await fetchApi("/api/record").then((r) => (r.ok ? r.json() : null)).catch(() => null);
  if (!r) return;
  recLines = r.lines || 0;
  $("recSize").textContent = recLines ? `자막 ${recLines}줄, ${r.minutes ? `약 ${r.minutes}분` : "1분 미만"}` : "기록 없음";
  $("wuStart").hidden = $("recClear").hidden = $("recDl").hidden = $("wuOpts").hidden = !recLines;
  $("recent").hidden = isCompleted() || (!recLines && !store.writeup);
  if (!isCompleted() && $("recent").hidden && !$("sheetRecent").hidden) $("sheetRecent").hidden = true;
  updateCompletion();
}
function wuStatus(cls, text) {
  $("wuStatus").hidden = false;
  $("wuStatus").className = cls;
  $("wuStatus").textContent = text;
}
const clock = (s) => (s < 60 ? `${s}초` : `${Math.floor(s / 60)}분 ${s % 60}초`);
async function pollWriteup() {
  clearTimeout(wuTimer);
  const id = store.writeup;
  if (!id) return;
  const r = await fetchApi(`/api/writeup/${id}`).catch(() => null);
  if (r?.status === 404) { // the server restarted and the job is gone
    writeupRunning = false;
    delete store.writeup; save();
    $("wuStatus").hidden = $("wuDone").hidden = true;
    return refreshRecord();
  }
  const j = r?.ok ? await r.json() : null;
  $("recent").hidden = isCompleted();
  if (!j || j.state === "running") {
    writeupRunning = true;
    $("wuStart").disabled = true;
    wuStatus("muted", j ? `${j.stage || "정리하는 중"}, ${clock(j.seconds)} 지남. 10분이 넘으면 중단하고 다시 시도할 수 있습니다.` : "상태를 확인하지 못해 다시 확인합니다.");
    wuTimer = setTimeout(pollWriteup, 4000);
    return;
  }
  writeupRunning = false;
  $("wuStart").disabled = S.finishing;
  if (j.state === "error") {
    delete store.writeup; save();
    updateCompletion();
    return wuStatus("err", `정리하지 못했습니다. ${j.error || ""}`);
  }
  $("wuStatus").hidden = true;
  $("wuDone").hidden = false;
  $("wuDocx").href = `/api/writeup/${id}/docx`;
  $("wuMd").href = `/api/writeup/${id}/md`;
  $("wuHtml").href = `/api/writeup/${id}/html`;
  $("wuOpen").href = `/api/writeup/${id}/html?view=1`;
  $("wuHtml").hidden = $("wuOpen").hidden = !j.formats?.includes("html");
  $("wuPreview").innerHTML = mdToHtml(j.md || "");
  $("wuReview").hidden = !j.review_available;
  $("wuReviewDownload").href = `/api/writeup/${id}/review`;
  updateCompletion();
}
$("wuStart").onclick = async () => {
  writeupRunning = true;
  $("wuDone").hidden = true;
  $("wuStart").disabled = true;
  const r = await fetchApi("/api/writeup", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lang: $("wuLang").value, template: $("wuTemplate").value, session_id: S.sessionState.session_id }) }).catch(() => null);
  if (!r?.ok) {
    writeupRunning = false;
    $("wuStart").disabled = false;
    updateCompletion();
    return wuStatus("err", r ? await r.text() : "서버에 연결하지 못했습니다.");
  }
  store.writeup = (await r.json()).id;
  store.wuLines = recLines;
  save();
  updateCompletion();
  pollWriteup();
};
for (const k of ["wuLang", "wuTemplate"]) {
  if (store[k]) $(k).value = store[k];
  $(k).onchange = () => { store[k] = $(k).value; save(); };
}
$("wuClose").onclick = () => {
  delete store.writeup; save();
  $("wuDone").hidden = true;
  refreshRecord();
};
$("recClear").onclick = async () => {
  if (!confirm("방금 끝난 세션의 기록을 지울까요? 정리한 문서는 남습니다.")) return;
  await fetchApi("/api/record", { method: "DELETE" });
  refreshRecord();
};
refreshRecord().then(pollWriteup);
