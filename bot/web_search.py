"""
Multi-Engine Web Search — DDG → Yandex → SearXNG → DDG API
Supports spare part search, news search, and general queries.
"""

import httpx
import re
import json
import time
import asyncio
import logging
from typing import List, Dict, Optional
from urllib.parse import quote_plus, urlencode

from bot.config import config

logger = logging.getLogger("masha.web_search")

# ── Search result model ────────────────────────────────────────────────────────

class SearchResult:
    """Single search result."""
    def __init__(self, title: str, url: str, snippet: str = "", source: str = ""):
        self.title = title
        self.url = url
        self.snippet = snippet
        self.source = source

    def to_dict(self) -> Dict[str, str]:
        return {"title": self.title, "url": self.url, "snippet": self.snippet, "source": self.source}


# ── DuckDuckGo HTML search ─────────────────────────────────────────────────────

DDG_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}

DDG_REGIONS = {
    "ru": "ru-ru",
    "en": "us-en",
    "de": "de-de",
}


async def search_ddg_html(query: str, max_results: int = 5, region: str = "ru") -> List[SearchResult]:
    """Search using DuckDuckGo HTML endpoint."""
    results = []
    try:
        async with httpx.AsyncClient(timeout=config.SEARCH_TIMEOUT_SECONDS, follow_redirects=True) as client:
            params = {
                "q": query,
                "kl": DDG_REGIONS.get(region, "ru-ru"),
                "no_redirect": "1",
            }
            response = await client.get("https://html.duckduckgo.com/html/", params=params, headers=DDG_HEADERS)
            if response.status_code != 200:
                logger.debug(f"DDG HTML returned {response.status_code}")
                if response.status_code == 202:
                    try:
                        await asyncio.sleep(0.5)
                        lite_params = {"q": query, "kl": DDG_REGIONS.get(region, "ru-ru")}
                        response = await client.get(
                            "https://lite.duckduckgo.com/lite/",
                            params=lite_params,
                            headers=DDG_HEADERS,
                        )
                        if response.status_code != 200:
                            return results
                        urls = re.findall(r'<a[^>]+class="result-link"[^>]+href="([^"]+)"', response.text)
                        titles = re.findall(r'<a[^>]+class="result-link"[^>]*>(.*?)</a>', response.text, re.DOTALL)
                        snippets = re.findall(r'<td[^>]+class="result-snippet"[^>]*>(.*?)</td>', response.text, re.DOTALL)
                        for i, url in enumerate(urls[:max_results]):
                            title = _clean_html(titles[i]) if i < len(titles) else ""
                            snippet = _clean_html(snippets[i]) if i < len(snippets) else ""
                            if url and title:
                                results.append(SearchResult(title=title, url=url, snippet=snippet, source="duckduckgo_lite"))
                        return results
                    except Exception as e2:
                        logger.debug(f"DDG Lite search error: {e2}")
                return results

            html = response.text

            result_blocks = re.findall(
                r'<a rel="nofollow" class="result__a" href="([^"]+?)".*?>(.*?)</a>.*?'
                r'<a class="result__snippet".*?>(.*?)</a>',
                html, re.DOTALL,
            )

            for url, title, snippet in result_blocks[:max_results]:
                title = _clean_html(title)
                snippet = _clean_html(snippet)
                if url and title:
                    results.append(SearchResult(title=title, url=url, snippet=snippet, source="duckduckgo"))

    except httpx.TimeoutException:
        logger.debug("DDG HTML search timed out")
    except Exception as e:
        logger.debug(f"DDG HTML search error: {e}")

    return results


# ── DuckDuckGo API (Instant Answer) ────────────────────────────────────────────

async def search_ddg_api(query: str, region: str = "ru") -> Optional[SearchResult]:
    """Search using DuckDuckGo Instant Answer API."""
    try:
        async with httpx.AsyncClient(timeout=config.SEARCH_TIMEOUT_SECONDS) as client:
            params = {
                "q": query,
                "format": "json",
                "no_html": "1",
                "skip_disambig": "1",
            }
            response = await client.get("https://api.duckduckgo.com/", params=params)
            if response.status_code == 200:
                data = response.json()
                abstract = data.get("AbstractText", "")
                url = data.get("AbstractURL", "")
                title = data.get("Heading", "")
                if abstract and url:
                    return SearchResult(title=title, url=url, snippet=abstract, source="ddg_api")
    except Exception as e:
        logger.error(f"DDG API search error: {e}")
    return None


