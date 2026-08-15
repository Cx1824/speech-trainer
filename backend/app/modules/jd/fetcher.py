"""JD（职位描述）抓取模块。

通用网页抓取：获取 HTML，提取 title + meta description + 主要可见文本。
不做站点特定适配，反爬严格的招聘网站可能会失败，前端会提示用户手动粘贴。
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# 通用浏览器 UA
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)

MAX_CONTENT_LENGTH = 8000  # 抓取后保留的最大字符数


async def fetch_jd(url: str) -> dict[str, Any]:
    """抓取 URL 的 JD 内容。

    返回 dict: {title, company, content, url, success, message}
    """
    if not _is_valid_url(url):
        return {
            "title": "",
            "company": "",
            "content": "",
            "url": url,
            "success": False,
            "message": "URL 不合法",
        }

    try:
        async with httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        ) as client:
            resp = await client.get(url)

        if resp.status_code != 200:
            return {
                "title": "",
                "company": "",
                "content": "",
                "url": url,
                "success": False,
                "message": f"网页返回 {resp.status_code}",
            }

        html = resp.text
        result = _extract_from_html(html, url)
        result["url"] = url
        result["success"] = True
        result["message"] = "抓取成功"
        return result

    except httpx.TimeoutException:
        return _fail(url, "抓取超时")
    except httpx.HTTPError as e:
        logger.warning("JD 抓取失败：%s", e)
        return _fail(url, f"网络错误：{e}")
    except Exception as e:
        logger.exception("JD 抓取异常")
        return _fail(url, f"未知错误：{e}")


def _is_valid_url(url: str) -> bool:
    return bool(re.match(r"^https?://", url, re.IGNORECASE))


def _fail(url: str, message: str) -> dict[str, Any]:
    return {
        "title": "",
        "company": "",
        "content": "",
        "url": url,
        "success": False,
        "message": message,
    }


def _extract_from_html(html: str, url: str) -> dict[str, str]:
    """从 HTML 提取关键信息（无依赖，纯字符串解析）。"""
    title = _extract_title(html)
    company = _extract_company(html, title)
    description = _extract_meta_description(html)
    main_text = _extract_main_text(html)

    # 组合内容：description + 主要文本（截断）
    parts = []
    if description and description not in main_text:
        parts.append(description)
    if main_text:
        parts.append(main_text)
    content = "\n\n".join(parts)[:MAX_CONTENT_LENGTH]

    return {
        "title": title,
        "company": company,
        "content": content,
    }


def _extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    if m:
        return _clean(m.group(1))
    return ""


def _extract_meta_description(html: str) -> str:
    # <meta name="description" content="...">
    patterns = [
        r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']+)["\']',
        r'<meta\s+content=["\']([^"\']+)["\']\s+name=["\']description["\']',
        r'<meta\s+property=["\']og:description["\']\s+content=["\']([^"\']+)["\']',
    ]
    for p in patterns:
        m = re.search(p, html, re.IGNORECASE)
        if m:
            return _clean(m.group(1))
    return ""


def _extract_company(html: str, title: str) -> str:
    """尝试从 meta 或 title 推断公司名。"""
    # og:site_name
    m = re.search(
        r'<meta\s+property=["\']og:site_name["\']\s+content=["\']([^"\']+)["\']',
        html,
        re.IGNORECASE,
    )
    if m:
        return _clean(m.group(1))

    # title 里常见格式：「职位名-公司名-招聘网站」
    if "-" in title or "—" in title or "_" in title:
        parts = re.split(r"[-—_]", title)
        if len(parts) >= 2:
            # 取倒数第二段（通常公司名）
            candidate = _clean(parts[-2])
            if candidate and "招聘" not in candidate and len(candidate) < 20:
                return candidate
    return ""


def _extract_main_text(html: str) -> str:
    """提取页面可见文本（简化版）。"""
    # 移除 script/style/nav/footer
    html = re.sub(r"<(script|style|nav|footer|header|aside)[^>]*>.*?</\1>",
                  "", html, flags=re.IGNORECASE | re.DOTALL)
    # 移除 HTML 注释
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    # 标签转换：把 <br>, <p>, <div>, <li> 转换为换行
    html = re.sub(r"<(br|/p|/div|/li|/h[1-6])[^>]*>", "\n", html, flags=re.IGNORECASE)
    # 移除其他标签
    text = re.sub(r"<[^>]+>", "", html)
    # HTML 实体
    text = (text
            .replace("&nbsp;", " ")
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"'))
    # 折叠空白
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return _clean(text).strip()


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()
