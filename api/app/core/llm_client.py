"""Canonical Claude CLI client and prompt-building primitives.

Public surface (import from here, not from `app.processing`):

- Error type: `ClaudeError`
- Concurrency guard: `_claude_semaphore` (plan-Max 1 concurrent CLI call)
- Callers:
  - `call_claude(prompt, timeout=None)` — single-shot JSON response
  - `call_claude_locked(prompt, timeout=None)` — same, serialized via semaphore
  - `stream_claude(prompt, ...)` — streaming generator
- Prompt hardening:
  - `sanitize_user_content(text, max_chars=None)` — strips injection patterns
  - `build_fenced_prompt(template, user_blocks, ...)` — UUID-fenced interpolation
  - `sanitize_summary_for_reuse(text, max_chars=2000)` — legacy default cap
  - `generate_canary()` / `canary_in_output(canary, output)` — leakage detection
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import uuid
from collections.abc import AsyncGenerator

from app.config import processing_settings
from app.core.exceptions import AppError


class ClaudeError(AppError):
    def __init__(self, code: str, message: str):
        super().__init__(code, message, status_code=502)


# Unified Claude CLI concurrency guard for plan-Max (1 call at a time across
# summarize/tagging/crossref/insight).
_claude_semaphore = asyncio.Semaphore(1)


# ---------------------------------------------------------------------------
# Prompt hardening
# ---------------------------------------------------------------------------

_MARKDOWN_HEADER_RE = re.compile(r"^#+\s.*$", re.MULTILINE)
_INJECTION_LINE_RE = re.compile(
    r"^\s*(ignore|system|assistant|user|instruction)s?\s*:.*$",
    re.MULTILINE | re.IGNORECASE,
)
_DELIM_LOOKALIKE_RE = re.compile(r"<<<[^>]{1,64}>>>")


def sanitize_user_content(
    text: str | None, max_chars: int | None = None
) -> str:
    """Strip prompt-injection patterns from untrusted text.

    Applied to:
    - PDF-extracted paper text before `generate_summaries` / `generate_tags` /
      `run_crossref_step`
    - Chat user messages
    - Model-generated summaries that are re-injected downstream (crossref,
      insights)

    Strips: markdown headers, role-prefix lines (`ignore:`, `system:`, etc.),
    UUID fence lookalikes (`<<<...>>>`). Collapses whitespace runs. Truncates
    to `max_chars` when provided.
    """
    if not text:
        return ""
    cleaned = _MARKDOWN_HEADER_RE.sub("", text)
    cleaned = _INJECTION_LINE_RE.sub("", cleaned)
    cleaned = _DELIM_LOOKALIKE_RE.sub("", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if max_chars is not None and len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars]
    return cleaned


def build_fenced_prompt(
    template: str,
    user_blocks: dict[str, str],
    *,
    other_vars: dict[str, object] | None = None,
    max_chars_per_block: int | None = None,
    sanitize: bool = True,
) -> str:
    """Wrap each user-authored block in a per-request UUID fence and
    interpolate into `template`.

    The template must contain:
    - `{delim}` in its system section (so Claude is told which delimiter
      marks data vs. instructions)
    - `{<key>}` placeholders for every entry in `user_blocks`
    - `{<key>}` placeholders for every entry in `other_vars` (unfenced — for
      trusted values like nonces, IDs, JSON-encoded DB state)

    Each user block becomes:
        <<<{delim}>>>
        {sanitized_content}
        <<</{delim}>>>

    Sanitization strips delimiter lookalikes in content so the caller cannot
    close or forge the fence from within the data.
    """
    delim = uuid.uuid4().hex
    fenced: dict[str, str] = {}
    for key, content in user_blocks.items():
        clean = (
            sanitize_user_content(content, max_chars=max_chars_per_block)
            if sanitize
            else (content or "")
        )
        fenced[key] = f"<<<{delim}>>>\n{clean}\n<<</{delim}>>>"

    format_args: dict[str, object] = {"delim": delim, **fenced}
    if other_vars:
        overlap = set(other_vars) & set(fenced)
        if overlap:
            raise ValueError(
                f"build_fenced_prompt: keys overlap between "
                f"user_blocks and other_vars: {sorted(overlap)}"
            )
        format_args.update(other_vars)
    return template.format(**format_args)


def sanitize_summary_for_reuse(text: str | None, max_chars: int = 2000) -> str:
    """Legacy default cap (2000 chars) for model-generated text reuse."""
    return sanitize_user_content(text, max_chars=max_chars)


def generate_canary() -> str:
    """Return a random canary token to embed in a prompt.

    Best-effort defense: if the token appears verbatim in Claude's response,
    it signals that a prompt-injection attempt likely coerced Claude into
    echoing the system prompt. Callers should log (not fail) on detection.
    """
    return f"CANARY-{secrets.token_hex(8)}"


def canary_in_output(canary: str, output: str | None) -> bool:
    """True when `canary` appears verbatim in `output` (injection signal)."""
    if not canary or not output:
        return False
    return canary in output


# ---------------------------------------------------------------------------
# Claude CLI callers
# ---------------------------------------------------------------------------


async def call_claude(prompt: str, timeout: float | None = None) -> str:
    timeout = timeout or processing_settings.CLAUDE_TIMEOUT
    process = await asyncio.create_subprocess_exec(
        "claude",
        "-p",
        "-",
        "--output-format",
        "json",
        "--max-turns",
        "1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(input=prompt.encode()), timeout=timeout
        )
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise ClaudeError(
            "CLAUDE_TIMEOUT", f"Claude CLI timed out after {timeout}s"
        ) from exc

    if process.returncode != 0:
        err_msg = (stderr.decode() + stdout.decode())[:500]
        raise ClaudeError("CLAUDE_ERROR", f"Claude CLI failed: {err_msg}")

    try:
        data = json.loads(stdout.decode())
        # --output-format json returns a list of messages.
        # Extract the text from the last assistant message.
        if isinstance(data, list):
            for msg in reversed(data):
                if msg.get("type") == "assistant" and "message" in msg:
                    content = msg["message"].get("content", [])
                    texts = [b["text"] for b in content if b.get("type") == "text"]
                    if texts:
                        return "\n".join(texts)
            return stdout.decode()
        return data.get("result", stdout.decode())
    except json.JSONDecodeError as e:
        raise ClaudeError(
            "CLAUDE_PARSE_ERROR", f"Failed to parse Claude response: {e}"
        ) from e


async def call_claude_locked(prompt: str, timeout: float | None = None) -> str:
    """Serialize Claude CLI calls across the app via `_claude_semaphore`.

    Wraps `call_claude` to enforce the plan-Max 1-concurrent-call constraint
    across every domain that talks to Claude (summaries, tags, crossref, insights).
    """
    async with _claude_semaphore:
        return await call_claude(prompt, timeout=timeout)


async def stream_claude(
    prompt: str,
    timeout_per_chunk: float = 30.0,
    stdin_drain_timeout: float = 10.0,
) -> AsyncGenerator[dict, None]:
    """Stream Claude CLI output as parsed chunks.

    Uses --output-format stream-json --max-turns 1. Yields dicts:
      - {"type": "content", "text": str}   per text delta
      - {"type": "error", "message": str}  on timeout/failure
      - {"type": "done", "full_text": str} at the end on success

    The subprocess is killed if the per-chunk readline times out, if stdin
    drain hangs longer than `stdin_drain_timeout`, if the generator is closed
    (client disconnect), or on unexpected errors. `finally` reaps the process
    (calls `process.wait()`) to avoid zombies.
    """
    process = await asyncio.create_subprocess_exec(
        "claude",
        "-p",
        "-",
        "--output-format",
        "stream-json",
        "--max-turns",
        "1",
        "--verbose",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    full_response: list[str] = []
    try:
        if process.stdin is not None:
            process.stdin.write(prompt.encode())
            try:
                await asyncio.wait_for(
                    process.stdin.drain(), timeout=stdin_drain_timeout
                )
            except TimeoutError:
                process.kill()
                yield {
                    "type": "error",
                    "message": (
                        f"Claude stdin drain timed out after "
                        f"{stdin_drain_timeout}s"
                    ),
                    "code": "CLAUDE_TIMEOUT",
                }
                return
            process.stdin.close()

        assert process.stdout is not None
        while True:
            try:
                line = await asyncio.wait_for(
                    process.stdout.readline(), timeout=timeout_per_chunk
                )
            except TimeoutError:
                process.kill()
                yield {
                    "type": "error",
                    "message": (
                        f"Response generation timed out after "
                        f"{timeout_per_chunk}s per chunk"
                    ),
                }
                return

            if not line:
                break

            try:
                event = json.loads(line.decode())
            except json.JSONDecodeError:
                continue

            # Extract text deltas from Claude's streaming envelope.
            # stream-json format wraps assistant messages in event envelopes.
            event_type = event.get("type")
            if event_type == "content_block_delta":
                delta = event.get("delta", {})
                text = delta.get("text") if isinstance(delta, dict) else None
                if text:
                    full_response.append(text)
                    yield {"type": "content", "text": text}
            elif event_type == "assistant":
                message = event.get("message", {}) or {}
                for block in message.get("content", []) or []:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        if text:
                            full_response.append(text)
                            yield {"type": "content", "text": text}

        returncode = await process.wait()
        if returncode != 0:
            assert process.stderr is not None
            stderr = await process.stderr.read()
            yield {
                "type": "error",
                "message": f"Claude CLI failed: {stderr.decode()[:200]}",
            }
            return

        yield {"type": "done", "full_text": "".join(full_response)}
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
