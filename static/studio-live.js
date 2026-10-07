// Live view: start and stop, slides, the control bar, sharing and keyboard shortcuts.
import { CaptionClient } from "/static/captions.js";
import { micError } from "/static/studio-text.js";
import { $, S, clientId, fetchApi, save, store } from "/static/studio-state.js";
import { ensureSession, renderPreview } from "/static/studio-setup.js";
import { refreshRecord } from "/static/studio-record.js";
import { soundCheck, stopCheck } from "/static/studio-audio.js";
import { applyContext } from "/static/studio-settings.js";
import { logCaption, logSys, showCost } from "/static/studio-log.js";
import { materialTools, updateCompletion, updateSessionControls } from "/static/studio.js";

// ---------------- live ----------------
const STATES = { replaced: "다른 창에서 시작함", live: "통역 중", healthy: "통역 중", connecting: "연결 복구 중", connected: "대기", stopped: "종료", error: "오류", offline: "연결 복구 중",
  paused: "잠시 멈춤", pausing: "마지막 자막 정리 중", silence: "입력 소리가 작습니다", audio_missing: "음성이 안 들어옵니다",
  recognition_delayed: "받아쓰기가 지연됩니다", translation_delayed: "번역이 지연됩니다", overloaded: "입력이 밀려 멈췄습니다", device_lost: "음성 장치 연결이 끊겼습니다" };
