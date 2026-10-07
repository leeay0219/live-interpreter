"""Evidence-backed notes, composed by subject rather than processing window."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re

from postprocess.html_document import clean_prose

KINDS = {"point", "decision", "action", "open_question"}
FACT_SCHEMA = """{
  "facts": [{
    "kind": "point|decision|action|open_question",
    "topic": "short subject",
    "text": "one substantive fact in the requested language",
    "sources": [transcript line indices],
    "owner": null,
    "due": null,
    "uncertainty": ""
  }],
  "review_notes": [{
    "sources": [transcript line indices],
    "issue": "recognition ambiguity or correction",
    "resolution": "how it was handled"
  }]
}"""
# Shared with the repository's session-writeup agent skill.
RULES = "\n" + Path(__file__).with_name("writeup-policy.md").read_text(encoding="utf-8")


def windows(segments, limit=14000):
    """Bound model inputs without giving slide boundaries editorial significance."""
    result, group, size = [], [], 0
    for segment in segments:
        cost = len(segment["text"]) + len(segment.get("shown", "")) + 80
        if group and (size + cost > limit or segment["start"] - group[0]["start"] >= 900):
            result.append(group)
            group, size = [], 0
        group.append(segment)
        size += cost
    if group:
        result.append(group)
    return result


def source_text(segments):
    return "\n".join(
        f"[{s['i']}] {s['start']:.0f}s {s.get('spk', '')}: {s['text']}"
        + (f"\n  Live subtitle: {s['shown']}" if s.get("shown") else "")
        for s in segments
    )


def validate_notes(value, indices):
    """Reject unsupported references rather than silently dropping substantive claims."""
    if not isinstance(value, dict) or not isinstance(value.get("facts"), list):
        raise ValueError("정리 결과의 사실 목록을 읽을 수 없습니다.")
    for fact in value["facts"]:
        if fact.get("kind") not in KINDS or not isinstance(fact.get("text"), str) or not fact["text"].strip():
            raise ValueError("정리 결과의 사실 형식이 올바르지 않습니다.")
        sources = fact.get("sources")
        if not isinstance(sources, list) or not sources or any(type(i) is not int or i not in indices for i in sources):
            raise ValueError("정리 내용에 연결된 원문 근거를 확인할 수 없습니다.")
        for key in ("topic", "owner", "due", "uncertainty"):
            if fact.get(key) is not None and not isinstance(fact[key], str):
                raise ValueError("정리 결과의 항목 형식이 올바르지 않습니다.")
    notes = value.get("review_notes", [])
    if not isinstance(notes, list):
        raise ValueError("검토 내역 형식이 올바르지 않습니다.")
    for note in notes:
        if not isinstance(note, dict) or not isinstance(note.get("issue"), str):
            raise ValueError("검토 내역 형식이 올바르지 않습니다.")
        if not isinstance(note.get("sources"), list) or any(type(i) is not int or i not in indices for i in note["sources"]):
            raise ValueError("검토 내역의 원문 근거를 확인할 수 없습니다.")
    return {"facts": value["facts"], "review_notes": notes}


def extract_window(claude, segments, system, background):
    indices = {s["i"] for s in segments}
    evidence = source_text(segments)
    references = list(dict.fromkeys(s["topic"] for s in segments if s.get("topic")))
    if references:
        background += "\nSlide reference text (background only, not evidence of speech):\n" + "\n".join(references)[:8000]
    prompt = f"""Extract substantive notes from the transcript, with exact source indices.
Do not draft a document or use processing-window headings.
{background}
Transcript:
{evidence}

Return this JSON structure. owner/due apply only to actual actions. Empty lists are allowed.
{FACT_SCHEMA}"""
    draft = validate_notes(claude.ask_json(system, prompt, max_tokens=10000), indices)
    prompt = f"""Check these extracted notes against the original transcript.
