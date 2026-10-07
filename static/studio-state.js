// Shared studio state and helpers: element lookup, preferences, the operator id and API calls. Values other modules reassign live on S.

export const $ = (id) => document.getElementById(id);
const ICONS = {
  prev: '<path d="M15 6l-6 6 6 6"/>',
  next: '<path d="M9 6l6 6-6 6"/>',
  overlay: '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M3 15h18"/><path d="M7 17.5h7"/>',
  cap: '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="M10.5 10.3a2.2 2.2 0 1 0 0 3.4M17 10.3a2.2 2.2 0 1 0 0 3.4"/>',
  src: '<path d="M5 7h14"/><path d="M5 12h14" opacity=".45"/><path d="M5 17h9" opacity=".45"/>',
  full: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  share: '<circle cx="6" cy="12" r="2.3"/><circle cx="18" cy="6" r="2.3"/><circle cx="18" cy="18" r="2.3"/><path d="M8.1 11l7.8-4M8.1 13l7.8 4"/>',
  log: '<path d="M9 6h11M9 12h11M9 18h11"/><path d="M4.5 6h.01M4.5 12h.01M4.5 18h.01" stroke-width="2.6"/>',
};
document.querySelectorAll("[data-icon]").forEach((b) => (b.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[b.dataset.icon]}</svg>`));
export let store;
try { store = JSON.parse(localStorage.getItem("studio2") || "{}"); } catch { store = {}; }
// Device/display preferences persist; people, terms and conversation belong to the server session.
delete store.people; delete store.terms;
export const save = () => { const { people, terms, ...prefs } = store; localStorage.setItem("studio2", JSON.stringify(prefs)); };
export const clientId = crypto.randomUUID();
export const fetchApi = async (url, options = {}) => {
  try {
    const response = await fetch(url, { ...options, headers: { ...options.headers, "X-Operator-Id": clientId } });
    $("serverNotice").hidden = true;
    return response;
  } catch (error) {
    if (error.name !== "TypeError") throw error;
    $("serverNotice").textContent = "서버에 연결하지 못했습니다. 서버 실행 상태를 확인해 주세요.";
    $("serverNotice").hidden = false;
    throw new Error("서버 연결이 끊겨 요청을 마치지 못했습니다. 연결 후 다시 시도해 주세요.");
  }
};
export const isCompleted = () => S.finishing || S.sessionState.phase === "ended";
store.capPos ||= "below"; store.size ||= 1; store.src ??= true; store.skills ||= []; store.theme ||= "dark";

// values that more than one module reassigns
export const S = {
  sessionState: {},
  focus: "presentation",
  finishing: false,
  deck: null,
  page: 0,
  mics: [],
  skillDefs: [],
  eventPeople: [],
  analysisTimer: undefined,
  uploadVersion: 0,
  uploading: false,
};
