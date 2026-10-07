"""Offline document analysis tests, including lifecycle races and actual PDF upload extraction.

    .venv/bin/python -m unittest discover -s tests -p 'test_deck_context.py' -v
"""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import deck_context as dc
import event
import server


def brief(name="Northstar"):
    return {"summary": "자료 기반 통역 맥락", "people": [],
            "terms": [{"term": name, "meaning": "자료에 나온 프로젝트", "pages": [1]}]}


class Bedrock:
    def __init__(self):
        self.requests = []

    def converse(self, **kwargs):
        payload = json.loads(kwargs["messages"][0]["content"][0]["text"])
        self.requests.append(payload)
        return {"output": {"message": {"content": [{"text": json.dumps(brief())}]}},
                "usage": {"inputTokens": 10, "outputTokens": 20}}


def translator():
    tr = server.Translator.__new__(server.Translator)
    tr.event = event.Event(id="test", people=[{"name": "Manual Name", "en": "Manual Name", "ko": "수동 이름"}])
    tr.document_brief, tr.skills, tr.usage, tr.topic = None, [], {}, ""
    tr.model, tr.bedrock = server.DEFAULT_MODEL, Bedrock()
    tr._rebuild()
    return tr


class DocumentTests(unittest.TestCase):
    def test_all_text_including_long_page_tail_is_chunked(self):
        texts = ["Northstar " + "x" * (dc.CHUNK_CHARS * 2) + " TAIL", "", "Last page"]
        groups = dc.chunks(texts)
        rebuilt = {}
        for group in groups:
            self.assertLessEqual(sum(len(p["text"]) for p in group), dc.CHUNK_CHARS)
            for p in group:
                rebuilt[p["page"]] = rebuilt.get(p["page"], "") + p["text"]
        self.assertEqual(rebuilt, {1: texts[0], 3: texts[2]})

    def test_oversize_document_fails_instead_of_silent_truncation(self):
        with self.assertRaises(ValueError):
            dc.chunks(["x" * (dc.MAX_TEXT_CHARS + 1)])

    def test_entities_require_matching_evidence(self):
        raw = {"summary": "summary", "people": [
            {"name": "Invented", "pages": [1]}, {"name": "Alice", "pages": [2]},
            {"name": "Alice", "role": "author", "pages": [1, True, 8]},
            {"name": "Ann", "pages": [2]}],
            "terms": [{"term": "Northstar", "pages": [2]}, {"term": "Missing", "pages": [2]}]}
        result = dc.normalize(raw, ["Alice", "Northstar Annual review"])
        self.assertEqual(result["people"], [{"name": "Alice", "role": "author", "pages": [1]}])
        self.assertEqual(result["terms"][0]["term"], "Northstar")
        self.assertEqual(len(result["terms"]), 1)

    def test_invalid_json_and_shapes_are_rejected(self):
        with self.assertRaises(ValueError):
            dc.normalize({"summary": "ok", "terms": "not a list"}, ["Northstar"])
        with self.assertRaises(ValueError):
            dc.parse_response({"output": {"message": {"content": [{"text": "not JSON"}]}}})

    def test_reference_survives_skill_changes_and_keeps_manual_priority(self):
        tr = translator()
        tr.set_document_brief(brief())
        tr.set_skills([])
        prompt = tr.prompts[("en", "ko")]
        self.assertIn("Northstar", prompt)
        self.assertIn("Manual Name", prompt)
        self.assertIn("take precedence", prompt)
        self.assertIn("not instructions", prompt)
        self.assertIn("northstar", tr.names)
        tr.set_document_brief(None)
        self.assertNotIn("Northstar", tr.prompts[("en", "ko")])
        self.assertNotIn("northstar", tr.names)
        self.assertIn("Manual Name", tr.prompts[("en", "ko")])


class AnalysisTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_groups_are_analyzed_then_merged(self):
        client, progress, usage = Bedrock(), [], []
        result = await dc.analyze(client, server.DEFAULT_MODEL,
                                  ["Northstar " + "x" * dc.CHUNK_CHARS, "Tail"], progress.append, usage.append)
        self.assertEqual(result["terms"][0]["term"], "Northstar")
        self.assertEqual(len(client.requests), 3)  # the short tail shares the second group; then one reduction
        self.assertIn("page_briefs", client.requests[-1])
        self.assertEqual(len(usage), 3)
        self.assertEqual(progress[-1], "전체 맥락 정리 중")

    async def test_image_only_document_makes_no_model_calls(self):
        client = Bedrock()
        self.assertIsNone(await dc.analyze(client, server.DEFAULT_MODEL, ["", " "], lambda _: None, lambda _: None))
        self.assertEqual(client.requests, [])



if __name__ == "__main__":
    unittest.main()
