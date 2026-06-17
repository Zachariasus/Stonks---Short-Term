"""
news/source_bias.py
===================
Map a news story's source (URL domain, falling back to outlet name) to its
political-bias tag + reliability, using the curated outlet list in
sources_bias.json.

WHY DOMAIN-FIRST
    The bias guide says to tag by the ORIGINAL source's domain, not the outlet
    name a feed reports (aggregators like Yahoo Finance reprint wires). So we
    match the article URL's host against each outlet's `domains` list first, and
    only fall back to the reported source name if the domain isn't recognized.

This is read-time enrichment — nothing is stored on the article row, so the bias
map can be edited (or re-rated) without re-fetching or migrating anything.
"""

import json
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

_DATA_PATH = Path(__file__).resolve().parent / "sources_bias.json"


@lru_cache(maxsize=1)
def _load():
    """Load the outlet list once; build domain→outlet and name→outlet indexes."""
    with open(_DATA_PATH, encoding="utf-8") as f:
        data = json.load(f)
    outlets = data.get("outlets", [])
    by_domain = {}
    by_name = {}
    for outlet in outlets:
        for dom in outlet.get("domains", []):
            by_domain[dom.lower()] = outlet
        by_name[outlet["name"].lower()] = outlet
    return outlets, by_domain, by_name


def _host(url):
    """Bare hostname from a URL, without a leading www. ('' if unparseable)."""
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except (ValueError, AttributeError):
        return ""


def lookup(url=None, source_name=None) -> dict:
    """Return {outlet, bias, reliability, category} for a story.

    Matches by URL domain (exact or sub-domain), then by reported source name.
    Unrecognized sources return bias 'Unknown' so the UI can still show the name.
    """
    _outlets, by_domain, by_name = _load()

    host = _host(url) if url else ""
    outlet = None
    if host:
        outlet = by_domain.get(host)
        if outlet is None:  # sub-domain (e.g. markets.businessinsider.com → businessinsider.com)
            for dom, o in by_domain.items():
                if host == dom or host.endswith("." + dom):
                    outlet = o
                    break
    if outlet is None and source_name:
        outlet = by_name.get(source_name.strip().lower())

    if outlet is None:
        return {
            "outlet": source_name or host or "Unknown",
            "bias": "Unknown",
            "reliability": None,
            "category": None,
            "homepage": None,
        }
    domains = outlet.get("domains") or []
    return {
        "outlet": outlet["name"],
        "bias": outlet["bias"],
        "reliability": outlet.get("reliability"),
        "category": outlet.get("category"),
        # Outlet homepage from its primary domain (clicking the name opens this).
        "homepage": f"https://{domains[0]}" if domains else None,
    }


def tracked_domains() -> list:
    """All curated domains — usable to restrict the NewsAPI fetch to these outlets."""
    _outlets, by_domain, _by_name = _load()
    return sorted(by_domain.keys())


if __name__ == "__main__":
    for u, n in [
        ("https://www.reuters.com/markets/x", None),
        ("https://www.cnbc.com/2026/06/17/nvda.html", None),
        ("https://finance.yahoo.com/news/x", "Bloomberg"),
        ("https://example-unknown.com/x", "Some Blog"),
    ]:
        print(f"{u or n} -> {lookup(u, n)}")
    print(f"\n{len(tracked_domains())} tracked domains")
