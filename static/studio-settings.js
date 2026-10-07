// Speakers, session terms, situation skills, and the context sent on Start.
import { $, S, fetchApi, save, store } from "/static/studio-state.js";
import { logSys } from "/static/studio-log.js";

// speakers for this session. Once the list has been edited (store.people set) it replaces the event file's
// speakers on Start; until then the event file's stay. Misheard forms come from /api/people/suggest or by hand.
const MAX_PEOPLE = 8;
const FORMS = [["say_ko", "한국어 발음"], ["heard_as", "잘못 들릴 형태"]];
export function renderPeople() {
  const list = store.people || [];
  $("people").replaceChildren(...list.map((p, i) => personCard(p, i)));
  $("addPerson").hidden = list.length >= MAX_PEOPLE;
  $("peopleNote").textContent = store.people === undefined && S.eventPeople.length
    ? `지금 서버에 등록된 발표자 ${S.eventPeople.map((p) => p.en).join(", ")}. 여기서 추가하면 입력한 사람으로 바뀝니다.`
    : list.length >= MAX_PEOPLE ? `최대 ${MAX_PEOPLE}명까지 넣을 수 있습니다.` : "";
}
function personCard(p, i) {
  const card = document.createElement("div");
  card.className = "person";
  const names = document.createElement("div");
  names.className = "names";
  const field = (key, placeholder) => {
    const inp = document.createElement("input");
    inp.type = "text";
    inp.placeholder = placeholder;
    inp.value = p[key] || "";
    inp.oninput = () => { p[key] = inp.value; save(); };
    return inp;
  };
  const del = document.createElement("button");
  del.className = "btn x";
  del.textContent = "삭제";
  del.title = "이 발표자 빼기";
  del.onclick = () => { store.people.splice(i, 1); save(); renderPeople(); };
  names.append(field("name", "이름 (예: Ahyeong Lee)"), field("ko", "한국어 자막 (예: 아영님)"), field("role", "역할 (예: AWS 솔루션즈 아키텍트)"), del);
  card.append(names);
  for (const [key, label] of FORMS) {
    const row = document.createElement("div");
    row.className = "forms";
    const title = document.createElement("span");
    title.className = "lbl";
    title.textContent = label;
    row.append(title, ...(p[key] || []).map((w, k) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = w;
      const x = document.createElement("button");
      x.textContent = "×";
      x.title = "빼기";
      x.onclick = () => { p[key].splice(k, 1); save(); renderPeople(); };
      chip.append(x);
      return chip;
    }));
    const add = document.createElement("input");
    add.type = "text";
    add.className = "add";
    add.placeholder = "직접 추가";
    add.onkeydown = (e) => {
      if (e.key !== "Enter" || e.isComposing) return;
      e.preventDefault();
      const w = add.value.trim();
      p[key] ||= [];
      if (w && !p[key].includes(w)) p[key].push(w);
      save(); renderPeople();
      $("people").children[i]?.querySelectorAll("input.add")[FORMS.findIndex(([k]) => k === key)]?.focus();
    };
    row.append(add);
    if (key === "heard_as") {
      const sug = document.createElement("button");
      sug.className = "btn small";
      sug.textContent = "잘못 들릴 형태 추천";
      sug.onclick = () => suggest(p, sug);
      row.append(sug);
    }
    card.append(row);
  }
  return card;
}
async function suggest(p, btn) {
  if (!p.name?.trim()) return ($("peopleNote").textContent = "이름을 먼저 입력하세요.");
  btn.disabled = true;
  btn.textContent = "추천 받는 중";
  const r = await fetchApi("/api/people/suggest", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: p.name, ko: p.ko, role: p.role }) }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  if (!r) {
    btn.disabled = false;
    btn.textContent = "잘못 들릴 형태 추천";
    return ($("peopleNote").textContent = "추천을 받지 못했습니다. 직접 추가해 주세요.");
  }
  for (const [key] of FORMS) p[key] = [...new Set([...(p[key] || []), ...(r[key] || [])])].slice(0, 12);
  save(); renderPeople(); // redraws the cards and resets the note, so the note comes after
  $("peopleNote").textContent = (r.say_ko.length || r.heard_as.length) ? "추천에 실제로 들린 형태가 빠질 수 있습니다. 리허설 로그에서 본 형태는 직접 추가하세요." : "추천이 없습니다. 직접 추가해 주세요.";
}
$("addPerson").onclick = () => {
  store.people ||= [];
  if (store.people.length >= MAX_PEOPLE) return;
  store.people.push({ name: "", ko: "", role: "", say_ko: [], heard_as: [] });
  save(); renderPeople();
  $("people").lastChild.querySelector("input").focus();
};
// session terms (misheard → right spelling). Kept in the browser like the speakers, sent on Start, and sent again
// right away when one is added from the log while captioning.
store.terms ||= [];
export function renderTerms() {
  $("terms").replaceChildren(...store.terms.map((t, i) => {
    const row = document.createElement("div");
    row.className = "term";
    row.innerHTML = `<span class="heard"></span><span class="muted">대신</span><span class="right"></span><span class="muted ko"></span><span class="muted grow"></span>`;
    row.querySelector(".heard").textContent = t.heard;
    row.querySelector(".right").textContent = t.en;
    row.querySelector(".ko").textContent = t.ko ? `한국어 자막 ${t.ko}` : "";
    row.querySelector(".grow").textContent = t.always ? "항상 바꿈" : "맥락에 맞을 때 바꿈";
    const del = document.createElement("button");
    del.className = "btn small";
    del.textContent = "삭제";
    del.onclick = () => { store.terms.splice(i, 1); save(); renderTerms(); sendTerms(); };
    row.append(del);
    return row;
  }));
}
export function termForm(heard, onDone) {
  const f = document.createElement("form");
  f.className = "termform";
  f.innerHTML = `<input type="text" name="heard" placeholder="잘못 들린 말" required>
    <input type="text" name="en" placeholder="바른 표기" required>
    <input type="text" name="ko" placeholder="한국어 자막 (선택)">
    <label class="check" title="다른 뜻으로 쓰이지 않는 말일 때만 켜세요"><input type="checkbox" name="always"> 항상 바꾸기</label>
    <span class="btns"><button class="btn">추가</button></span>`;
  f.heard.value = heard || "";
  if (onDone) {
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "btn";
    cancel.textContent = "닫기";
    cancel.onclick = () => onDone(null);
    f.querySelector(".btns").append(cancel);
  }
  f.onsubmit = (e) => {
    e.preventDefault();
    const t = { heard: f.heard.value.trim(), en: f.en.value.trim(), ko: f.ko.value.trim(), always: f.always.checked };
    if (!t.heard || !t.en) return;
    store.terms = [...store.terms.filter((x) => x.heard.toLowerCase() !== t.heard.toLowerCase()), t];
    save(); renderTerms(); sendTerms();
    if (onDone) onDone(t);
    else { f.reset(); f.heard.focus(); }
  };
  return f;
}
async function sendTerms() {
  const r = await fetchApi("/api/terms", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ terms: store.terms }) }).catch(() => null);
  if (!r?.ok) logSys("세션 용어를 서버에 보내지 못했습니다");
}
renderTerms();
$("termAdd").append(termForm());
// advanced: situation skills and whether speech content goes into the server log
fetch("/api/skills").then((r) => r.json()).then((d) => {
  S.skillDefs = d.skills;
  store.skills = store.skills.filter((n) => S.skillDefs.some((s) => s.name === n));
  renderSkills();
});
function renderSkills() {
  $("skills").replaceChildren(...S.skillDefs.map((sk) => {
    const b = document.createElement("button");
    b.className = `skill${store.skills.includes(sk.name) ? " on" : ""}`;
    b.textContent = sk.title;
    b.title = sk.description;
    b.onclick = () => {
      store.skills = store.skills.includes(sk.name) ? store.skills.filter((n) => n !== sk.name) : [...store.skills, sk.name];
      save(); renderSkills();
    };
    return b;
  }));
}
// content logging is off unless chosen for this browser; it is not remembered across reloads on purpose
export async function applyContext() {
  const r = await fetchApi("/api/context", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ skills: store.skills, log_content: $("logContent").checked, terms: store.terms, deck_id: S.deck?.id || null, session_id: S.sessionState.session_id,
      ...(store.people && { people: store.people.filter((p) => p.name?.trim()) }) }) });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
