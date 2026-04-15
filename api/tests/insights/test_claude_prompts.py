"""Tests T24-T25: sanitize_insight_output.

Verifies the Literal/UUID-whitelist + length-cap sanitization applied to
Claude's raw insight generation output before persistence.
"""

import json
import uuid

from app.insights.claude_prompts import sanitize_insight_output


class TestSanitizeInsightOutput:
    """T24: supporting_papers UUIDs orphelins sont droppes.
    T25: type hors Literal whitelist -> reject silencieux.
    """

    def _make_raw(self, items: list[dict]) -> str:
        return json.dumps(items)

    def test_orphan_uuids_are_dropped(self):
        """T24: UUIDs not in valid_paper_ids are filtered out."""
        real_a = str(uuid.uuid4())
        real_b = str(uuid.uuid4())
        orphan = str(uuid.uuid4())
        raw = self._make_raw([
            {
                "type": "trend",
                "title": "Title 1",
                "content": "Content",
                "evidence": "Evidence",
                "confidence": "high",
                "supporting_papers": [real_a, real_b, orphan],
            }
        ])
        result = sanitize_insight_output(raw, valid_paper_ids={real_a, real_b})
        assert len(result) == 1
        assert orphan not in result[0].supporting_papers
        assert set(result[0].supporting_papers) == {real_a, real_b}

    def test_insight_dropped_if_supporting_papers_below_min(self):
        """T24 corollary: after orphan filter, <2 papers -> dropped."""
        real_a = str(uuid.uuid4())
        orphan = str(uuid.uuid4())
        raw = self._make_raw([
            {
                "type": "trend",
                "title": "Only one valid paper",
                "content": "Content",
                "evidence": "Evidence",
                "confidence": "high",
                "supporting_papers": [real_a, orphan],
            }
        ])
        result = sanitize_insight_output(raw, valid_paper_ids={real_a})
        assert result == []

    def test_type_outside_literal_is_rejected(self):
        """T25: type not in Literal whitelist is dropped silently."""
        real_a = str(uuid.uuid4())
        real_b = str(uuid.uuid4())
        raw = self._make_raw([
            {
                "type": "concordance",  # not in whitelist (should be 'trend')
                "title": "Bad type",
                "content": "Content",
                "evidence": "Evidence",
                "confidence": "high",
                "supporting_papers": [real_a, real_b],
            },
            {
                "type": "trend",  # valid
                "title": "Good type",
                "content": "Content",
                "evidence": "Evidence",
                "confidence": "high",
                "supporting_papers": [real_a, real_b],
            },
        ])
        result = sanitize_insight_output(raw, valid_paper_ids={real_a, real_b})
        assert len(result) == 1
        assert result[0].type == "trend"

    def test_confidence_outside_literal_is_rejected(self):
        """T25 corollary: confidence outside whitelist -> dropped."""
        real_a = str(uuid.uuid4())
        real_b = str(uuid.uuid4())
        raw = self._make_raw([
            {
                "type": "trend",
                "title": "T",
                "content": "C",
                "evidence": "E",
                "confidence": "super-high",  # invalid
                "supporting_papers": [real_a, real_b],
            }
        ])
        result = sanitize_insight_output(raw, valid_paper_ids={real_a, real_b})
        assert result == []

    def test_gap_requires_three_supporting_papers(self):
        """Spec 3.3: gap needs >=3 supporting papers."""
        ids = [str(uuid.uuid4()) for _ in range(2)]
        raw = self._make_raw([
            {
                "type": "gap",
                "title": "gap",
                "content": "C",
                "evidence": "E",
                "confidence": "medium",
                "supporting_papers": ids,
            }
        ])
        result = sanitize_insight_output(raw, valid_paper_ids=set(ids))
        assert result == []

    def test_length_caps_applied(self):
        real_a = str(uuid.uuid4())
        real_b = str(uuid.uuid4())
        raw = self._make_raw([
            {
                "type": "trend",
                "title": "X" * 500,
                "content": "Y" * 3000,
                "evidence": "Z" * 3000,
                "confidence": "low",
                "supporting_papers": [real_a, real_b],
            }
        ])
        result = sanitize_insight_output(raw, valid_paper_ids={real_a, real_b})
        assert len(result) == 1
        assert len(result[0].title) <= 300
        assert len(result[0].content) <= 2000
        assert result[0].evidence is not None
        assert len(result[0].evidence) <= 2000

    def test_non_list_json_returns_empty(self):
        raw = json.dumps({"not": "a list"})
        result = sanitize_insight_output(raw, valid_paper_ids=set())
        assert result == []

    def test_empty_raw_returns_empty(self):
        assert sanitize_insight_output("", valid_paper_ids=set()) == []

    def test_malformed_json_returns_empty(self):
        result = sanitize_insight_output("[{not valid}]", valid_paper_ids=set())
        assert result == []
