import re


def strip_think_tags(text: str) -> str:
    """Remove <think>...</think> blocks from text."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def strip_markdown_code_fences(text: str) -> str:
    """Remove markdown code block fences (```language and ```)."""
    text = re.sub(r"^```(?:\w+)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"```$", "", text, flags=re.MULTILINE)
    return text.strip()


def clean_llm_output(text: str) -> str:
    """Apply all standard cleaning steps to LLM output:
    1. Remove think tags
    2. Remove markdown code fences
    3. Strip whitespace
    """
    text = strip_think_tags(text)
    text = strip_markdown_code_fences(text)
    return text.strip()
