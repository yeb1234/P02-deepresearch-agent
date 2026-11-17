"""搜索 MCP 结果的中间层归一化。

MCP 应用返回的是“搜索结果 + 应用自己写的一段说明”，字段可能重复、
缺失或带跟踪参数。这里在进入 LLM 之前统一成稳定的
`{title, url, snippet}` 结构，减少重复上下文和无效链接。
"""

from __future__ import annotations

import os
import re
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


_TRACKING_PARAMS = {
    "spm",
    "from",
    "source",
    "share",
    "share_token",
    "timestamp",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
}

_HOST_ALIASES = {
    "wapbaike.baidu.com": "baike.baidu.com",
    "m.baike.baidu.com": "baike.baidu.com",
}

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\u4e00-\u9fff]+")


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


MAX_ITEMS = _env_int("SEARCH_MAX_ITEMS", 8, 3, 20)
MAX_SNIPPET_CHARS = _env_int("SEARCH_MAX_SNIPPET_CHARS", 320, 80, 1200)
MAX_TOTAL_CHARS = _env_int("SEARCH_MAX_TOTAL_CHARS", 4200, 1200, 12000)


def canonical_url(url: str) -> str:
    """去掉跟踪参数、统一主机别名和末尾斜杠，用于去重。"""
    raw = (url or "").strip().rstrip(".,;:)]}\"'")
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""

    host = parsed.netloc.lower().split("@")[-1]
    host = _HOST_ALIASES.get(host, host)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_PARAMS
    ]
    path = re.sub(r"/+", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urlunparse(
        (
            parsed.scheme.lower(),
            host,
            path,
            "",
            urlencode(query, doseq=True),
            "",
        )
    )


def _text_key(text: str) -> str:
    return _PUNCT_RE.sub("", _WS_RE.sub(" ", (text or "").lower()))


def _text_similar(a: str, b: str) -> bool:
    a_key = _text_key(a)
    b_key = _text_key(b)
    if not a_key or not b_key:
        return False
    shorter, longer = sorted((a_key, b_key), key=len)
    if len(shorter) >= 18 and shorter in longer and len(shorter) / len(longer) >= 0.72:
        return True
    return SequenceMatcher(None, a_key, b_key).ratio() >= 0.86


def _same_host_similar(a: str, b: str) -> bool:
    """同站点正文相似度容忍度更低：转载/移动站经常只改标题。"""
    a_key = _text_key(a)
    b_key = _text_key(b)
    if not a_key or not b_key:
        return False
    if min(len(a_key), len(b_key)) < 30:
        return False
    return SequenceMatcher(None, a_key, b_key).ratio() >= 0.72


def _is_duplicate(left: dict, right: dict) -> bool:
    if left["_canonical"] and left["_canonical"] == right["_canonical"]:
        return True

    left_title = _text_key(left.get("title", ""))
    right_title = _text_key(right.get("title", ""))
    left_snippet = left.get("snippet", "")
    right_snippet = right.get("snippet", "")

    if left_title and left_title == right_title and _text_similar(left_snippet, right_snippet):
        return True

    # 同一站点下的同一篇内容，标题可能被应用改写；正文相似即可去重。
    left_host = urlparse(left["_canonical"]).netloc
    right_host = urlparse(right["_canonical"]).netloc
    if left_host and left_host == right_host and _same_host_similar(left_snippet, right_snippet):
        return True

    # 同一篇报道常被不同站点转载；标题相似且正文高度相似时只保留一条。
    return _text_similar(left_title, right_title) and _text_similar(left_snippet, right_snippet)


def _clean_item(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None
    url = canonical_url(str(item.get("url") or ""))
    if not url:
        return None

    title = _WS_RE.sub(" ", str(item.get("title") or "")).strip()
    snippet = _WS_RE.sub(
        " ", str(item.get("snippet") or item.get("summary") or item.get("description") or "")
    ).strip()
    if not snippet and not title:
        return None

    return {
        "title": (title or url)[:120],
        "url": url,
        "snippet": snippet[:MAX_SNIPPET_CHARS],
        "_canonical": url,
    }


def normalize_search_results(
    items: list[dict] | None,
    *,
    max_items: int | None = None,
    max_snippet_chars: int | None = None,
    max_total_chars: int | None = None,
) -> list[dict]:
    """去重、清洗并限制搜索结果预算，返回可直接交给 LLM 的列表。"""
    limit_items = max_items or MAX_ITEMS
    limit_snippet = max_snippet_chars or MAX_SNIPPET_CHARS
    limit_total = max_total_chars or MAX_TOTAL_CHARS

    cleaned: list[dict] = []
    for raw in items or []:
        item = _clean_item(raw)
        if not item:
            continue
        if any(_is_duplicate(item, existing) for existing in cleaned):
            # 保留信息更完整的一条，避免先到的短摘要压掉后到的长摘要。
            for existing in cleaned:
                if _is_duplicate(item, existing):
                    if len(item.get("snippet", "")) > len(existing.get("snippet", "")):
                        existing["title"] = item["title"]
                        existing["snippet"] = item["snippet"]
                    break
            continue
        cleaned.append(item)

    kept: list[dict] = []
    used_chars = 0
    for item in cleaned[:limit_items]:
        snippet = item.get("snippet", "")[:limit_snippet]
        title = item.get("title", "")
        remaining = limit_total - used_chars - len(title) - 8
        if remaining < 40:
            break
        snippet = snippet[:remaining]
        entry = {
            "title": title,
            "url": item["url"],
            "snippet": snippet,
        }
        kept.append(entry)
        used_chars += len(title) + len(snippet) + 8

    return kept


def normalize_stats(before: int, after: int) -> str:
    return f"{before} -> {after} 条（去重/预算后）"
