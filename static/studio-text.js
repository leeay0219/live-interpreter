// Text helpers for the studio with no studio state: the Markdown preview and microphone error messages.

// A small Markdown reader for the studio preview. Full documents are available as HTML, Word and Markdown.
export function mdToHtml(md) {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const inline = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
  const lines = md.split("\n"), out = [];
  let para = [], list = null;
  const flush = () => {
    if (para.length) out.push(`<p>${para.map(inline).join("<br>")}</p>`);
    if (list) out.push(`</${list}>`);
    para = []; list = null;
  };
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    let m;
    if (!l.trim()) flush();
    else if ((m = l.match(/^(#{1,6})\s+(.*)/))) {
      flush();
      const n = Math.min(4, m[1].length);
      out.push(`<h${n}>${inline(m[2])}</h${n}>`);
    } else if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(l)) { flush(); out.push("<hr>"); }
    else if (l.trim().startsWith("|")) {
      flush();
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(lines[i++].trim());
      i--;
      const cells = (row) => row.replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()));
      const head = rows.length > 1 && /^[|\s:-]+$/.test(rows[1]);
      out.push("<table>" + rows.filter((r, k) => !(head && k === 1)).map((r, k) => {
        const tag = head && k === 0 ? "th" : "td";
        return `<tr>${cells(r).map((c) => `<${tag}>${c}</${tag}>`).join("")}</tr>`;
      }).join("") + "</table>");
    } else if ((m = l.match(/^>\s?(.*)/))) { flush(); out.push(`<blockquote>${inline(m[1])}</blockquote>`); }
    else if ((m = l.match(/^\s*([-*+]|\d+[.)])\s+(.*)/))) {
      const tag = /\d/.test(m[1]) ? "ol" : "ul";
      if (list !== tag) { flush(); out.push(`<${tag}>`); list = tag; }
      out.push(`<li>${inline(m[2])}</li>`);
    } else {
      if (list) flush();
      para.push(l);
    }
  }
  flush();
  return out.join("");
}

// why a microphone did not open, in words the operator can act on
export function micError(e) {
  return {
    NotAllowedError: "마이크 권한이 막혀 있습니다. 주소창 왼쪽 아이콘에서 마이크를 허용하고, macOS라면 시스템 설정의 개인정보 보호 및 보안, 마이크 항목에서 Chrome을 켜세요.",
    SecurityError: "마이크 권한이 막혀 있습니다. 주소창 왼쪽 아이콘에서 마이크를 허용하세요.",
    NotReadableError: "다른 앱(Zoom, Teams 등)이 마이크를 쓰고 있어 열 수 없습니다. 그 앱을 닫거나 다른 장치를 고르세요.",
    NotFoundError: "연결된 마이크가 없습니다. 장치를 연결한 뒤 다시 시도하세요.",
    OverconstrainedError: "고른 장치를 찾을 수 없습니다. 목록에서 다시 고르세요.",
    InsecureContext: "이 주소에서는 브라우저가 마이크를 열지 않습니다. https 주소나 localhost로 여세요.",
  }[e.name] || `마이크를 열지 못했습니다 (${e.name || e}).`;
}
