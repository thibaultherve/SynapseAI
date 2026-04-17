"""Prompt-building primitives shared across every Claude call path.

Provides:
- `sanitize_user_content()` — strips prompt-injection patterns (markdown headers,
  role-prefix lines, delimiter lookalikes) from user-authored or PDF-extracted
  text before it is injected into a Claude prompt.
- `build_fenced_prompt()` — wraps each user-authored block in a per-request
  UUID-delimited fence and interpolates it into the caller's template. The
  template must reference `{delim}` in its system section so Claude is told
  explicitly which bytes are data vs. instructions.
- `generate_canary()` / `canary_in_output()` — best-effort markers for
  detecting prompt-injection leakage in Claude responses. Optional per spec 3.4.
"""

from __future__ import annotations

import re
import secrets
import uuid

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
