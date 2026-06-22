import re
from typing import Any


_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CHINESE_NUMERAL_RE = r"[零〇一二两三四五六七八九十百千万]+"
_ORDINAL_TOKEN_RE = rf"(?:0?\d{{1,3}}|{_CHINESE_NUMERAL_RE})"


def _ordinal_to_int(value: str) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text in _CHINESE_DIGITS:
        return _CHINESE_DIGITS[text]
    if "十" in text:
        left, _, right = text.partition("十")
        tens = 1 if not left else _CHINESE_DIGITS.get(left)
        ones = 0 if not right else _CHINESE_DIGITS.get(right)
        if tens is None or ones is None:
            return None
        return tens * 10 + ones
    return None


def section_ordinals(text: str) -> set[int]:
    value = str(text or "")
    ordinals: set[int] = set()
    patterns = (
        rf"模块\s*({_ORDINAL_TOKEN_RE})",
        rf"第\s*({_ORDINAL_TOKEN_RE})\s*(?:章|章节|部分|篇|节)?",
        r"\b(?:part|chapter)\s*0?(\d{1,3})\b",
        rf"(?:章节)?(?:编号|序号)\s*[「『“\"']?\s*({_ORDINAL_TOKEN_RE})\s*[」』”\"']?",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, value, flags=re.IGNORECASE):
            ordinal = _ordinal_to_int(match.group(1))
            if ordinal is not None:
                ordinals.add(ordinal)
    if re.fullmatch(rf"\s*{_ORDINAL_TOKEN_RE}\s*", value):
        ordinal = _ordinal_to_int(value)
        if ordinal is not None:
            ordinals.add(ordinal)
    return ordinals


def _identity_key(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", str(text or ""), flags=re.UNICODE).lower()


def is_structural_section_label(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    compact = re.sub(r"\s+", "", value)
    patterns = (
        rf"第{_ORDINAL_TOKEN_RE}(?:章|章节|部分|篇|节|部)",
        rf"模块{_ORDINAL_TOKEN_RE}",
        rf"(?:章节)?(?:编号|序号){_ORDINAL_TOKEN_RE}",
        r"(?:part|chapter)0?\d{1,3}",
    )
    return any(re.fullmatch(pattern, compact, flags=re.IGNORECASE) for pattern in patterns)


def should_render_section_title(section_title: str, content_text: dict[str, Any] | None) -> bool:
    title = str(section_title or "").strip()
    if not title:
        return False
    if is_structural_section_label(title):
        return False
    content_text = content_text or {}
    visible_values = [
        str(content_text.get("headline") or "").strip(),
        str(content_text.get("subhead") or "").strip(),
    ]
    title_key = _identity_key(title)
    for value in visible_values:
        value_key = _identity_key(value)
        if title_key and value_key and (title_key == value_key or title_key in value_key or value_key in title_key):
            return False

    title_ordinals = section_ordinals(title)
    visible_ordinals: set[int] = set()
    for value in visible_values:
        visible_ordinals.update(section_ordinals(value))
    if title_ordinals and title_ordinals.issubset(visible_ordinals):
        return False
    return True


def sanitize_section_visual_numbering(text: str) -> str:
    value = str(text or "")
    if not value:
        return value

    value = re.sub(rf"第\s*{_ORDINAL_TOKEN_RE}\s*(?:章|章节|部分|篇|节|部)\s*[:：-]?", "", value)
    value = re.sub(
        r"(章节标题|章节名)\s*[/、,，和与]*\s*(?:编号|序号)\s*[/、,，和与]*\s*(转场氛围)?",
        lambda match: f"{match.group(1)}/转场氛围" if match.group(2) else match.group(1),
        value,
    )
    value = re.sub(
        rf"(?:章节)?(?:编号|序号)\s*[「『“\"']?\s*{_ORDINAL_TOKEN_RE}\s*[」』”\"']?",
        "标题转场",
        value,
    )
    value = re.sub(r"(?:part|chapter)\s*0?\d{1,3}\s*(?:编号|序号)", "标题转场", value, flags=re.IGNORECASE)
    value = re.sub(rf"模块\s*{_ORDINAL_TOKEN_RE}", "章节", value)
    value = re.sub(rf"[「『“\"']\s*{_ORDINAL_TOKEN_RE}\s*[」』”\"']", "", value)
    value = re.sub(r"数字\s*章节\s*转场", "标题转场", value)
    value = re.sub(r"数字\s*章节", "标题", value)
    value = value.replace("章节转场", "标题转场")
    value = re.sub(r"章节\s*编号", "章节标题", value)
    value = value.replace("编号", "标题").replace("序号", "标题")
    value = re.sub(r"(?:01\s*/\s*03|0?\d{1,3}\s*/\s*0?\d{1,3})\s*类?", "", value)
    value = re.sub(r"(?:阿拉伯|罗马)?数字", "标题", value)
    value = re.sub(r"标题转场\s*[与和]\s*主标题", "主标题", value)
    value = re.sub(r"章节标题\s*/\s*标题转场\s*/\s*转场氛围", "章节标题/转场氛围", value)
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s*（\s*）", "", value)
    return value.strip()