# ── Yandex search ──────────────────────────────────────────────────────────────

async def search_yandex(query: str, max_results: int = 5) -> List[SearchResult]:
    """Search using Yandex (XML-like parsing)."""
    results = []
    try:
        async with httpx.AsyncClient(timeout=config.SEARCH_TIMEOUT_SECONDS, follow_redirects=True) as client:
            params = {
                "text": query,
                "lr": "213",
            }
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9",
            }
            response = await client.get("https://yandex.ru/search/", params=params, headers=headers)
            if response.status_code != 200:
                logger.warning(f"Yandex returned {response.status_code}")
                return results

            html = response.text
            link_pattern = re.findall(
                r'<a[^>]+href="((?:https?://)[^"]+)"[^>]*class="[^"]*Link[^"]*"[^>]*>(.*?)</a>',
                html, re.DOTALL,
            )
            if not link_pattern:
                link_pattern = re.findall(
                    r'<a[^>]+href="(https?://(?!yandex\.)[^"]+)"[^>]*>(.*?)</a>',
                    html, re.DOTALL,
                )

            seen_urls = set()
            for url, title in link_pattern[:max_results * 2]:
                title = _clean_html(title)
                if url not in seen_urls and title and len(title) > 5:
                    seen_urls.add(url)
                    results.append(SearchResult(title=title, url=url, snippet="", source="yandex"))
                    if len(results) >= max_results:
                        break

    except Exception as e:
        logger.error(f"Yandex search error: {e}")

    return results


# ── SearXNG search ─────────────────────────────────────────────────────────────

SEARXNG_INSTANCES = [
    "https://searx.be",
    "https://search.sapti.me",
    "https://searxng.ch",
    "https://baresearch.org",
    "https://searx.tiekoetter.com",
    "https://search.ononoki.org",
    "https://search.lvkaszus.pl",
    "https://searxng.site",
    "https://searxng.perennialte.ch",
    "https://search.0relay.com",
    "https://searxng.au",
    "https://searxng.shreven.org",
    "https://search.privacyredirect.com",
    "https://searxng.tordenskjold.one",
    "https://search.cronobox.one",
    "https://searx.fmac.xyz",
    "https://search.mdosch.de",
    "https://searx.prvcy.eu",
    "https://search.bus-hit.me",
    "https://search.rowie.at",
    "https://searx.divided-by-zero.eu",
    "https://search.sergioprado.blog",
    "https://searx.work",
    "https://searxng.bravefence.com",
    "https://searx.no-logs.com",
    "https://searx.datura.network",
    "https://search.rhscze.cf",
    "https://search.charleseroop.com",
]


async def search_searxng(query: str, max_results: int = 5, language: str = "ru", categories: str = "") -> List[SearchResult]:
    """Search using SearXNG public instances with CONCURRENT requests."""
    import random
    results = []
    instances = SEARXNG_INSTANCES.copy()
    random.shuffle(instances)

    CONCURRENT_LIMIT = 5
    PER_INSTANCE_TIMEOUT = 6.0

    async def _try_instance(instance: str) -> List[SearchResult]:
        try:
            async with httpx.AsyncClient(timeout=PER_INSTANCE_TIMEOUT) as client:
                params = {
                    "q": query,
                    "format": "json",
                    "language": language,
                    "pageno": 1,
                }
                if categories:
                    params["categories"] = categories
                response = await client.get(f"{instance}/search", params=params)
                if response.status_code == 200:
                    data = response.json()
                    instance_results = []
                    for item in data.get("results", [])[:max_results]:
                        instance_results.append(SearchResult(
                            title=item.get("title", ""),
                            url=item.get("url", ""),
                            snippet=item.get("content", ""),
                            source=f"searxng({instance})",
                        ))
                    return instance_results
        except Exception as e:
            logger.debug(f"SearXNG instance {instance} failed: {e}")
        return []

    for batch_start in range(0, len(instances), CONCURRENT_LIMIT):
        batch = instances[batch_start:batch_start + CONCURRENT_LIMIT]
        tasks = [_try_instance(inst) for inst in batch]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in batch_results:
            if isinstance(result, list) and result:
                results.extend(result)

        if results:
            return results[:max_results]

    return results


# ── Google search fallback (scraping) ──────────────────────────────────────────