Correct unsupported details and recover omitted substantive facts. Check proposals versus
decisions, real work versus examples, and uncertainty affecting actual actions.
Preserve existing review_notes and add useful corrections there, never to reader-facing facts.
{background}
Original transcript:
{evidence}
Notes:
{json.dumps(draft, ensure_ascii=False)}
Return the same JSON structure:
{FACT_SCHEMA}"""
    return validate_notes(claude.ask_json(system, prompt, max_tokens=10000), indices)


def validate_outline(outline, facts):
    if not isinstance(outline, dict) or not isinstance(outline.get("title"), str):
        raise ValueError("문서 제목을 읽을 수 없습니다.")
    title = outline["title"].strip()
    if not title or "©" in title or re.match(r"^(?:Slide|슬라이드)\s*\d", title, re.I):
        raise ValueError("문서의 주제 제목을 확인할 수 없습니다.")
    by_id = {f["id"]: f for f in facts}
    blocks = []
    summary = outline.get("summary")
    if summary:
        blocks.append(summary)
    topics = outline.get("topics", [])
    if not isinstance(topics, list):
        raise ValueError("문서의 주제 목록을 읽을 수 없습니다.")
    for topic in topics:
        if not isinstance(topic.get("title"), str) or not topic["title"].strip() or "©" in topic["title"]:
            raise ValueError("문서의 주제 제목을 확인할 수 없습니다.")
        if not isinstance(topic.get("paragraphs"), list):
            raise ValueError("문서의 문단을 읽을 수 없습니다.")
        blocks.extend(topic["paragraphs"])
    narrative_ids = set()
    for block in blocks:
        refs = block.get("fact_ids", []) if isinstance(block, dict) else []
        if any(not isinstance(i, str) or i not in by_id or by_id[i]["kind"] != "point"
               or by_id[i].get("uncertainty") for i in refs):
            raise ValueError("주제 설명에는 결정, 작업, 확인 항목을 반복하지 마세요.")
        if narrative_ids.intersection(refs):
            raise ValueError("같은 사실을 요약과 주제 설명에서 반복하지 마세요.")
        narrative_ids.update(refs)
    confirmations = outline.get("confirmations", [])
    if not isinstance(confirmations, list):
        raise ValueError("확인할 내용의 형식이 올바르지 않습니다.")
    blocks.extend(confirmations)
    for block in blocks:
        if not isinstance(block, dict) or not isinstance(block.get("text"), str) or not block["text"].strip():
            raise ValueError("문서의 문단을 읽을 수 없습니다.")
        refs = block.get("fact_ids")
        if not isinstance(refs, list) or not refs or any(not isinstance(i, str) or i not in by_id for i in refs):
            raise ValueError("문서의 사실 근거를 확인할 수 없습니다.")
        if any(by_id[i].get("uncertainty") for i in refs) and not block.get("uncertainty_preserved"):
            raise ValueError("문서 편집 과정에서 불확실한 내용의 표시가 빠졌습니다.")
    required = {f["id"] for f in facts if f["kind"] == "open_question" or f.get("uncertainty")}
    covered = {i for block in confirmations for i in block["fact_ids"]}
    if not required <= covered:
        raise ValueError("확인해야 할 내용이 문서 편집 과정에서 빠졌습니다.")
    return outline


def compose(claude, facts, system, lang, template):
    if not facts:
        return {"title": "회의 기록" if lang == "ko" else "Session notes", "summary": None, "topics": []}
    narrative = [f for f in facts if f["kind"] == "point" and not f.get("uncertainty")]
    pending = [{"id": f["id"], "text": f.get("uncertainty") or f["text"]}
               for f in facts if f["kind"] == "open_question" or f.get("uncertainty")]
    protected = [f["text"] for f in facts if f["kind"] in ("decision", "action")]
    prompt = f"""Compose one concise {'meeting note' if template == 'minutes' else 'presentation summary'}
from the verified facts below. Use a specific subject title in the requested language.
The reader needs the main message and meaningful explanations, not how the session or review progressed.
Use a short overview followed by subject headings only where they add detail.
Merge repetition across facts. Never organize by slide or processing window.
Assign each narrative fact to the overview OR a topic, never both. Each fact_id may appear
only once across summary and topic paragraphs. A short session needs only a short document;
with three or fewer narrative facts, prefer summary alone and no topics unless the explanation is substantial.
Do not add Decisions, Actions, Verification notes or provenance sections:
those are rendered separately from verified facts only when they exist.
Decision/action subjects below are for choosing the title only. They are rendered separately.
Do not repeat their content in summary or topics. No empty-section announcements or missing-content explanations.
Use confirmations to merge meeting open questions and substantive uncertainty by subject.
Cover EVERY unresolved detail there, using its fact_id.
Several facts about the same uncertain identifier must become ONE confirmation with all their IDs.
Do not repeat these confirmation details in the overview or topic paragraphs.
Confirmations describe ONLY the unresolved detail; do not restate agreed actions or decisions.
Each paragraph must cite supporting fact_ids. Preserve meaningful uncertainty in the paragraph
and mark uncertainty_preserved=true when citing an uncertain fact.
Narrative facts (the ONLY source for summary/topics):
{json.dumps(narrative, ensure_ascii=False)}
Decision/action subjects (title context only, never repeat them in paragraphs):
{json.dumps(protected, ensure_ascii=False)}
Unresolved details (confirmations only):
{json.dumps(pending, ensure_ascii=False)}
Return JSON:
{{"title": "subject", "summary": {{"text": "brief overview", "fact_ids": ["f1"], "uncertainty_preserved": false}},
  "topics": [{{"title": "subject", "paragraphs": [{{"text": "useful detail", "fact_ids": ["f2"], "uncertainty_preserved": false}}]}}],
  "confirmations": [{{"text": "one unresolved subject", "fact_ids": ["f3"], "uncertainty_preserved": true}}]}}
