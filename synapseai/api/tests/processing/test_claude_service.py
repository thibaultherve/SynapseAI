import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.processing.claude_service import call_claude
from app.processing.exceptions import ClaudeError


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
