import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.llm_client import ClaudeError, call_claude
from app.processing.claude_service import sanitize_tag_output


@pytest.mark.asyncio
async def test_call_claude_valid_json():
    """Mock subprocess returns valid JSON -> parsed result."""
    expected = {"result": "Hello from Claude"}

    mock_process = AsyncMock()
    mock_process.communicate.return_value = (json.dumps(expected).encode(), b"")
    mock_process.returncode = 0

    with patch("asyncio.create_subprocess_exec", return_value=mock_process):
        result = await call_claude("test prompt")

    assert result == "Hello from Claude"


@pytest.mark.asyncio
async def test_call_claude_timeout():
    """Mock subprocess times out -> ClaudeError."""
    mock_process = AsyncMock()
    mock_process.communicate.side_effect = TimeoutError()
    mock_process.kill = MagicMock()
    mock_process.wait = AsyncMock()

    with (
        patch("asyncio.create_subprocess_exec", return_value=mock_process),
        pytest.raises(ClaudeError) as exc_info,
    ):
        await call_claude("test prompt", timeout=1.0)

    assert exc_info.value.code == "CLAUDE_TIMEOUT"


@pytest.mark.asyncio
async def test_call_claude_nonzero_exit():
    """Mock subprocess returns nonzero exit -> ClaudeError."""
    mock_process = AsyncMock()
    mock_process.communicate.return_value = (b"", b"Error: something failed")
    mock_process.returncode = 1

    with (
        patch("asyncio.create_subprocess_exec", return_value=mock_process),
        pytest.raises(ClaudeError) as exc_info,
    ):
        await call_claude("test prompt")

    assert exc_info.value.code == "CLAUDE_ERROR"


# --- T15: sanitize_tag_output ---


class TestSanitizeTagOutput:
    """Tests for sanitize_tag_output — validates Claude's tagging output."""

    EXISTING_IDS = {1, 2, 5, 10}

    def test_valid_existing_id(self):
        raw = [{"existing_id": 5}]
        result = sanitize_tag_output(raw, self.EXISTING_IDS)
        assert result == [{"existing_id": 5}]

    def test_invalid_existing_id_not_in_db(self):
        raw = [{"existing_id": 999}]
        result = sanitize_tag_output(raw, self.EXISTING_IDS)
        assert result == []

    def test_existing_id_not_int(self):
        raw = [{"existing_id": "five"}]
        result = sanitize_tag_output(raw, self.EXISTING_IDS)
        assert result == []

    def test_valid_new_tag(self):
        raw = [{"new": {"name": "scRNA-seq", "category": "technique", "description": "Single-cell RNA sequencing"}}]
        result = sanitize_tag_output(raw, self.EXISTING_IDS)
        assert len(result) == 1
        assert result[0]["new"]["name"] == "scRNA-seq"
        assert result[0]["new"]["category"] == "technique"

    def test_new_tag_invalid_category(self):
        raw = [{"new": {"name": "test", "category": "INVALID"}}]
        result = sanitize_tag_output(raw, self.EXISTING_IDS)
        assert result == []

    def test_new_tag_malicious_name_html(self):
        raw = [{"new": {"name": "<script>alert('xss')</script>", "category": "technique"}}]
        result = sanitize_tag_output(raw, self.EXISTING_IDS)
        assert result == []

    def test_new_tag_name_too_long(self):
        raw = [{"new": {"name": "A" * 101, "category": "technique"}}]
        result = sanitize_tag_output(raw, self.EXISTING_IDS)
        assert result == []

    def test_new_tag_empty_name(self):
        raw = [{"new": {"name": "", "category": "technique"}}]
        result = sanitize_tag_output(raw, self.EXISTING_IDS)
        assert result == []

    def test_new_tag_description_truncated(self):
        raw = [{"new": {"name": "test-tag", "category": "topic", "description": "D" * 600}}]
        result = sanitize_tag_output(raw, self.EXISTING_IDS)
        assert len(result) == 1
        assert len(result[0]["new"]["description"]) == 500

    def test_mixed_valid_and_invalid(self):
        raw = [
            {"existing_id": 5},
            {"existing_id": 999},  # invalid
            {"new": {"name": "valid-tag", "category": "sub_domain"}},
            {"new": {"name": "<bad>", "category": "technique"}},  # invalid
            {"garbage": True},  # ignored
        ]
        result = sanitize_tag_output(raw, self.EXISTING_IDS)
        assert len(result) == 2
        assert result[0] == {"existing_id": 5}
        assert result[1]["new"]["name"] == "valid-tag"

    def test_new_tag_name_with_allowed_special_chars(self):
        """Tag names with parentheses, slashes, dots etc. should pass."""
        raw = [{"new": {"name": "fMRI (resting-state)", "category": "technique"}}]
        result = sanitize_tag_output(raw, self.EXISTING_IDS)
        assert len(result) == 1
        assert result[0]["new"]["name"] == "fMRI (resting-state)"
