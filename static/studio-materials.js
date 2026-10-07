import { CaptionClient } from "/static/captions.js";

// A PDF upload returns 202 with an upload id; rendering continues on the server. Poll until it is done.
export async function waitForUpload(api, started, { onProgress = () => {}, isCurrent = () => true } = {}) {
  if (started.state !== "rendering") return started;
  for (;;) {
    await new Promise(resolve => setTimeout(resolve, 1000));
    if (!isCurrent()) return null;
    const response = await api(`/api/uploads/${started.upload_id}`);
    if (!response.ok) throw new Error(response.status === 404 ? "세션이 바뀌어 업로드를 취소했습니다. 다시 올려 주세요." : await response.text());
    const status = await response.json();
    if (status.state === "done") return status.document;
    if (status.state === "error") throw new Error(status.error);
    onProgress(status.seconds);
  }
}

export class MaterialTools {
  constructor({ $, api, open, prepareAudio, applyContext, onDeckAnalysis, clientId }) {
    Object.assign(this, { $, api, open, prepareAudio, applyContext, onDeckAnalysis, clientId });
    this.references = [];
    $("addReference").onclick = () => $("referenceFile").click();
    $("referenceFile").onchange = () => this.upload($("referenceFile").files[0]);
    $("evidencePage").onchange = () => this.showPage();
    $("evidenceSave").onclick = () => this.change({ [this.field === "people" ? "name" : "term"]: $("evidenceSpelling").value });
    $("evidenceExclude").onclick = () => this.change({ excluded: true });
    $("evidenceRestore").onclick = () => this.change({ restore: true });
    $("rehearsalStart").onclick = () => this.rehearse();
    $("rehearsalStop").onclick = () => this.stopRehearsal();
    this.refresh();
    this.timer = setInterval(() => this.refresh(), 3000);
    addEventListener("pagehide", () => { clearInterval(this.timer); this.stopRehearsal(); });
  }

  async json(url, options = {}) {
    const response = await this.api(url, options);
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }

