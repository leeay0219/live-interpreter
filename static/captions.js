// Shared caption client: WebSocket to server.py, caption rendering, optional mic capture.
export class CaptionClient {
  constructor({ el, onStatus = () => {}, onLatency = () => {}, onLevel = () => {}, onMessage = () => {}, replay, clientId = "", rehearsal = false }) {
    this.el = el;
    this.replay = replay; // recent lines the server sends on connect (default 3)
    this.onStatus = onStatus;
    this.onLatency = onLatency;
    this.onLevel = onLevel;
    this.onMessage = onMessage; // every server message, for logs
    this.clientId = clientId;
    this.rehearsal = rehearsal;
    this.closed = false;
    this.paused = false;
    this.finals = [];        // [{id, tx, lang}] translated lines
    this.provisional = null; // {rid, tx, lang}
    this.liveSrc = "";       // what is being said right now, in the spoken language
    this.pending = [];       // translated lines waiting for their turn on screen
    this.seen = new Set();
    this.shownUntil = 0;
    this.connect();
  }

  connect() {
    const params = new URLSearchParams({ client: this.clientId, replay: String(this.replay || 3) });
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws${this.rehearsal ? "/rehearsal" : ""}?${params}`);
    ws.binaryType = "arraybuffer";
    ws.onopen = () => {
      this.onStatus(this.mic ? "connecting" : "connected");
      if (this.mic && !this.paused && !this.rehearsal) this.send({ type: "start", intent: "resume", session_id: this.sessionId });
    };
    ws.onclose = () => {
      if (this.closed) return;
      this.onStatus(this.rehearsal ? "stopped" : "offline");
      if (!this.rehearsal) this._reconnect = setTimeout(() => this.connect(), 1500);
    };
    ws.onmessage = (e) => this.handle(JSON.parse(e.data));
    this.ws = ws;
  }

  send(obj) {
    if (this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(obj));
  }

  setPage(deckId, page, focus = "presentation") {
    this.send({ type: "context", session_id: this.sessionId, deck_id: deckId, page, focus });
  }

  handle(m) {
    this.onMessage(m);
    switch (m.type) {
      case "session":
        this.sid = m.sid; // the stream started for this tab
        if (m.session_id) this.sessionId = m.session_id;
        this._started?.();
        return;
      case "lifecycle":
        this.sessionId = m.session_id;
        if (m.phase === "paused") this.onStatus("paused");
        if (m.phase === "ended") this.onStatus("stopped");
        return;
      case "control_error":
        this._startFailed?.(new Error(m.detail));
        this.onStatus("control_error", m.detail);
        return;
      case "diagnostic":
        if (m.sid === this.sid) this.onStatus(m.state);
        return;
      case "rehearsal_done":
        this.onStatus("rehearsal_done");
        this.dispose();
        return;
      case "clear":
        this.seen.clear(); // a new session started (on any operator tab): drop the previous one's lines
        this.clear();
        return;
      case "status": {
        // tabs without a mic just display whatever is happening; a tab sending audio only follows its own stream
        const mine = !m.sid || !this.mic || m.sid === this.sid;
        if (m.state === "replaced" && mine && this.mic) {
          // another tab started captioning: stop for good, don't fight it
          this.stopMic({ quiet: true });
          this.onStatus("replaced", "다른 창에서 통역을 시작해서 이 창은 멈췄습니다");
          return;
        }
        if (!mine) return; // another tab's stream
        this.onStatus(m.state, m.detail);
        if (m.state === "overloaded" && mine && this.mic) this.pause();
        // our own Transcribe stream died while the mic is still on (timeout, network blip): restart it
        if (this.mic && m.state === "error") {
          clearTimeout(this._restart);
          this._restart = setTimeout(() => this.mic && !this.paused && this.send({ type: "start", intent: "resume", session_id: this.sessionId }), 1500);
        }
        return;
      }
      case "partial":
      case "final":
        // only the original line changes here; the translation lines are left alone
        this.liveSrc = m.text;
        this.liveLang = m.lang;
        this.renderSource();
        return;
      case "partial_tx":
        this.provisional = { rid: m.id, tx: m.tx, lang: m.lang };
        this.renderProvisional();
        return;
      case "caption":
        if (this.seen.has(m.id)) return;
        this.seen.add(m.id);
        this.pending.push({ id: m.id, tx: m.tx, lang: m.tx_lang });
        // the next row of the sentence on screen right now: don't make it wait out that row's full reading time
        if (this.lastShown && this.lastShown.id.split("#")[0] === m.id.split("#")[0])
          this.shownUntil = Math.min(this.shownUntil, this.lastShown.at + 1000);
        if (m.ms) this.onLatency(m.ms, m.engine);
        this.pump();
        return;
    }
  }

  // Lines come out at reading pace: each new line stays at the bottom for at least holdFor() before the next one
  // pushes it up. It then remains readable above for two more lines, so nothing leaves the screen quickly.
  // With nothing waiting a line gets its full reading time; once lines queue up (continuous speech) they move on much
  // sooner, since each line stays readable in the roll-up for the next two lines anyway and falling behind the speaker
  // by several seconds is worse.
  holdFor(line) {
    const perSec = line.lang === "ko" ? 9 : 16;
    const backlog = this.pending.length;
    const min = [2.2, 1.4, 0.9][Math.min(backlog, 2)];
    const max = [4.5, 2.2, 1.2][Math.min(backlog, 2)];
    return Math.max(min, Math.min(max, line.tx.length / perSec)) * 1000;
  }

  pump() {
    clearTimeout(this._pump);
    if (!this.pending.length) return;
    const wait = this.shownUntil - performance.now();
    if (wait > 0) {
      this._pump = setTimeout(() => this.pump(), wait);
      return;
    }
    const line = this.pending.shift();
    if (this.provisional && line.id.startsWith(this.provisional.rid)) this.provisional = null;
    this.finals = [...this.finals, line].slice(-6);
    this.lastShown = { id: line.id, at: performance.now() };
    // the next part of the same sentence (a long translation split into rows) follows quickly: it reads as one
    const sameSentence = this.pending[0] && this.pending[0].id.split("#")[0] === line.id.split("#")[0];
    this.shownUntil = performance.now() + (sameSentence ? Math.min(1000, this.holdFor(line)) : this.holdFor(line));
    this.appendLine(line);
    if (this.pending.length) this._pump = setTimeout(() => this.pump(), this.shownUntil - performance.now());
  }

  clear() {
    this.finals = [];
    this.pending = [];
    this.provisional = null;
    this.liveSrc = "";
    this.render();
  }

  // full redraw without animation (load, preference changes)
  render() {
    this.renderSource();
    const roll = this.roll();
    roll.replaceChildren(...this.finals.map((f) => lineEl(f)));
    this.age();
    this.renderProvisional();
  }

  roll() {
    const box = this.el.querySelector(".ko");
    let roll = box.querySelector(".roll");
    if (!roll) {
      roll = document.createElement("div");
      roll.className = "roll";
      box.replaceChildren(roll);
    }
    return roll;
  }

  appendLine(f) {
    const roll = this.roll();
    const el = lineEl(f);
    el.classList.add("enter");
    roll.insertBefore(el, roll.querySelector(".prov"));
    while (roll.querySelectorAll(".line:not(.prov)").length > 6) roll.firstChild.remove();
    this.age();
    // older lines glide up by the height of the new one instead of jumping
    const h = el.getBoundingClientRect().height / (this.scale() || 1);
    roll.animate([{ transform: `translateY(${h}px)` }, { transform: "none" }], { duration: 380, easing: "cubic-bezier(.2,.7,.2,1)" });
  }

  // newest line white, the one above it light grey, older ones dimmer
  age() {
    const lines = [...this.roll().querySelectorAll(".line:not(.prov)")];
    lines.forEach((el, i) => {
      const n = lines.length - 1 - i;
      el.classList.toggle("now", n === 0);
      el.classList.toggle("prev", n === 1);
      el.classList.toggle("old", n >= 2);
    });
  }

  renderProvisional() {
    const roll = this.roll();
    let el = roll.querySelector(".prov");
    const p = this.showProvisional !== false && this.provisional;
    if (!p) return el?.remove();
    if (!el) {
      el = document.createElement("div");
      el.className = "line prov";
      roll.append(el);
    }
    el.lang = p.lang;
    el.textContent = p.tx;
  }

  scale() {
    // the deck stage is CSS-scaled; getBoundingClientRect reports scaled pixels
    const r = this.el.getBoundingClientRect();
    return r.width / this.el.offsetWidth;
  }

  renderSource() {
    // keep the newest words visible; the line is centered, so trim from the front
    let src = this.liveSrc;
    if (src.length > 110) {
      const tail = src.slice(-110);
      src = "… " + tail.slice(tail.indexOf(" ") + 1);
    }
    const srcEl = this.el.querySelector(".en");
    srcEl.textContent = src;
    srcEl.lang = this.liveLang || "en";
  }

  async startMic(deviceId, { raw = true } = {}) {
    return this.startAudio([{ kind: "device", deviceId, raw }]);
  }

  // Capture one or more sources, mixed into one mono 16 kHz stream:
  //   {kind: "device", deviceId, raw}  microphone or USB line-in (raw: no browser noise processing)
  //   {kind: "screen"}                 audio of a shared tab, window or screen (Zoom/Teams/Chime, web players)
  //   {kind: "file", file, monitor}    a recording played at real time, for tests; monitor also plays it aloud
  async startAudio(sources, { onEnded = () => {}, intent = "new" } = {}) {
    this.sources = sources;
    this.audioEnded = onEnded;
    await this.stopMic({ quiet: true });
    if (this.ws.readyState !== WebSocket.OPEN) {
      await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => reject(new Error("서버 연결을 확인해 주세요.")), 5000);
        this.ws.addEventListener("open", () => { clearTimeout(timeout); resolve(); }, { once: true });
        this.ws.addEventListener("error", () => { clearTimeout(timeout); reject(new Error("서버에 연결하지 못했습니다.")); }, { once: true });
      });
    }
    const ctx = new AudioContext({ sampleRate: 16000 });
    await ctx.audioWorklet.addModule("/static/pcm-worklet.js");
    const node = new AudioWorkletNode(ctx, "pcm-worklet", { channelCount: 1, channelCountMode: "explicit", channelInterpretation: "speakers" });
    const streams = [], players = [];
    try {
      for (const s of sources) {
        if (s.kind === "device") {
          const raw = s.raw !== false;
          const stream = await navigator.mediaDevices.getUserMedia({
            audio: {
              deviceId: s.deviceId ? { exact: s.deviceId } : undefined,
              channelCount: 1,
              // line-in from the sound desk is already clean; browser DSP only hurts recognition
              echoCancellation: !raw,
              noiseSuppression: !raw,
              autoGainControl: !raw,
            },
          });
          streams.push(stream);
          stream.getAudioTracks().forEach((track) => track.addEventListener("ended", () => {
            if (this.mic) { this.pause(); this.onStatus("device_lost"); }
          }));
          ctx.createMediaStreamSource(stream).connect(node);
        } else if (s.kind === "screen") {
          // browsers only share audio together with a video surface; the video track is ignored
          const stream = await navigator.mediaDevices.getDisplayMedia({
            video: true,
            audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
            systemAudio: "include",
          });
          streams.push(stream);
          const audio = stream.getAudioTracks();
          if (!audio.length) throw new Error("공유한 화면에 오디오가 없습니다. 공유 창에서 '탭 오디오 공유' 또는 '시스템 오디오 공유'를 켜 주세요.");
          audio.forEach((track) => track.addEventListener("ended", () => {
            if (this.mic) { this.pause(); this.onStatus("device_lost"); }
          }));
          ctx.createMediaStreamSource(new MediaStream(audio)).connect(node);
        } else if (s.kind === "file") {
          const buf = await ctx.decodeAudioData(await s.file.arrayBuffer());
          const src = ctx.createBufferSource();
          src.buffer = buf;
          src.connect(node);
          if (s.monitor) src.connect(ctx.destination);
          src.onended = () => onEnded();
          players.push(src);
        }
      }
    } catch (e) {
      streams.forEach((st) => st.getTracks().forEach((t) => t.stop()));
      await ctx.close();
      throw e;
    }
    node.port.onmessage = (e) => {
      this.onLevel(this.paused ? 0 : e.data.peak);
      if (this.ws.readyState === WebSocket.OPEN && !this.paused) {
        if (this.ws.bufferedAmount > 320000) { this.pause(); this.onStatus("overloaded"); }
        else this.ws.send(e.data.pcm);
      }
    };
    this.mic = { streams, ctx, node, players };
    this.paused = false;
    if (!this.rehearsal) {
      try {
        await new Promise((resolve, reject) => {
          const timeout = setTimeout(() => reject(new Error("통역 시작 응답이 없습니다.")), 15000);
          this._started = () => { clearTimeout(timeout); resolve(); };
          this._startFailed = (e) => { clearTimeout(timeout); reject(e); };
          this.send({ type: "start", intent, session_id: this.sessionId, take: true });
        });
      } catch (e) {
        await this.stopMic({ quiet: true });
        throw e;
      } finally { this._started = this._startFailed = null; }
    }
    players.forEach((p) => p.start());
  }

  async pause() {
    if (!this.mic || this.paused) return;
    this.paused = true;
    await this.mic.ctx.suspend();
    this.send({ type: "pause" });
    this.onLevel(0);
    this.onStatus("pausing");
  }

  async resume() {
    if (!this.mic || this.mic.streams.some(s => s.getAudioTracks().some(t => t.readyState === "ended"))) {
      if (!this.sources) throw new Error("음성 입력을 다시 선택하세요.");
      try { await this.startAudio(this.sources, { onEnded: this.audioEnded, intent: "resume" }); }
      catch (e) { this.paused = true; throw new Error("음성 장치를 다시 연결하거나 공유 권한을 확인한 뒤 이어서 하세요."); }
      return;
    }
    if (this.ws.readyState !== WebSocket.OPEN) throw new Error("연결 복구를 기다려 주세요.");
    this.send({ type: "start", intent: "resume", session_id: this.sessionId });
    this.paused = false;
    await this.mic.ctx.resume();
    this.onStatus("connecting");
  }

  async dispose() {
    this.closed = true;
    clearTimeout(this._reconnect);
    await this.stopMic({ quiet: true });
    this.ws.close();
  }

  async stopMic({ quiet = false } = {}) {
    const mic = this.mic;
    if (!mic) return;
    this.mic = null; // first, so nothing running meanwhile sees a live mic and sends "stop" or "start" again
    this.paused = false;
    clearTimeout(this._restart);
    if (!quiet) this.send({ type: "stop" }); // quiet: the server already moved on to another tab's stream
    mic.players.forEach((p) => { p.onended = null; try { p.stop(); } catch {} });
    mic.streams.forEach((st) => st.getTracks().forEach((t) => t.stop()));
    await mic.ctx.close();
    this.onLevel(0);
  }
}

export async function listMics() {
  // labels are only exposed after permission has been granted once
  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true });
    s.getTracks().forEach((t) => t.stop());
  } catch (e) {
    return [];
  }
  return (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "audioinput");
}

function lineEl(f) {
  const el = document.createElement("div");
  el.className = "line";
  el.lang = f.lang;
  el.textContent = f.tx;
  return el;
}
