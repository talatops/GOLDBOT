from __future__ import annotations

import asyncio
import re
import time
from datetime import timezone
from datetime import datetime
from email.utils import parsedate_to_datetime
from difflib import SequenceMatcher

import feedparser
import httpx

from src.storage.db import Database

NEWS_SOURCES: list[tuple[str, str]] = [
    ("FXStreetNews", "https://www.fxstreet.com/rss/news"),
    ("InvestingCommodities", "https://www.investing.com/rss/news_25.rss"),
    ("InvestingMetals", "https://www.investing.com/rss/news_301.rss"),
]

SOURCE_BASE_SCORES: dict[str, int] = {
    "FXStreetNews": 92,
    "InvestingCommodities": 84,
    "InvestingMetals": 80,
}

REFERENCE_LINKS: list[tuple[str, str]] = [
    ("TradingView GOLD Chart", "https://www.tradingview.com/chart/?symbol=TVC%3AGOLD"),
]


class NewsService:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._source_fail_counts: dict[str, int] = {}
        self._source_muted_until: dict[str, float] = {}
        self._last_source_health: dict[str, dict[str, str | int]] = {}

    async def fetch_and_cache_market_snapshot(
        self, owner_user_id: int | None = None
    ) -> list[dict[str, str | None]]:
        news = await self._fetch_news_items(owner_user_id=owner_user_id)
        if news:
            self._db.add_news_items(news)
        return news

    async def get_top_news(self, owner_user_id: int | None = None, limit: int = 5) -> list[dict[str, str | None]]:
        news = self._db.get_recent_news(limit=limit * 2)
        if not news:
            news = await self.fetch_and_cache_market_snapshot(owner_user_id=owner_user_id)
        ranked = sorted(news, key=lambda x: _to_float(x.get("quality_score")) or 0, reverse=True)
        deduped: list[dict[str, str | None]] = []
        seen_url: set[str] = set()
        seen_title: set[str] = set()
        for item in ranked:
            url = str(item.get("url") or "").strip()
            title = str(item.get("title") or "").strip()
            title_key = _normalize_title(title)
            if url and url in seen_url:
                continue
            if title_key and (title_key in seen_title or any(_is_similar_title(title, d.get("title")) for d in deduped)):
                continue
            if url:
                seen_url.add(url)
            if title_key:
                seen_title.add(title_key)
            deduped.append(item)
            if len(deduped) >= limit:
                break
        return deduped[:limit]

    async def build_market_context(self, owner_user_id: int | None = None) -> str:
        news = await self.get_top_news(owner_user_id=owner_user_id, limit=3)
        price = await self.get_live_price_snapshot()
        price_line = (
            f"Live gold price snapshot: XAUUSD={price['price']} | change={price['change']} | "
            f"change_percent={price['change_percent']} | source={price['source']} | fallback={price['fallback_active']}"
        )
        if not news:
            return f"{price_line}\nNo fresh gold headlines found."
        lines = [price_line, "Top 3 fresh gold headlines (with source confidence):"]
        for idx, item in enumerate(news, start=1):
            title = str(item.get("title") or "Untitled")
            source = str(item.get("source") or "Source")
            url = str(item.get("url") or "")
            conf = str(item.get("source_confidence") or "n/a")
            lines.append(f"{idx}. {title} | source={source} | confidence={conf} | url={url}")
        return "\n".join(lines)

    async def build_headline_context(self, owner_user_id: int | None = None) -> str:
        news = await self.get_top_news(owner_user_id=owner_user_id, limit=6)
        price = await self.get_live_price_snapshot()
        if not news:
            return "No fresh gold headlines available."
        lines = [
            (
                f"Price snapshot: XAUUSD={price['price']} | change={price['change']} | "
                f"change_percent={price['change_percent']} | source={price['source']}"
            ),
            "Candidate headlines ranked by quality:",
        ]
        for idx, item in enumerate(news, start=1):
            lines.append(
                f"{idx}. {item.get('title', 'Untitled')} | source={item.get('source', 'Source')} "
                f"| confidence={item.get('source_confidence', 'n/a')}"
            )
        return "\n".join(lines)

    def build_sources_html(self, news_items: list[dict[str, str | None]]) -> str:
        if not news_items and not REFERENCE_LINKS:
            return "<b>Sources</b>\nNo links available."
        lines = ["<b>Sources</b>"]
        seen_url: set[str] = set()
        seen_title: set[str] = set()
        for item in news_items:
            title = _escape_html(str(item.get("title") or "Untitled"))
            source = _escape_html(str(item.get("source") or "Source"))
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            title_key = _normalize_title(str(item.get("title") or ""))
            if url in seen_url or (title_key and title_key in seen_title):
                continue
            seen_url.add(url)
            if title_key:
                seen_title.add(title_key)
            lines.append(f"- <a href=\"{url}\">{title}</a> ({source})")
        if REFERENCE_LINKS:
            lines.append("")
            lines.append("<b>Market References</b>")
            for name, url in REFERENCE_LINKS:
                lines.append(f"- <a href=\"{url}\">{_escape_html(name)}</a>")
        return "\n".join(lines)

    async def get_live_price_snapshot(self) -> dict[str, str]:
        yahoo_url = "https://query1.finance.yahoo.com/v7/finance/quote?symbols=GC%3DF"
        stooq_url = "https://stooq.com/q/l/?s=gc.f&f=sd2t2ohlcv&h&e=csv"
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                # Primary provider: Yahoo Finance futures quote
                try:
                    response = await client.get(yahoo_url, headers={"User-Agent": "Mozilla/5.0"})
                    response.raise_for_status()
                    data = response.json()
                    quote = ((data.get("quoteResponse") or {}).get("result") or [{}])[0]
                    price = quote.get("regularMarketPrice")
                    change = quote.get("regularMarketChange")
                    change_pct = quote.get("regularMarketChangePercent")
                    if price is not None:
                        return {
                            "price": _fmt_num(price),
                            "change": _fmt_num(change),
                            "change_percent": _fmt_num(change_pct, suffix="%"),
                            "source": "YahooFinance GC=F",
                            "fallback_active": "false",
                        }
                except Exception:
                    pass

                # Fallback provider: Stooq futures CSV quote
                try:
                    response = await client.get(stooq_url, headers={"User-Agent": "Mozilla/5.0"})
                    response.raise_for_status()
                    lines = [line.strip() for line in response.text.splitlines() if line.strip()]
                    if len(lines) >= 2:
                        headers = lines[0].split(",")
                        values = lines[1].split(",")
                        row = {headers[i].lower(): values[i] for i in range(min(len(headers), len(values)))}
                        close_val = row.get("close")
                        open_val = row.get("open")
                        if close_val and close_val.lower() != "n/d":
                            close_num = _to_float(close_val)
                            open_num = _to_float(open_val)
                            if close_num is not None and open_num is not None and open_num != 0:
                                chg = close_num - open_num
                                chg_pct = (chg / open_num) * 100
                            else:
                                chg = None
                                chg_pct = None
                            return {
                                "price": _fmt_num(close_num if close_num is not None else close_val),
                                "change": _fmt_num(chg),
                                "change_percent": _fmt_num(chg_pct, suffix="%"),
                                "source": "Stooq GC.F",
                                "fallback_active": "true",
                            }
                except Exception:
                    pass
        except Exception:
            pass
        return {
            "price": "n/a",
            "change": "n/a",
            "change_percent": "n/a",
            "source": "price_unavailable",
            "fallback_active": "true",
        }

    async def _fetch_news_items(self, owner_user_id: int | None = None) -> list[dict[str, str | None]]:
        custom_sources = self._db.list_custom_sources(owner_user_id=owner_user_id)
        dynamic_sources: list[tuple[str, str]] = list(NEWS_SOURCES)
        for entry in custom_sources:
            source_name = str(entry.get("source_name") or "CustomSource")
            source_url = str(entry.get("source_url") or "").strip()
            if source_url:
                dynamic_sources.append((source_name, source_url))

        now = time.time()
        active_sources = [
            (source, url)
            for source, url in dynamic_sources
            if self._source_muted_until.get(source, 0) <= now
        ]

        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            tasks = [self._fetch_source(client, source, url) for source, url in active_sources]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        merged: list[dict[str, str | None]] = []
        for i, chunk in enumerate(results):
            source_name = active_sources[i][0]
            if isinstance(chunk, Exception):
                self._mark_source_failure(source_name, str(chunk))
                continue
            self._mark_source_success(source_name, len(chunk))
            merged.extend(chunk)

        # Deduplicate by URL and keep latest first.
        seen_url: set[str] = set()
        seen_title: set[str] = set()
        deduped: list[dict[str, str | None]] = []
        filtered = [item for item in merged if _is_fresh(item.get("published_at"))]
        for item in sorted(filtered, key=lambda i: i.get("published_at") or "", reverse=True):
            url = item.get("url") or ""
            raw_title = str(item.get("title") or "")
            title_key = _normalize_title(raw_title)
            if not url or url in seen_url or (title_key and title_key in seen_title):
                continue
            if any(_is_similar_title(raw_title, existing.get("title")) for existing in deduped):
                continue
            seen_url.add(url)
            if title_key:
                seen_title.add(title_key)
            source_name = str(item.get("source") or "Unknown")
            item["source_confidence"] = str(self._source_confidence(source_name))
            item["quality_score"] = str(
                _quality_score(
                    title=raw_title,
                    published_at=item.get("published_at"),
                    source_confidence=self._source_confidence(source_name),
                )
            )
            deduped.append(item)
        return deduped[:20]

    async def _fetch_source(
        self, client: httpx.AsyncClient, source_name: str, url: str
    ) -> list[dict[str, str | None]]:
        response = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        parsed = feedparser.parse(response.text)
        items: list[dict[str, str | None]] = []
        if parsed.entries:
            for entry in parsed.entries[:8]:
                published = _normalize_datetime(
                    getattr(entry, "published", None) or getattr(entry, "updated", None)
                )
                title = (getattr(entry, "title", "") or "").strip()
                link = (getattr(entry, "link", "") or "").strip()
                summary = (getattr(entry, "summary", "") or "").strip()
                if not title or not link:
                    continue
                text_blob = f"{title} {summary}".lower()
                if not any(token in text_blob for token in ("gold", "xau", "bullion", "precious metal")):
                    continue
                items.append(
                    {
                        "source": source_name,
                        "title": title,
                        "url": link,
                        "published_at": published,
                    }
                )
            return items

        # Fallback for non-RSS pages added via /addsite.
        page_title_match = re.search(r"<title[^>]*>(.*?)</title>", response.text, flags=re.I | re.S)
        page_title = (
            re.sub(r"\s+", " ", page_title_match.group(1)).strip() if page_title_match else f"{source_name} homepage"
        )
        page_text = _extract_text(response.text)
        if _contains_gold_terms(page_text):
            items.append(
                {
                    "source": source_name,
                    "title": page_title,
                    "url": url,
                    "published_at": None,
                }
            )
        return items

    def source_health_snapshot(self) -> list[dict[str, str | int]]:
        return [
            {
                "source": source,
                "failures": meta.get("failures", 0),
                "last_status": meta.get("last_status", "unknown"),
                "last_items": meta.get("last_items", 0),
                "muted": "true" if self._source_muted_until.get(source, 0) > time.time() else "false",
            }
            for source, meta in sorted(self._last_source_health.items(), key=lambda kv: kv[0])
        ]

    def _mark_source_success(self, source: str, item_count: int) -> None:
        self._source_fail_counts[source] = 0
        self._last_source_health[source] = {
            "failures": 0,
            "last_status": "ok",
            "last_items": item_count,
        }

    def _mark_source_failure(self, source: str, _error: str) -> None:
        failures = self._source_fail_counts.get(source, 0) + 1
        self._source_fail_counts[source] = failures
        self._last_source_health[source] = {
            "failures": failures,
            "last_status": "failed",
            "last_items": 0,
        }
        if failures >= 3:
            self._source_muted_until[source] = time.time() + 600

    def _source_confidence(self, source: str) -> int:
        base = SOURCE_BASE_SCORES.get(source, 60)
        fail_penalty = min(30, self._source_fail_counts.get(source, 0) * 10)
        return max(30, base - fail_penalty)

    async def build_question_context(self, owner_user_id: int | None, question: str) -> str:
        base_context = await self.build_market_context(owner_user_id=owner_user_id)
        custom_sources = self._db.list_custom_sources(owner_user_id=owner_user_id)
        if not custom_sources:
            if "dubai" in question.lower():
                dubai_hint = await self._fetch_dubai_price_hint()
                if dubai_hint:
                    return f"{base_context}\n\nDubai extraction:\n- {dubai_hint}"
            return base_context

        snippets: list[str] = []
        query_terms = _extract_query_terms(question)
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            for entry in custom_sources[:4]:
                source_name = str(entry.get("source_name") or "CustomSource")
                source_url = str(entry.get("source_url") or "").strip()
                if not source_url:
                    continue
                try:
                    response = await client.get(source_url, headers={"User-Agent": "Mozilla/5.0"})
                    response.raise_for_status()
                    text = _extract_text(response.text)
                    if _looks_like_anti_bot(response.text):
                        snippets.append(
                            f"{source_name} ({source_url}): blocked by anti-bot protection (Cloudflare/JS challenge)."
                        )
                        continue
                    matched_lines = _match_lines(text, query_terms)
                    if matched_lines:
                        snippets.append(f"{source_name} ({source_url}): {' | '.join(matched_lines[:3])}")
                    else:
                        snippets.append(f"{source_name} ({source_url}): page fetched but no direct keyword match.")
                except Exception:
                    snippets.append(f"{source_name} ({source_url}): fetch failed.")

        if "dubai" in question.lower():
            dubai_hint = await self._fetch_dubai_price_hint()
            if dubai_hint:
                snippets.append(f"Dubai fallback: {dubai_hint}")

        if snippets:
            return f"{base_context}\n\nCustom website extraction:\n" + "\n".join(f"- {s}" for s in snippets)
        return base_context

    async def _fetch_dubai_price_hint(self) -> str | None:
        sources = [
            "https://www.goldpricez.com/ae",
            "https://www.livepriceofgold.com/dubai-gold-price.html",
        ]
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            for url in sources:
                try:
                    response = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                    response.raise_for_status()
                    text = _extract_text(response.text)
                    value = _extract_aed_price(text)
                    if value:
                        return f"{value} AED found on {url}"
                except Exception:
                    continue
        return None