export const cap = new CaptionClient({
  clientId,
  el: $("captions"),
  onStatus: (state, detail) => {
    $("dot").className = `dot ${state}`;
    $("state").textContent = STATES[state] || state;
    if (state === "error") logSys(`오류: ${detail || ""}`);
    if (state === "control_error") { $("setupError").textContent = detail; logSys(detail); }
    if (state === "pausing") $("pause").disabled = true;
    if (state === "paused") { $("pause").disabled = false; $("pause").textContent = "이어서 하기"; }
    if (state === "replaced") {
      logSys(detail);
      stop().then(() => { S.finishing = false; updateCompletion(); $("setupError").textContent = detail; });
    }
  },
  onLatency: (ms, engine) => ($("latency").textContent = `${engine} ${ms}ms`),
  onLevel: (peak) => ($("level").style.width = `${Math.min(100, peak * 140)}%`),
  onMessage: (m) => {
    if (m.type === "caption") logCaption(m);
    if (m.type === "context") {
      S.focus = m.focus;
      if (S.deck?.id === m.deck_id) {
        S.page = m.page;
        $("slide").src = S.deck.pages[S.page].url;
        $("pageNo").textContent = `${S.page + 1} / ${S.deck.pages.length}`;
      }
      $("focusMode").setAttribute("aria-pressed", String(S.focus === "qa"));
      $("focusMode").textContent = S.focus === "qa" ? "발표로 돌아가기" : "질의응답";
    }
    if (m.type === "lifecycle") {
      if (S.sessionState.session_id && S.sessionState.session_id !== m.session_id) {
        location.reload(); return;
      }
      S.sessionState.phase = m.phase;
      if (m.phase === "ended") S.finishing = false;
      updateSessionControls();
      if (m.phase === "ended") refreshRecord();
    }
  },
});
$("start").onclick = async () => {
  try { await ensureSession(); }
  catch (e) { $("setupError").textContent = e.message; return; }
  await materialTools.stopRehearsal();
  $("setupError").textContent = "";
  if (S.sessionState.phase === "paused" && S.sessionState.controller !== clientId) {
    const claimed = await fetchApi("/api/session/claim", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: S.sessionState.session_id }) }).catch(() => null);
    if (!claimed?.ok) { $("setupError").textContent = "현재 입력 창이 연결되어 있습니다. 그 창에서 이어서 하세요."; return; }
    S.sessionState = await claimed.json();
  }
  const v = $("input").value;
  let source;
  if (v === "screen") source = { kind: "screen" };
  else if (v === "file") {
    const f = $("audioFile").files[0];
    if (!f) return ($("setupError").textContent = "재생할 녹음 파일을 고르세요.");
    source = { kind: "file", file: f, monitor: $("monitor").checked };
  } else source = { kind: "device", deviceId: v.slice(4) || undefined, raw: !$("dsp").checked };
  let ctx, preparing = true;
  try {
    $("start").disabled = true;
    stopCheck();
    ctx = await applyContext();
    preparing = false;
    await cap.startAudio([source], { intent: S.sessionState.phase === "prepared" ? "new" : "resume",
      onEnded: () => { logSys("녹음 파일 끝"); setTimeout(stop, 2500); } });
  } catch (err) {
    $("start").disabled = false;
    soundCheck();
    $("setupError").textContent = preparing ? (err.message || "통역 설정을 적용하지 못했습니다.") : source?.kind === "device" ? micError(err)
      : err.name === "NotAllowedError" ? "권한이 거부됐거나 공유를 취소했습니다." : (err.message || String(err));
    return;
  }
  enterLive();
  const titles = ctx.skills.map((n) => S.skillDefs.find((d) => d.name === n)?.title || n);
  logSys(`시작, ${$("input").selectedOptions[0].text}` + (titles.length ? `, ${titles.join(", ")}` : "") + (ctx.people ? `, 발표자 ${ctx.people}명` : "") + (ctx.terms ? `, 세션 용어 ${ctx.terms}개` : "") + (ctx.log_content ? ", 대화 내용 로그 기록함" : ", 대화 내용 로그 기록 안 함"));
};
function enterLive() {
  $("setup").hidden = true;
  $("live").hidden = false;
  $("live").classList.toggle("captions-only", !S.deck);
  $("nav").hidden = !S.deck;
  applyPrefs();
  if (S.deck) show(S.page);
  else cap.setPage(null, 0, S.focus);
  wake();
}
async function stop() {
  S.finishing = true;
  stopCheck();
  updateCompletion();
  await cap.stopMic();
  if (document.fullscreenElement) document.exitFullscreen();
  $("live").hidden = true;
  $("setup").hidden = false;
  $("drawer").hidden = $("share").hidden = true;
  $("start").disabled = false;
  refreshRecord();
  setTimeout(refreshRecord, 3000); // the last lines may still be translating
}
$("stop").onclick = () => confirm("통역을 끝내고 기록을 정리할까요?") && stop();
function show(i) {
  if (!S.deck) return;
  S.page = Math.max(0, Math.min(S.deck.pages.length - 1, i));
  const p = S.deck.pages[S.page];
  $("slide").src = p.url;
  $("pageNo").textContent = `${S.page + 1} / ${S.deck.pages.length}`;
  // the slide's own words are the best context for what is being said now
  cap.setPage(S.deck.id, S.page, S.focus);
  if (S.deck.pages[S.page + 1]) new Image().src = S.deck.pages[S.page + 1].url;
}
$("prev").onclick = () => show(S.page - 1);
$("next").onclick = () => show(S.page + 1);
function applyPrefs() {
  const base = S.deck ? 0.9 : 1.3;
  // deck.css sizes captions for a 1920 px wide stage
  $("captions").style.setProperty("--cap-scale", (base * store.size * Math.max(0.5, innerWidth / 1920)).toFixed(3));
  $("captions").classList.toggle("no-en", !store.src);
  $("live").classList.toggle("overlay", !!S.deck && store.capPos === "overlay");
  $("live").classList.toggle("light", store.theme === "light");
  $("tPos").classList.toggle("on", store.capPos === "overlay");
  $("tSrc").classList.toggle("on", store.src);
  $("tCap").classList.toggle("on", !$("live").classList.contains("nocap"));
}
addEventListener("resize", applyPrefs);
export const size = (d) => { store.size = Math.round(Math.max(0.6, Math.min(1.8, store.size + d)) * 10) / 10; save(); applyPrefs(); renderPreview(); };
$("smaller").onclick = () => size(-0.1);
$("bigger").onclick = () => size(0.1);
$("tSrc").onclick = () => { store.src = !store.src; save(); applyPrefs(); };
$("tCap").onclick = () => { $("live").classList.toggle("nocap"); applyPrefs(); };
$("tPos").onclick = () => { store.capPos = store.capPos === "overlay" ? "below" : "overlay"; save(); applyPrefs(); };
const full = () => (document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen());
$("tFull").onclick = full;
$("tShare").onclick = () => { $("share").hidden = !$("share").hidden; };
$("tLog").onclick = () => { $("drawer").hidden = !$("drawer").hidden; showCost(); };
$("closeLog").onclick = () => ($("drawer").hidden = true);
document.querySelectorAll("#bar button").forEach((b) => b.addEventListener("click", () => b.blur()));
document.querySelectorAll("[data-open]").forEach((b) => (b.onclick = () => window.open(b.dataset.open + (store.theme === "light" ? "&bg=light" : ""), "_blank")));
document.querySelectorAll("[data-copy]").forEach((b) => (b.onclick = () => copy(location.origin + b.dataset.copy, b)));
// e.currentTarget is null once the handler has awaited, so the button is taken first
$("copyOperator").onclick = async (e) => {
  const btn = e.currentTarget;
  const r = await fetchApi("/api/oplink").then((r) => r.json()).catch(() => null);
  if (r) copy(r.url, btn);
};
$("showQr").onclick = () => {
  $("qrImg").src = `/caplink.svg?t=${Date.now()}`;
  $("qr").hidden = false;
  $("share").hidden = true;
};
$("qr").onclick = () => ($("qr").hidden = true);
$("copyViewer").onclick = async (e) => {
  const btn = e.currentTarget;
  const r = await fetchApi("/caplink").then((r) => r.json()).catch(() => null);
  copy(r ? r.url : location.origin + "/captions?view=list", btn);
};
async function copy(text, btn) {
  await navigator.clipboard.writeText(text).catch(() => prompt("복사하세요", text));
  const label = btn.firstChild;
  const t = label.textContent;
  label.textContent = "복사했습니다";
  setTimeout(() => (label.textContent = t), 1500);
}
addEventListener("keydown", (e) => {
  if ($("live").hidden || e.target.closest("select, input")) return;
  const k = e.key;
  if (k === "Escape" && !$("qr").hidden) return ($("qr").hidden = true);
  if (["ArrowRight", "PageDown", " ", "ArrowDown"].includes(k)) show(S.page + 1);
  else if (["ArrowLeft", "PageUp", "ArrowUp"].includes(k)) show(S.page - 1);
  else if (k === "Home") show(0);
  else if (k === "End" && S.deck) show(S.deck.pages.length - 1);
  else if (k === "c" || k === "C") $("tCap").click();
  else if (k === "e" || k === "E") $("tSrc").click();
  else if (k === "+" || k === "=") size(0.1);
  else if (k === "-") size(-0.1);
  else if (k === "b" || k === "B" || k === ".") $("live").classList.toggle("blank");
  else if (k === "f" || k === "F") full();
  else if (k === "l" || k === "L") $("tLog").click();
  else return;
  e.preventDefault();
});
// the control bar fades out while the mouse is still
let idle;
function wake() {
  $("bar").classList.remove("idle");
  clearTimeout(idle);
  idle = setTimeout(() => $("bar").classList.add("idle"), 2500);
}
addEventListener("mousemove", wake);