summary may be null; topics and confirmations may be empty. No Markdown headings inside text fields."""
    for attempt in range(2):
        outline = claude.ask_json(system, prompt, max_tokens=12000)
        try:
            return validate_outline(outline, facts)
        except ValueError as error:
            if attempt:
                raise
            prompt += f"\nRebuild the document and fix this validation error: {error}"


def render_notes(outline, facts, *, lang, duration):
    ko = lang == "ko"
    lines = ["# " + outline["title"], ""]
    if outline.get("summary"):
        lines += [outline["summary"]["text"], ""]
    for topic in outline.get("topics", []):
        if topic["paragraphs"]:
            lines += ["## " + topic["title"], ""]
            for paragraph in topic["paragraphs"]:
                lines += [paragraph["text"], ""]
    # Decisions and work are never discarded by the document editor.
    for kind, heading in (("decision", "결정 사항" if ko else "Decisions"),
                          ("action", "후속 작업" if ko else "Next steps")):
        items, seen = [], set()
        for f in facts:
            key = (f["text"], f.get("owner"), f.get("due"), f.get("uncertainty"))
            if f["kind"] == kind and key not in seen:
                seen.add(key)
                items.append(f)
        if not items:
            continue
        lines += ["## " + heading, ""]
        if kind == "action":
            lines += ["| 할 일 | 담당 | 기한 |" if ko else "| Action | Owner | Due |", "|---|---|---|"]
            for f in items:
                cells = [f["text"], f.get("owner") or ("미정" if ko else "TBD"), f.get("due") or ("미정" if ko else "TBD")]
                lines.append("| " + " | ".join(c.replace("|", "\\|").replace("\n", " ") for c in cells) + " |")
        else:
            lines += ["- " + f["text"] for f in items]
        lines.append("")
    confirmations = [block["text"] for block in outline.get("confirmations", [])]
    if not confirmations:
        # Defensive fallback for direct renderer callers; generate() validates coverage.
        confirmations = list(dict.fromkeys(
            f.get("uncertainty") or f["text"] for f in facts
            if f.get("uncertainty") or f["kind"] == "open_question"))
    if confirmations:
        lines += ["## " + ("확인할 내용" if ko else "Details to confirm"), ""]
        lines += ["- " + text for text in confirmations]
        lines.append("")
    if not facts:
        lines += ["기록에서 정리할 핵심 내용을 찾지 못했습니다." if ko else "No substantive content was found in the record.", ""]
    lines += [f"*{'기록 길이' if ko else 'Recorded duration'}: {duration}.*", ""]
    return clean_prose("\n".join(lines))


def generate(claude, segments, ev, *, lang="ko", template="summary", glossary="", progress=lambda _: None):
    """Extract, verify and compose; retain audit data outside the reading document."""
    system = f"""You prepare {'Korean, natural 합니다체' if lang == 'ko' else 'English'} notes for people
who need to understand the session without reading its transcript.
{RULES}
Terminology:
{glossary}
{ev.glossary()}"""
    background = "Session background (not proof of speech):\n" + ev.title + "\n" + ev.context
    if ev.agenda:
        background += "\nPlanned agenda: " + json.dumps(ev.agenda, ensure_ascii=False)
    groups = windows(segments)
    progress("주요 내용과 원문 근거를 확인하는 중")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda group: extract_window(claude, group, system, background), groups))
    facts, notes = [], []
    for result in results:
        for fact in result["facts"]:
            facts.append({**fact, "id": f"f{len(facts) + 1}"})
        notes.extend(result["review_notes"])
    progress("주제별로 문서를 정리하는 중")
    outline = compose(claude, facts, system, lang, template)
    end = round(segments[-1]["end"]) if segments else 0
    duration = f"{end // 60:02d}:{end % 60:02d}"
    return render_notes(outline, facts, lang=lang, duration=duration), {
        "schema_version": 1, "facts": facts, "review_notes": notes, "outline": outline,
    }