def _normalize_datetime(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\"", "&quot;")
    )


def _fmt_num(value: object, suffix: str = "") -> str:
    try:
        return f"{float(value):,.2f}{suffix}"
    except Exception:
        return f"n/a{suffix}" if suffix else "n/a"


def _to_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def _is_similar_title(title_a: str | None, title_b: str | None) -> bool:
    if not title_a or not title_b:
        return False
    a = _normalize_title(title_a)
    b = _normalize_title(title_b)
    if not a or not b:
        return False
    return SequenceMatcher(None, a, b).ratio() >= 0.88


def _extract_text(html: str) -> str:
    cleaned = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
    cleaned = re.sub(r"(?is)<style.*?>.*?</style>", " ", cleaned)
    cleaned = re.sub(r"(?is)<[^>]+>", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _is_fresh(published_at: str | None, max_age_hours: int = 72) -> bool:
    if not published_at:
        return True
    try:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        age_hours = (now - dt.astimezone(timezone.utc)).total_seconds() / 3600
        return age_hours <= max_age_hours
    except Exception:
        return True


def _contains_gold_terms(text: str) -> bool:
    lower = text.lower()
    return any(token in lower for token in ("gold", "xau", "bullion", "precious metal"))


def _extract_query_terms(question: str) -> list[str]:
    tokens = re.findall(r"[a-zA-Z]{3,}", question.lower())
    stop = {"what", "when", "where", "which", "please", "tell", "about", "current", "price"}
    uniq: list[str] = []
    for token in tokens:
        if token in stop:
            continue
        if token not in uniq:
            uniq.append(token)
    return uniq[:6]


def _quality_score(title: str, published_at: str | None, source_confidence: int) -> int:
    score = source_confidence
    lower = title.lower()
    boost_terms = ("gold", "xau", "bullion", "spot", "fed", "rates", "yield")
    for term in boost_terms:
        if term in lower:
            score += 3
    if _is_fresh(published_at, max_age_hours=24):
        score += 10
    elif _is_fresh(published_at, max_age_hours=48):
        score += 4
    return min(100, score)


def _match_lines(text: str, terms: list[str]) -> list[str]:
    if not terms:
        return []
    candidates = re.split(r"[.!?;|]", text)
    matched: list[str] = []
    for c in candidates:
        c2 = c.strip()
        if len(c2) < 20:
            continue
        lower = c2.lower()
        if any(t in lower for t in terms):
            matched.append(c2[:180])
        if len(matched) >= 5:
            break
    return matched


def _extract_aed_price(text: str) -> str | None:
    patterns = [
        r"(\d{2,6}(?:[.,]\d{1,2})?)\s*(?:AED|د\.إ)",
        r"(?:AED|د\.إ)\s*(\d{2,6}(?:[.,]\d{1,2})?)",
    ]
    for pat in patterns:
        matches = re.findall(pat, text, flags=re.I)
        if matches:
            raw = str(matches[0]).replace(",", "")
            try:
                val = float(raw)
                if 50 <= val <= 20000:
                    return f"{val:,.2f}"
            except Exception:
                continue
    return None


def _looks_like_anti_bot(html: str) -> bool:
    lower = html.lower()
    return "just a moment" in lower or "cloudflare" in lower or "captcha" in lower


