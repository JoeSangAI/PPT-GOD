import re


THEMATIC_BREAK_RE = re.compile(r"^\s{0,3}(?:(?:-\s*){3,}|(?:_\s*){3,}|(?:\*\s*){3,})\s*$")
LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)、])\s+")


def strip_think_tags(text: str) -> str:
    """Remove <think>...</think> blocks from text."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def strip_markdown_code_fences(text: str) -> str:
    """Remove markdown code block fences (```language and ```)."""
    text = re.sub(r"^```(?:\w+)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"```$", "", text, flags=re.MULTILINE)
    return text.strip()


def normalize_markdown_emphasis(text: str) -> str:
    """
    Remove orphan emphasis delimiters without flattening valid Markdown.

    LLMs sometimes emit lines such as "**第四部分：媒介与资本叙事" with a
    missing closing delimiter. Markdown renderers treat that marker as literal
    text, and image prompts can then ask the model to render the stray "**".
    This keeps balanced pairs intact while deleting only unpaired ** / __.
    """
    if not text:
        return text

    def clean_line(line: str, delimiter: str) -> str:
        positions = [m.start() for m in re.finditer(re.escape(delimiter), line)]
        if len(positions) % 2 == 0:
            return line
        stripped = line.lstrip()
        leading_ws = len(line) - len(stripped)
        if stripped.startswith(delimiter):
            return line + delimiter
        if line.rstrip().endswith(delimiter):
            return line[:leading_ws] + delimiter + line[leading_ws:]
        remove_at = positions[-1]
        return line[:remove_at] + line[remove_at + len(delimiter):]

    cleaned_lines = []
    for line in text.splitlines():
        line = clean_line(line, "**")
        line = clean_line(line, "__")
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def is_markdown_thematic_break_line(line: str) -> bool:
    """Return True for Markdown horizontal-rule lines, including empty list items."""
    if not str(line or "").strip():
        return False
    stripped = str(line or "").strip()
    if THEMATIC_BREAK_RE.match(stripped):
        return True
    unlisted = LIST_MARKER_RE.sub("", str(line or ""), count=1).strip()
    return unlisted != stripped and bool(THEMATIC_BREAK_RE.match(unlisted))


def remove_markdown_structural_noise(text: str) -> str:
    """Remove Markdown control-only lines that should not become slide copy."""
    if not text:
        return text

    cleaned_lines = []
    in_code_fence = False
    fence_marker = ""
    for line in str(text).splitlines():
        fence_match = re.match(r"^\s*(```+|~~~+)", line)
        if fence_match:
            marker = fence_match.group(1)[:3]
            if not in_code_fence:
                in_code_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_code_fence = False
                fence_marker = ""
            cleaned_lines.append(line.rstrip())
            continue
        if not in_code_fence and is_markdown_thematic_break_line(line):
            continue
        cleaned_lines.append(line.rstrip())
    return "\n".join(cleaned_lines).strip()


def normalize_markdown_content(text: str) -> str:
    """Normalize Markdown text before storing or sending it to image prompts."""
    return remove_markdown_structural_noise(normalize_markdown_emphasis(text or ""))


def clean_llm_output(text: str) -> str:
    """Apply all standard cleaning steps to LLM output:
    1. Remove think tags
    2. Remove markdown code fences
    3. Strip whitespace
    """
    text = strip_think_tags(text)
    text = strip_markdown_code_fences(text)
    return text.strip()
