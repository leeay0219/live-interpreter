// Audio input choice and the sound check before Start.
import { listMics } from "/static/captions.js";
import { micError } from "/static/studio-text.js";
import { $, S, isCompleted, save, store } from "/static/studio-state.js";

// audio input: devices first, then meeting-tab audio and a test file
const HINTS = {
  screen: "시작하면 공유 창이 뜹니다. 회의 탭을 고르고 탭 오디오 공유를 켜세요. 데스크톱 앱은 BlackHole 같은 가상 오디오 장치를 위 목록에서 고르면 됩니다.",
  file: "녹음 파일을 실제 속도로 흘려 리허설합니다. 열려 있는 자막 화면에도 그대로 나가니 라이브용 서버에서는 쓰지 마세요.",
  device: "현장에서는 음향 콘솔 출력을 USB 오디오 인터페이스로 받으세요. 홀 소리를 노트북 마이크로 받으면 인식률이 크게 떨어집니다.",
  mix: "이어폰으로 들으세요. 스피커로 들으면 상대 목소리가 마이크로 다시 들어가 두 번 통역됩니다.",
};
const builtIn = (label) => /macbook|built-?in|내장|internal/i.test(label || "");
export function renderInputs() {
  const sel = $("input");
  sel.replaceChildren(
    ...(S.mics.length ? S.mics : [{ deviceId: "", label: "기본 마이크" }]).map((m) => new Option(m.label || "마이크", `dev:${m.deviceId}`)),
    new Option("회의 탭 소리 (Zoom, Teams, Chime 웹)", "screen"),
    new Option("녹음 파일로 테스트", "file"),
  );
  if (store.input && [...sel.options].some((o) => o.value === store.input)) sel.value = store.input;
  const mic = $("addMicDevice");
  mic.replaceChildren(...(S.mics.length ? S.mics : [{ deviceId: "", label: "기본 마이크" }]).map((m) => new Option(m.label || "마이크", m.deviceId)));
  if (store.addMicId !== undefined && [...mic.options].some((o) => o.value === store.addMicId)) mic.value = store.addMicId;
  $("addMic").checked = !!store.addMic;
  onInput();
}
// The meeting's audio plus the operator's own microphone, mixed into one stream, so both sides are interpreted.
// Only for a meeting tab or a device (a virtual device such as BlackHole carrying a desktop meeting app).
function mixing() {
  const v = $("input").value;
  return $("addMic").checked && (v === "screen" || v.startsWith("dev:")) && !(v.startsWith("dev:") && v.slice(4) === $("addMicDevice").value);
}
// What Start and the rehearsal capture. Throws when a test file is chosen but missing.
export function audioSources(missingFile = "재생할 녹음 파일을 고르세요.") {
  const v = $("input").value;
  if (v === "file") {
    const file = $("audioFile").files[0];
    if (!file) throw new Error(missingFile);
    return [{ kind: "file", file, monitor: $("monitor").checked }];
  }
  const main = v === "screen" ? { kind: "screen" } : { kind: "device", deviceId: v.slice(4) || undefined, raw: !$("dsp").checked };
  if (!mixing()) return [main];
  // two channels, so each line is known to come from the operator (0) or the meeting (1); live.py SIDES reads them.
  // The operator's mic hears the room and maybe the speakers: browser echo cancellation stays on for it.
  return [{ ...main, channel: 1 }, { kind: "device", deviceId: $("addMicDevice").value || undefined, raw: false, channel: 0 }];
}
function onInput() {
  const v = $("input").value;
  store.input = v; save();
  const kind = v.startsWith("dev:") ? "device" : v;
  $("mixRow").hidden = kind === "file";
  $("addMicDevice").hidden = !$("addMic").checked || kind === "file";
  $("hint").textContent = kind === "device" ? "" : HINTS[kind];
  $("mixHint").textContent = mixing() ? HINTS.mix : "";
  $("input").title = kind === "device" ? HINTS.device : "";
  $("fileRow").hidden = kind !== "file";
  // browser noise processing helps a laptop mic and hurts a clean line-in; the user can override in 고급 설정
  if (kind === "device" && store.dsp === undefined) $("dsp").checked = builtIn($("input").selectedOptions[0]?.text);
}
$("input").onchange = () => { onInput(); soundCheck(); };
$("addMic").onchange = () => { store.addMic = $("addMic").checked; save(); onInput(); soundCheck(); };
$("addMicDevice").onchange = () => { store.addMicId = $("addMicDevice").value; save(); onInput(); soundCheck(); };
$("audioFile").onchange = () => { $("audioName").textContent = $("audioFile").files[0]?.name || "고른 파일 없음"; };
$("pickAudio").onclick = () => $("audioFile").click();
$("checkRetry").onclick = async () => { S.mics = await listMics(); renderInputs(); soundCheck(); };
$("dsp").onchange = () => { store.dsp = $("dsp").checked; save(); soundCheck(); };
// sound check: while the setup screen is open, the chosen device feeds a level bar so a dead cable or the wrong
// input shows before Start. Released before Start, which opens the device itself.
let check = null;
export async function soundCheck() {
  if (isCompleted()) { stopCheck(); return; }
  stopCheck();
  const v = $("input").value;
  // a meeting tab can only be opened on Start; with the operator's mic added, check that mic instead
  const id = v.startsWith("dev:") ? v.slice(4) : v === "screen" && mixing() ? $("addMicDevice").value : null;
  // the meter sits under the input it measures
  if (v === "screen") $("addMicDevice").after($("meter")); else $("hint").before($("meter"));
  $("meter").hidden = id === null || $("setup").hidden;
  if ($("meter").hidden) return;
  const mine = (check = {});
  const raw = v.startsWith("dev:") ? !$("dsp").checked : false;
  let stream;
  $("checkRetry").hidden = true;
  const open = (id) => navigator.mediaDevices.getUserMedia({ audio: { deviceId: id ? { exact: id } : undefined,
    channelCount: 1, echoCancellation: !raw, noiseSuppression: !raw, autoGainControl: !raw } });
  try {
    if (!navigator.mediaDevices) throw Object.assign(new Error("insecure"), { name: "InsecureContext" });
    try {
      stream = await open(id);
    } catch (e) {
      // a remembered device that is unplugged or renamed: fall back to the system default once
      if (!id || !["NotFoundError", "OverconstrainedError"].includes(e.name)) throw e;
      stream = await open("");
    }
  } catch (e) {
    $("checkNote").dataset.state = "error";
    $("checkNote").textContent = micError(e);
    $("checkRetry").hidden = false;
    console.warn("sound check:", e.name, e.message);
    return;
  }
  if (check !== mine) return stream.getTracks().forEach((t) => t.stop()); // the choice changed meanwhile
  const ctx = new AudioContext();
  const an = ctx.createAnalyser();
  an.fftSize = 1024;
  ctx.createMediaStreamSource(stream).connect(an);
  const buf = new Float32Array(an.fftSize);
  let heard = 0, start = performance.now();
  Object.assign(check, { stream, ctx });
  const tick = () => {
    if (check !== mine) return;
    an.getFloatTimeDomainData(buf);
    let peak = 0;
    for (const x of buf) peak = Math.max(peak, Math.abs(x));
    if (peak > 0.02) heard = performance.now();
    $("checkLevel").style.width = `${Math.min(100, peak * 140)}%`;
    $("checkLevel").classList.toggle("hot", peak > 0.97);
    $("checkNote").dataset.state = peak > 0.97 ? "error" : heard && performance.now() - heard < 3000 ? "ok"
      : !heard && performance.now() - start > 4000 ? "warn" : "idle";
    $("checkNote").textContent = peak > 0.97 ? "소리가 너무 커서 찢어질 수 있습니다. 입력 음량을 줄이세요."
      : heard ? (performance.now() - heard < 3000 ? "들어오고 있습니다" : "조용합니다")
      : performance.now() - start > 4000 ? "소리가 들어오지 않습니다. 장치와 연결을 확인하세요." : "말해 보세요";
    check.raf = requestAnimationFrame(tick);
  };
  tick();
}
export function stopCheck() {
  if (!check) return;
  cancelAnimationFrame(check.raf);
  check.stream?.getTracks().forEach((t) => t.stop());
  check.ctx?.close();
  check = null;
  $("checkLevel").style.width = "0";
  $("checkNote").textContent = "";
}
if (store.dsp !== undefined) $("dsp").checked = store.dsp;
