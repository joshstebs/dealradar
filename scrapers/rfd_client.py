"""RSS discovery plus conservative HTML enrichment. Missing votes stay unknown."""
from __future__ import annotations

import re
import asyncio
import xml.etree.ElementTree as ElementTree
from urllib.parse import urljoin, urlsplit

import httpx
from scrapers.html_utils import Document, plain_text
from scrapers.http_utils import fetch

RSS_URL = "https://forums.redflagdeals.com/rss/feed/active/"


def safe_rfd_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return (parts.scheme == "https" and parts.hostname == "forums.redflagdeals.com"
                and parts.port in (None, 443) and not parts.username and not parts.password)
    except ValueError:
        return False


def parse_feed(xml: str) -> list[dict]:
    # ElementTree doesn't resolve external entities. Reject declarations too,
    # preventing internal entity expansion and oversized XML from feeds.
    if len(xml) > 2_000_000 or re.search(r"<!\s*(DOCTYPE|ENTITY)\b", xml, re.I):
        raise ValueError("Unsafe or oversized RSS")
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise ValueError("RFD returned invalid XML") from exc
    if root.tag != "rss":
        raise ValueError("RFD response is not RSS")
    posts = []
    for node in root.findall(".//item"):
        url = (node.findtext("link") or "").strip()
        title = plain_text(node.findtext("title") or "")
        if not title or not safe_rfd_url(url):
            continue
        tag = re.match(r"^\[([^\]]+)\]", title)
        posts.append({"title": title, "retailer_tag": tag.group(1) if tag else None,
                      "thread_url": url, "published_at": node.findtext("pubDate"),
                      "upvotes": None, "downvotes": None, "score": None,
                      "deal_url": None, "price_verified": False, "is_hot_deal": False})
    return list({p["thread_url"]: p for p in posts}.values())


def enrich_post(post: dict, html: str) -> dict:
    nodes = list(Document(html).root.descendants())
    # Example selectors: maintain against saved fixtures when markup changes.
    score = next((n for n in nodes if "data-vote-score" in n.attrs or n.has_class("thread-vote-score")), None)
    text = (score.attrs.get("data-vote-score") or score.text()) if score else ""
    parsed = int(text) if re.fullmatch(r"[+-]?\d+", text) else None
    first_post = next((n for n in nodes if n.has_class("postbody", "post-content")), None)
    deal_url = None
    if first_post:
        for link in first_post.descendants():
            if link.tag != "a" or "href" not in link.attrs:
                continue
            candidate = urljoin(post["thread_url"], link.attrs["href"])
            parts = urlsplit(candidate)
            if (parts.scheme == "https" and parts.hostname
                    and not parts.hostname.endswith("redflagdeals.com")
                    and not parts.username and not parts.password):
                # A candidate, not a verified checkout URL; verifier applies
                # retailer allowlist before making outbound server requests.
                deal_url = candidate
                break
    # Category evidence must come from a breadcrumb, not the site's global nav.
    breadcrumbs = [n for n in nodes if n.has_class("breadcrumb", "breadcrumbs")
                   or n.attrs.get("itemtype", "").endswith("BreadcrumbList")
                   or n.attrs.get("aria-label", "").casefold() == "breadcrumb"]
    is_hot = any("hot deals" in n.text().casefold() for n in breadcrumbs)
    return {**post, "score": parsed, "deal_url": deal_url, "is_hot_deal": is_hot,
            "category_known": bool(breadcrumbs)}


class RFDClient:
    def __init__(self, http: httpx.AsyncClient, feed_url: str = RSS_URL):
        if not safe_rfd_url(feed_url):
            raise ValueError("Only the RFD HTTPS host is supported")
        self.http, self.feed_url = http, feed_url

    async def trending(self, min_score: int = 20, max_threads: int = 10) -> dict:
        if not 1 <= max_threads <= 30:
            raise ValueError("max_threads must be 1–30")
        response = await fetch(self.http, self.feed_url)
        posts = parse_feed(response.text)
        deals, unknown, errors = [], [], []
        unclassified = 0
        # Sequential and bounded; production worker adds host rate limiter,
        # cached thread bodies and Retry-After handling.
        for post in posts[:max_threads]:
            try:
                page = await fetch(self.http, post["thread_url"])
                enriched = enrich_post(post, page.text)
                if not enriched["category_known"]:
                    unclassified += 1
                if not enriched["is_hot_deal"]:
                    continue
                if enriched["score"] is None:
                    unknown.append(enriched)
                elif enriched["score"] > min_score:
                    deals.append(enriched)
            except (httpx.HTTPError, ValueError) as exc:
                errors.append({"url": post["thread_url"], "error": type(exc).__name__})
            finally:
                await asyncio.sleep(0.25)
        return {"deals": sorted(deals, key=lambda p: p["score"], reverse=True),
                "unscored": unknown, "errors": errors,
                "unclassified_count": unclassified,
                "discovered": len(posts), "inspected": min(len(posts), max_threads)}
