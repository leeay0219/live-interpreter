"""Reader-facing notes exclude audit noise and preserve actionable uncertainty."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import event
from postprocess.editorial import generate, render_notes, validate_notes, validate_outline, windows

FACT = {"kind": "point", "topic": "데이터 연결", "text": "생산 이력과 설비 정보를 ID로 연결합니다.",
        "sources": [1], "owner": None, "due": None, "uncertainty": ""}
NOTES = {"facts": [FACT], "review_notes": [
    {"sources": [0], "issue": "unrelated recognition fragment", "resolution": "omitted from reader text"}]}
OUTLINE = {"title": "생산 데이터 연결", "summary": {"text": FACT["text"], "fact_ids": ["f1"]}, "topics": []}
SEGS = [{"i": i, "start": i * 30, "end": i * 30, "spk": "speaker", "lang": "ko",
         "text": text, "topic": f"Slide {i + 1} of 3: © Copyright"}
        for i, text in enumerate(["안녕하세요.", FACT["text"], "감사합니다."])]


class FakeClaude:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.prompts = []
        self.systems = []

    def ask_json(self, system, prompt, **kwargs):
        self.prompts.append(prompt)
        self.systems.append(system)
        return copy.deepcopy(next(self.replies))


class EditorialTests(unittest.TestCase):
    def test_shared_policy_reaches_extraction_review_and_composition(self):
        policy = (Path(__file__).resolve().parents[1] / "postprocess/writeup-policy.md").read_text(encoding="utf-8")
        claude = FakeClaude([NOTES, NOTES, OUTLINE])
        generate(claude, SEGS, event.load("general"), template="minutes")
        self.assertEqual(len(claude.systems), 3)
        for system in claude.systems:
            self.assertIn(policy, system)

    def test_short_talk_across_slides_is_one_input_window(self):
        self.assertEqual(windows(SEGS), [SEGS])
        chunks = windows(SEGS, limit=100)
        self.assertEqual([s["i"] for group in chunks for s in group], [0, 1, 2])

    def test_topic_document_has_no_review_noise_empty_sections_or_slide_headings(self):
        claude = FakeClaude([NOTES, NOTES, OUTLINE])
        md, audit = generate(claude, SEGS, event.load("general"), template="minutes")
        self.assertIn("# 생산 데이터 연결", md)
        self.assertEqual(md.count(FACT["text"]), 1)
        for absent in ("©", "슬라이드", "결정 사항", "후속 작업", "없음", "unrelated recognition", "인사"):
            self.assertNotIn(absent, md)
        self.assertEqual(audit["review_notes"], NOTES["review_notes"])
        self.assertNotIn("unrelated recognition", claude.prompts[-1])

    def test_empty_introduction_does_not_invent_a_meeting(self):
        claude = FakeClaude([{"facts": [], "review_notes": []}] * 2)
        md, audit = generate(claude, SEGS[:1], event.load("general"), template="minutes")
        self.assertIn("핵심 내용을 찾지 못했습니다", md)
        self.assertEqual(audit["facts"], [])
        self.assertEqual(len(claude.prompts), 2)

    def test_actions_and_material_uncertainty_survive_an_empty_editor_outline(self):
        action = {**FACT, "id": "f1", "kind": "action", "text": "해당 설비의 가동을 중단하기로 했습니다.",
                  "owner": "담당자 확인 필요", "due": None,
                  "uncertainty": "조치 대상 설비 번호를 원문과 확인해야 합니다."}
        md = render_notes({"title": "설비 조치", "summary": None, "topics": []}, [action], lang="ko", duration="02:30")
        self.assertIn("## 후속 작업", md)
        self.assertIn(action["text"], md)
        self.assertIn(action["owner"], md)
        self.assertIn(action["uncertainty"], md)
        self.assertNotIn("## 결정 사항", md)

    def test_unsupported_evidence_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_notes(NOTES, {0})
        with self.assertRaises(ValueError):
            validate_outline(OUTLINE, [{**FACT, "id": "f2"}])

    def test_uncertain_fact_cannot_be_used_as_certain_prose(self):
        with self.assertRaises(ValueError):
            validate_outline(OUTLINE, [{**FACT, "id": "f1", "uncertainty": "설비 확인 필요"}])

    def test_decisions_cannot_be_repeated_as_topic_paragraphs(self):
        with self.assertRaises(ValueError):
            validate_outline(OUTLINE, [{**FACT, "id": "f1", "kind": "decision"}])

    def test_same_fact_cannot_fill_overview_and_details(self):
        outline = copy.deepcopy(OUTLINE)
        outline["topics"] = [{"title": "상세", "paragraphs": [outline["summary"]]}]
        with self.assertRaises(ValueError):
            validate_outline(outline, [{**FACT, "id": "f1"}])

    def test_related_uncertainties_are_rendered_once_and_all_references_required(self):
        facts = [{**FACT, "id": "f1", "uncertainty": "설비 번호 확인"},
                 {**FACT, "id": "f2", "kind": "open_question", "uncertainty": "조치 설비 확인"}]
        outline = {"title": "설비 확인", "summary": None, "topics": [], "confirmations": [
            {"text": "조치할 설비 번호를 확인해야 합니다.", "fact_ids": ["f1", "f2"], "uncertainty_preserved": True}]}
        validate_outline(outline, facts)
        md = render_notes(outline, facts, lang="ko", duration="01:00")
        self.assertEqual(md.count("조치할 설비 번호를 확인해야 합니다."), 1)
        self.assertNotIn("## 남은 논의", md)
        outline["confirmations"][0]["fact_ids"] = ["f1"]
        with self.assertRaises(ValueError):
            validate_outline(outline, facts)

    def test_empty_optional_sections_are_not_rendered(self):
        md = render_notes(OUTLINE, [{**FACT, "id": "f1"}], lang="en", duration="01:00")
        self.assertNotIn("Decisions", md)
        self.assertNotIn("Next steps", md)
        self.assertNotIn("Details to confirm", md)


if __name__ == "__main__":
    unittest.main()