  async refresh() {
    if (this.refreshing) return;
    this.refreshing = true;
    try {
      this.references = await this.json("/api/references");
      this.$("referenceCount").textContent = this.references.length ? `${this.references.length}개` : "추가";
      this.$("referenceButton").setAttribute("aria-label", `참고 자료 ${this.references.length ? `${this.references.length}개` : "추가"}`);
      this.$("addReference").disabled = this.uploading || this.references.length >= 4;
      const container = this.$("references");
      const signature = JSON.stringify(this.references.map(d => [d.id, d.revision, d.analysis?.stage, d.analysis?.state]));
      if (signature === this.signature) return;
      this.signature = signature;
      container.replaceChildren(...this.references.map(doc => {
        const row = document.createElement("div"), title = document.createElement("b"), status = document.createElement("p");
        row.className = "sub"; title.textContent = doc.name;
        status.textContent = doc.analysis?.stage || "분석 대기 중"; status.className = "hint";
        const actions = document.createElement("div"); actions.className = "row";
        const view = document.createElement("button"); view.className = "btn small"; view.textContent = "분석 보기";
        view.onclick = () => this.showReference(doc);
        const remove = document.createElement("button"); remove.className = "btn small"; remove.textContent = "삭제";
        remove.onclick = async () => {
          try { await this.json(`/api/references/${doc.id}`, { method: "DELETE" }); await this.refresh(); }
          catch (e) { this.$("materialError").textContent = e.message; }
        };
        actions.append(view, remove);
        if (["partial", "error"].includes(doc.analysis?.state)) {
          const retry = document.createElement("button"); retry.className = "btn small"; retry.textContent = "다시 분석";
          retry.onclick = async () => {
            try {
              await this.json(`/api/materials/${doc.id}/analysis`, { method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ deck_id: doc.id }) });
              await this.refresh();
            } catch (e) { this.$("materialError").textContent = e.message; }
          };
          actions.append(retry);
        }
        row.append(title, status, actions); return row;
      }));
    } catch (e) { this.$("materialError").textContent = "참고 자료 상태를 확인하지 못했습니다."; }
    finally { this.refreshing = false; }
  }

  async upload(file) {
    if (!file || this.uploading) return;
    if (!/\.pdf$/i.test(file.name) || file.size > 40 * 1024 * 1024) {
      this.$("materialError").textContent = "40MB 이하의 PDF를 올려 주세요.";
      this.$("referenceFile").value = "";
      return;
    }
    this.uploading = true;
    this.$("materialError").textContent = "자료 읽는 중";
    this.$("addReference").disabled = true;
    try {
      const form = new FormData(); form.append("file", file);
      const started = await this.json("/api/references", { method: "POST", body: form });
      await waitForUpload(this.api, started, { onProgress: seconds => this.$("materialError").textContent = `자료 읽는 중, ${seconds}초` });
      this.$("materialError").textContent = "";
      await this.refresh();
    } catch (e) { this.$("materialError").textContent = e.message; }
    finally { this.uploading = false; this.$("addReference").disabled = this.references.length >= 4; this.$("referenceFile").value = ""; }
  }

  async showReference(doc) {
    try {
      const analysis = await this.json(`/api/materials/${doc.id}/analysis`);
      if (!analysis) throw new Error("자료가 삭제되었습니다.");
      this.open("sheetEvidence");
      this.$("evidenceTitle").textContent = doc.name;
      this.doc = doc; this.analysis = analysis; this.item = null;
      this.$("evidenceEdit").hidden = true;
      this.$("evidenceStatus").textContent = analysis.stage;
      this.pages(doc.pages.map((_, i) => i + 1));
      const list = document.createElement("div");
      list.id = "referenceEntities";
      for (const field of ["people", "terms"]) for (const item of analysis[field] || []) {
        const button = document.createElement("button");
        button.className = "btn small"; button.textContent = item.name || item.term;
        button.onclick = () => this.evidence(doc, analysis, field, item);
        list.append(button);
      }
      for (const item of analysis.excluded || []) {
        const button = document.createElement("button");
        button.className = "btn small"; button.textContent = `${item.label} 다시 참고`;
        button.onclick = async () => {
          try { await this.edit(doc, analysis, { id: item.id, restore: true }); await this.showReference(doc); }
          catch (e) { this.$("evidenceStatus").textContent = e.message; }
        };
        list.append(button);
      }
      document.getElementById("referenceEntities")?.remove();
      this.$("evidenceDescription").after(list);
    } catch (e) { this.$("materialError").textContent = e.message; }
  }

  evidence(doc, analysis, field, item) {
    this.doc = doc; this.analysis = analysis; this.field = field; this.item = item;
    this.open("sheetEvidence");
    document.getElementById("referenceEntities")?.remove();
    this.$("evidenceTitle").textContent = doc.name;
    this.$("evidenceEdit").hidden = false;
    this.$("evidenceSpelling").value = item.name || item.term;
    this.$("evidenceStatus").textContent = item.evidence === "image" ? "이미지에서 읽은 표기입니다. 원본을 확인해 주세요." : "";
    this.pages(item.pages);
  }

  pages(numbers) {
    this.$("evidencePage").replaceChildren(...numbers.map(number => {
      const option = document.createElement("option"); option.value = number; option.textContent = `${number}쪽`; return option;
    }));
    this.showPage();
  }

  showPage() {
    const number = Number(this.$("evidencePage").value);
    this.$("evidenceImage").src = this.doc.pages[number - 1].url;
    const page = this.analysis.page_contexts?.find(p => p.page === number);
    this.$("evidenceDescription").textContent = page
      ? [page.summary, page.visual, page.uncertain ? `확인 필요: ${page.uncertain}` : ""].filter(Boolean).join("\n")
      : this.analysis.failed_pages?.includes(number) ? "이 페이지는 분석하지 못했습니다." : this.analysis.summary || "";
  }

  edit(doc, analysis, change) {
    return this.json(`/api/materials/${doc.id}/edit`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ revision: analysis.revision, ...change }) });
  }

  async change(change) {
    if (!this.item) return;
    try {
      this.analysis = await this.edit(this.doc, this.analysis, { id: this.item.id, ...change });
      this.$("evidenceStatus").textContent = "다음 발화부터 적용됩니다.";
      if (this.doc.kind !== "reference") this.onDeckAnalysis(this.analysis);
      this.signature = null; await this.refresh();
    } catch (e) { this.$("evidenceStatus").textContent = e.message; }
  }

  async rehearse() {
    await this.stopRehearsal();
    this.$("rehearsalStart").disabled = true;
    this.$("rehearsalStatus").textContent = "마이크 준비 중";
    this.$("rehearsalCaptions").innerHTML = '<div class="en"></div><div class="ko"></div>';
    try {
      await this.applyContext();
      const source = this.prepareAudio();
      this.rehearsal = new CaptionClient({ el: this.$("rehearsalCaptions"), rehearsal: true, clientId: this.clientId,
        onStatus: (state) => {
          this.$("rehearsalStatus").textContent = ({ live: "한 문장을 말해 주세요", rehearsal_done: "확인이 끝났습니다",
            error: "통역 연결을 확인해 주세요", stopped: "리허설이 끝났습니다", silence: "입력 소리가 작습니다" })[state] || "";
          if (["rehearsal_done", "stopped"].includes(state)) {
            this.$("rehearsalStart").disabled = false; this.$("rehearsalStop").hidden = true;
          }
        },
      });
      await this.rehearsal.startAudio([source]);
      this.$("rehearsalStop").hidden = false;
    } catch (e) {
      await this.stopRehearsal();
      this.$("rehearsalStatus").textContent = e.message || "음성 입력을 확인하세요.";
      this.$("rehearsalStart").disabled = false;
    }
  }

  async stopRehearsal() {
    if (this.rehearsal) { await this.rehearsal.dispose(); this.rehearsal = null; }
    this.$("rehearsalStart").disabled = false; this.$("rehearsalStop").hidden = true;
  }
}
