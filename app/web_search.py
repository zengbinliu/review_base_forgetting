"""联网搜索：为出题/判分补充公开网页摘要（可选）。失败则静默回退本地。"""
from __future__ import annotations

import re
from typing import List
from urllib.parse import quote

import requests


def search_web(query: str, max_results: int = 5) -> List[dict]:
    """
    返回 [{title, href, body}, ...]。
    依次尝试 ddgs / duckduckgo_search / Bing HTML；全部失败则返回空列表。
    """
    query = (query or "").strip()
    if not query:
        return []

    for fn in (_search_ddgs, _search_duckduckgo_legacy, _search_bing_html):
        try:
            results = fn(query, max_results=max_results)
            if results:
                return results[:max_results]
        except Exception:
            continue
    return []


def _search_ddgs(query: str, max_results: int = 5) -> List[dict]:
    try:
        from ddgs import DDGS
    except ImportError:
        return []
    results: List[dict] = []
    with DDGS() as ddgs:
        for item in ddgs.text(query, max_results=max_results):
            parsed = _normalize_item(item)
            if parsed:
                results.append(parsed)
    return results


def _search_duckduckgo_legacy(query: str, max_results: int = 5) -> List[dict]:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        return []
    results: List[dict] = []
    with DDGS() as ddgs:
        for item in ddgs.text(query, max_results=max_results):
            parsed = _normalize_item(item)
            if parsed:
                results.append(parsed)
    return results


def _search_bing_html(query: str, max_results: int = 5) -> List[dict]:
    """无 Key 的 Bing 结果页解析（可能随页面改版失效）。"""
    url = f"https://www.bing.com/search?q={quote(query)}&setlang=zh-CN"
    session = requests.Session()
    session.trust_env = False
    resp = session.get(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
        timeout=15,
    )
    if resp.status_code >= 400:
        return []
    html = resp.text
    # 粗匹配 Bing 结果块
    pattern = re.compile(
        r'<li class="b_algo".*?<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
        r'.*?(?:<p[^>]*>(.*?)</p>|<div class="b_caption"[^>]*>.*?<p[^>]*>(.*?)</p>)',
        re.I | re.S,
    )
    results: List[dict] = []
    for m in pattern.finditer(html):
        href = m.group(1).strip()
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        body_raw = m.group(3) or m.group(4) or ""
        body = re.sub(r"<[^>]+>", "", body_raw).strip()
        if title or body:
            results.append({"title": title, "href": href, "body": body})
        if len(results) >= max_results:
            break
    return results


def _normalize_item(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None
    title = str(item.get("title") or "").strip()
    href = str(item.get("href") or item.get("link") or item.get("url") or "").strip()
    body = str(
        item.get("body") or item.get("snippet") or item.get("description") or ""
    ).strip()
    if not (title or body):
        return None
    return {"title": title, "href": href, "body": body}


def format_search_context(results: List[dict], max_chars: int = 4000) -> str:
    if not results:
        return ""
    parts = []
    used = 0
    for i, r in enumerate(results, 1):
        block = (
            f"{i}. {r.get('title') or '（无标题）'}\n"
            f"来源: {r.get('href') or '-'}\n"
            f"摘要: {r.get('body') or '-'}"
        )
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)


def build_search_query(title: str, content: str = "") -> str:
    title = (title or "").strip()
    content = (content or "").strip().replace("\n", " ")
    snippet = content[:80] if content else ""
    if title and snippet:
        return f"{title} {snippet}"
    return title or snippet
