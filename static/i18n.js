// Interface language. Korean source text is the key; static/locales/<code>.json holds the translations and the
// server reads the same files for its own messages (ui_text.py). Adding a language is adding one file there.
//
// The language is chosen from ?lang=, then this browser's saved choice, then the browser language, then Korean.
// `t(text, vars)` translates one string ({name} placeholders); text already on the page is translated once at load.
// Server messages arrive in Korean too, so passing them through `t` translates them, including ones with numbers:
// a key may contain placeholders and then matches by pattern ("자료 분석 중 {done}/{total}").
const DEFAULT = { code: "ko", name: "한국어", html_lang: "ko" };
const STORAGE = "uiLang";
const COOKIE = "ui_lang";

export let lang = DEFAULT.code;
export let languages = [DEFAULT];
let strings = {};
let patterns = [];
const originals = new WeakMap(); // text node or element → its Korean text/attributes, so a switch re-translates from source

function normalize(code) {
  if (!code) return null;
  const c = String(code).toLowerCase();
  const exact = languages.find((l) => l.code === c);
  if (exact) return exact.code;
  // browser tags: zh-TW, zh-Hant-TW → zh; en-US → en; ko-KR → ko
  const base = c.split("-")[0];
  return languages.find((l) => l.code === base)?.code || null;
}

function detect() {
  const fromUrl = new URLSearchParams(location.search).get("lang");
  let saved = null;
  try { saved = localStorage.getItem(STORAGE); } catch { /* storage may be blocked */ }
  const fromBrowser = [navigator.language, ...(navigator.languages || [])].map(normalize).find(Boolean);
  return normalize(fromUrl) || normalize(saved) || fromBrowser || DEFAULT.code;
}

async function loadStrings(code) {
  if (code === DEFAULT.code) return {};
  const r = await fetch(`/static/locales/${code}.json`, { cache: "no-cache" });
  if (!r.ok) throw new Error(`locale ${code}: ${r.status}`);
  return (await r.json()).strings || {};
}

function compilePatterns() {
  patterns = Object.keys(strings).filter((k) => /\{\w+\}/.test(k)).map((k) => {
    const names = [];
    const source = k.split(/(\{\w+\})/).map((part, i) => {
      if (i % 2) { names.push(part.slice(1, -1)); return "(.+?)"; }
      return part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    }).join("");
    // "{n}쪽" would also match any Korean sentence ending in 쪽: a key with little fixed text only takes numbers and names
    const weak = k.replace(/\{\w+\}/g, "").trim().length < 4;
    return { key: k, names, weak, re: new RegExp(`^${source}$`) };
  });
}

function fill(text, vars) {
  return vars ? text.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m)) : text;
}

/** Translate `text` (Korean source) into the current language; unknown text comes back unchanged. */
export function t(text, vars) {
  if (text == null) return text;
  const s = String(text);
  if (lang === DEFAULT.code) return fill(s, vars);
  const hit = strings[s];
  if (hit !== undefined) return fill(hit, vars);
  const key = s.trim();
  if (key !== s && strings[key] !== undefined) return s.replace(key, fill(strings[key], vars));
  for (const p of patterns) {
    const m = p.re.exec(key);
    if (m && !(p.weak && m.slice(1).some((v) => HANGUL.test(v)))) {
      return fill(strings[p.key], Object.fromEntries(p.names.map((n, i) => [n, t(m[i + 1])])));  // a stage inside a message translates too
    }
  }
  return fill(s, vars);
}

const ATTRS = ["title", "placeholder", "aria-label", "alt"];
const HANGUL = /[\uac00-\ud7a3]/;

/** Translate the text and attributes already in `root` (static markup). Safe to call again after a switch. */
export function translateDom(root = document) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (node.nodeType === Node.TEXT_NODE) {
      if (node.parentNode?.nodeName === "SCRIPT" || node.parentNode?.nodeName === "STYLE") continue;
      let source = originals.get(node);
      if (source === undefined) {
        if (!HANGUL.test(node.nodeValue)) continue;
        source = node.nodeValue;
        originals.set(node, source);
      }
      node.nodeValue = t(source);
    } else {
      let attrs = originals.get(node);
      if (attrs === undefined) {
        attrs = {};
        for (const a of ATTRS) {
          const v = node.getAttribute(a);
          if (v && HANGUL.test(v)) attrs[a] = v;
        }
        if (!Object.keys(attrs).length) continue;
        originals.set(node, attrs);
      }
      for (const [a, v] of Object.entries(attrs)) node.setAttribute(a, t(v));
    }
  }
}

function remember(code) {
  try { localStorage.setItem(STORAGE, code); } catch { /* storage may be blocked */ }
  const secure = location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `${COOKIE}=${code}; Path=/; Max-Age=31536000; SameSite=Lax${secure}`;
  document.documentElement.lang = languages.find((l) => l.code === code)?.html_lang || code;
}

/** Switch the interface language: loads the strings, re-translates the page and tells listeners (`uilang` event). */
export async function setLang(code) {
  code = normalize(code) || DEFAULT.code;
  strings = await loadStrings(code).catch(() => ({}));
  compilePatterns();
  lang = code;
  remember(code);
  translateDom(document);
  document.dispatchEvent(new CustomEvent("uilang", { detail: { lang: code } }));
}

/** Fill a <select> with the available languages; changing it switches the language (and calls `onChange`). */
export function languageSelect(select, onChange) {
  select.replaceChildren(...languages.map((l) => new Option(l.name, l.code)));
  select.value = lang;
  select.onchange = async () => { await setLang(select.value); onChange?.(select.value); };
  document.addEventListener("uilang", () => { select.value = lang; });
}

async function init() {
  try {
    const r = await fetch("/api/ui-languages", { cache: "no-cache" });
    if (r.ok) languages = await r.json();
  } catch { /* offline or no server: Korean only */ }
  if (!languages.some((l) => l.code === DEFAULT.code)) languages.unshift(DEFAULT);
  await setLang(detect());
}

await init();