async def search_google(query: str, max_results: int = 5) -> List[SearchResult]:
    """Search using Google (basic scraping)."""
    results = []
    try:
        async with httpx.AsyncClient(timeout=config.SEARCH_TIMEOUT_SECONDS, follow_redirects=True) as client:
            params = {
                "q": query,
                "hl": "ru",
                "num": max_results,
            }
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "ru-RU,ru;q=0.9",
            }
            response = await client.get("https://www.google.com/search", params=params, headers=headers)
            if response.status_code != 200:
                return results

            html = response.text
            urls = re.findall(r'<a href="/url\?q=(https?://[^&"]+)&', html)
            titles = re.findall(r'<h3[^>]*>(.*?)</h3>', html, re.DOTALL)

            seen = set()
            for i, url in enumerate(urls):
                if url not in seen and "google.com" not in url:
                    seen.add(url)
                    title = _clean_html(titles[i]) if i < len(titles) else ""
                    results.append(SearchResult(title=title, url=url, snippet="", source="google"))
                    if len(results) >= max_results:
                        break
    except Exception as e:
        logger.error(f"Google search error: {e}")
    return results


# ── Spare part search ──────────────────────────────────────────────────────────

PART_SHOPS = [
    "rossko.ru",
    "autopiter.ru",
    "exist.ru",
    "emex.ru",
    "autodoc.ru",
    "zzap.ru",
    "partcost.ru",
    "avtoall.ru",
    "zapravka.ru",
    "ixora-auto.ru",
    "part-review.ru",
    "77Zap.ru",
    "PARTRUN.RU",
]

SHOP_SEARCH_URLS = {
    "rossko": "https://rossko.ru/search?text={article}&subid=masha_bot",
    "autopiter": "https://www.autopiter.ru/search?querystr={article}&subid=masha_bot",
    "exist": "https://exist.ru/Price/?p={article}&subid=masha_bot",
    "emex": "https://emex.ru/products?search={article}&subid=masha_bot",
    "autodoc": "https://autodoc.ru/search?keyword={article}&subid=masha_bot",
    "zzap": "https://zzap.ru/search/?q={article}&subid=masha_bot",
    "avtoall": "https://avtoall.ru/search/?q={article}&subid=masha_bot",
    "ixora": "https://ixora-auto.ru/search/?q={article}&subid=masha_bot",
}


async def search_spare_part(article: str, max_results: int = 8) -> List[SearchResult]:
    """Search for a spare part by article number across auto parts sites."""
    results = []
    article_clean = article.strip().upper()
    query = f"{article} запчасть купить артикул"

    # Step 1: Generate direct shop links
    for shop_name, url_template in SHOP_SEARCH_URLS.items():
        shop_url = url_template.format(article=quote_plus(article_clean))
        results.append(SearchResult(
            title=f"{article_clean} — {shop_name.capitalize()}",
            url=shop_url,
            snippet=f"Поиск артикула {article_clean} на {shop_name.capitalize()}",
            source=f"{shop_name}_direct",
        ))

    # Step 2: DDG search
    try:
        ddg_results = await search_ddg_html(query, max_results=max_results * 2)
        for r in ddg_results:
            if any(shop in r.url.lower() for shop in PART_SHOPS):
                results.append(r)
            elif article_clean in r.title.upper() or article_clean in r.snippet.upper():
                results.append(r)
    except Exception as e:
        logger.error(f"DDG spare part search error: {e}")

    # Step 3: If not enough from DDG, try specific shop site: searches
    if len([r for r in results if r.source.startswith("duckduckgo")]) < 3:
        for shop in ["rossko.ru", "autopiter.ru", "exist.ru"]:
            try:
                shop_query = f"site:{shop} {article}"
                shop_results = await search_ddg_html(shop_query, max_results=2)
                results.extend(shop_results)
            except Exception:
                pass

    return results[:max_results]


