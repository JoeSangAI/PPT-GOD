import re


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


def clean_llm_output(text: str) -> str:
    """Apply all standard cleaning steps to LLM output:
    1. Remove think tags
    2. Remove markdown code fences
    3. Strip whitespace
    """
    text = strip_think_tags(text)
    text = strip_markdown_code_fences(text)
    return text.strip()