async def search_parts_by_vin(vin: str, part_name: str = "", max_results: int = 5) -> List[SearchResult]:
    """Search for parts by VIN code."""
    results = []
    vin_clean = vin.strip().upper()

    query = f"VIN {vin_clean} запчасти подобрать"
    if part_name:
        query = f"VIN {vin_clean} {part_name} запчасть купить"

    # Step 1: Direct shop VIN-search links
    vin_search_urls = [
        (f"Росско — подбор по VIN {vin_clean}",
         f"https://rossko.ru/search?text={quote_plus(vin_clean)}&subid=masha_bot",
         "Поиск запчастей по VIN на Росско"),
        (f"Autopiter — подбор по VIN {vin_clean}",
         f"https://www.autopiter.ru/search?querystr={quote_plus(vin_clean)}&subid=masha_bot",
         "Поиск запчастей по VIN на Autopiter"),
        (f"Exist — подбор по VIN {vin_clean}",
         f"https://exist.ru/Price/?p={quote_plus(vin_clean)}&subid=masha_bot",
         "Поиск запчастей по VIN на Exist"),
        (f"ZZAP — подбор по VIN {vin_clean}",
         f"https://zzap.ru/search/?q={quote_plus(vin_clean)}&subid=masha_bot",
         "Агрегатор запчастей — поиск по VIN"),
        (f"Emex — подбор по VIN {vin_clean}",
         f"https://emex.ru/products?search={quote_plus(vin_clean)}&subid=masha_bot",
         "Поиск запчастей по VIN на Emex"),
    ]

    for title, url, snippet in vin_search_urls:
        results.append(SearchResult(title=title, url=url, snippet=snippet, source="vin_direct"))

    # Step 2: Web search for VIN compatibility
    try:
        ddg_results = await search_ddg_html(query, max_results=max_results)
        results.extend(ddg_results)
    except Exception as e:
        logger.error(f"VIN parts search error: {e}")

    return results[:max_results + 5]


# ── Combined multi-engine search ───────────────────────────────────────────────

async def web_search(query: str, max_results: int = None, region: str = "ru") -> List[SearchResult]:
    """Multi-engine web search with FAST fallback chain."""
    max_results = max_results or config.SEARCH_MAX_RESULTS

    # Strategy 1: Google News RSS
    gnews_results = await search_google_news_rss(query, max_results=max_results)
    if len(gnews_results) >= 2:
        return gnews_results[:max_results]

    # Strategy 2: SearXNG
    searxng_results = await search_searxng(query, max_results=max_results, language=region)
    if searxng_results:
        gnews_results.extend(searxng_results)
    if len(gnews_results) >= 2:
        return gnews_results[:max_results]

    # Strategy 3: DDG HTML
    ddg_results = await search_ddg_html(query, max_results=max_results, region=region)
    if ddg_results:
        gnews_results.extend(ddg_results)
    if len(gnews_results) >= 1:
        return gnews_results[:max_results]

    # Strategy 4: DDG API
    ddg_api = await search_ddg_api(query, region=region)
    if ddg_api:
        gnews_results.append(ddg_api)

    return gnews_results[:max_results]


async def search_google_news_rss(query: str, max_results: int = 5) -> List[SearchResult]:
    """Search Google News RSS feed."""
    import feedparser
    results = []
    try:
        url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=ru&gl=RU&ceid=RU:ru"
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(url)
            if response.status_code == 200:
                feed = feedparser.parse(response.text)
                for entry in feed.entries[:max_results]:
                    title = getattr(entry, "title", "").strip()
                    link = getattr(entry, "link", "").strip()
                    summary = getattr(entry, "summary", "").strip()
                    if title and link:
                        clean_summary = re.sub(r'<[^>]+>', '', summary)[:500]
                        results.append(SearchResult(
                            title=title, url=link, snippet=clean_summary, source="google_news"
                        ))
    except Exception as e:
        logger.debug(f"Google News RSS search failed: {e}")
    return results


async def search_news(query: str, max_results: int = 5) -> List[SearchResult]:
    """Search for news articles."""
    results = await search_google_news_rss(query, max_results=max_results)
    if len(results) >= 2:
        return results[:max_results]

    searxng_results = await search_searxng(query, max_results=max_results, language="ru", categories="news")
    if searxng_results:
        results.extend(searxng_results)
    if len(results) >= 2:
        return results[:max_results]

    if len(results) < 2:
        ddg_results = await search_ddg_html(query, max_results=max_results)
        results.extend(ddg_results)

    return results[:max_results]


def format_search_results(results: List[SearchResult], max_items: int = 5) -> str:
    """Format search results for inclusion in AI context."""
    if not results:
        return "Результаты поиска не найдены."

    lines = []
    for i, r in enumerate(results[:max_items], 1):
        lines.append(f"{i}. {r.title}")
        if r.snippet:
            lines.append(f"   {r.snippet[:200]}")
        lines.append(f"   {r.url}")
    return "\n".join(lines)


# ── Utility ────────────────────────────────────────────────────────────────────

def _clean_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')
    text = re.sub(r'\s+', ' ', text).strip()
    return text
