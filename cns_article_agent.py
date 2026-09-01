#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CNS Article Agent V1 — Retrieval Test

Goal:
- Monitor Nature, Science, and Cell.
- Look back 7 days.
- Retrieve recent journal content from official RSS feeds.
- Print title, date, DOI, URL, and any article-type metadata exposed by the feed.
- Do NOT use OpenAI.
- Do NOT send email.
- Do NOT deduplicate.
- Do NOT yet enforce final article-type filtering.

Target article types for the future production agent:
- Nature  -> Article
- Science -> Research Article
- Cell    -> Article

V1 is diagnostic:
we first inspect what the three official feeds actually expose.
"""

import html
import re
import ssl
import urllib.request
import xml.etree.ElementTree as ET

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime


# ============================================================
# SETTINGS
# ============================================================

SEARCH_DAYS = 7
TIMEOUT = 30
MAX_FEED_ITEMS = 500

UA = (
    "Mozilla/5.0 "
    "(compatible; CNSArticleAgent/1.0; +https://github.com/)"
)


JOURNALS = [
    {
        "name": "Nature",
        "target_type": "Article",
        "feed": "https://www.nature.com/nature.rss",
        "landing": "https://www.nature.com/nature/articles",
    },
    {
        "name": "Science",
        "target_type": "Research Article",
        "feed": (
            "https://www.science.org/action/showFeed"
            "?type=etoc&feed=rss&jc=science"
        ),
        "landing": "https://www.science.org/toc/science/current",
    },
    {
        "name": "Cell",
        "target_type": "Article",
        "feed": (
            "https://www.cell.com/action/showFeed"
            "?type=etoc&feed=rss&jc=cell"
        ),
        "landing": "https://www.cell.com/cell/current",
    },
]


# ============================================================
# BASIC HELPERS
# ============================================================

def clean(value):
    if not value:
        return ""

    value = html.unescape(str(value))
    value = re.sub(r"<!\[CDATA\[|\]\]>", "", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def local_name(tag):
    return tag.split("}", 1)[-1].lower()


def child_text(element, names):
    names = {name.lower() for name in names}

    for child in list(element):
        if local_name(child.tag) in names:
            text = "".join(child.itertext())

            if text.strip():
                return clean(text)

    return ""


def descendant_texts(element, names):
    names = {name.lower() for name in names}
    values = []

    for child in element.iter():
        if local_name(child.tag) in names:
            text = clean("".join(child.itertext()))

            if text and text not in values:
                values.append(text)

    return values


def extract_link(element):
    # RSS-style <link>URL</link>
    for child in list(element):
        if local_name(child.tag) == "link":
            href = child.attrib.get("href")

            if href:
                return href.strip()

            text = "".join(child.itertext()).strip()

            if text.startswith("http"):
                return text

    # Atom-style links
    for child in element.iter():
        if local_name(child.tag) == "link":
            href = child.attrib.get("href")

            if href:
                return href.strip()

    return ""


def normalize_doi(value):
    value = clean(value)

    value = re.sub(
        r"^https?://(dx\.)?doi\.org/",
        "",
        value,
        flags=re.I,
    )

    value = re.sub(
        r"^doi:\s*",
        "",
        value,
        flags=re.I,
    )

    match = re.search(
        r"10\.\d{4,9}/[-._;()/:A-Z0-9]+",
        value,
        flags=re.I,
    )

    if not match:
        return ""

    return match.group(0).rstrip(".,;)")


def find_doi(*values):
    for value in values:
        doi = normalize_doi(value)

        if doi:
            return doi

    return ""


# ============================================================
# DATE HANDLING
# ============================================================

def parse_date(value):
    if not value:
        return None

    value = clean(value)

    # RFC/RSS date
    try:
        dt = parsedate_to_datetime(value)

        if dt:
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)

            return dt.astimezone(timezone.utc)

    except Exception:
        pass

    # ISO date
    try:
        iso_value = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso_value)

        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc)

    except Exception:
        pass

    # YYYY-MM-DD somewhere in text
    match = re.search(
        r"\b(20\d{2})-(\d{2})-(\d{2})\b",
        value,
    )

    if match:
        return datetime(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            tzinfo=timezone.utc,
        )

    return None


def format_date(value):
    dt = parse_date(value)

    if dt:
        return dt.date().isoformat()

    return clean(value) or "Unavailable"


# ============================================================
# HTTP
# ============================================================

def request_bytes(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": (
                "application/rss+xml,"
                "application/atom+xml,"
                "application/xml,"
                "text/xml,"
                "text/html;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    )

    context = ssl.create_default_context()

    with urllib.request.urlopen(
        request,
        timeout=TIMEOUT,
        context=context,
    ) as response:

        return (
            response.read(),
            response.headers.get("Content-Type", ""),
            response.geturl(),
        )


# ============================================================
# FEED PARSING
# ============================================================

def parse_feed(xml_bytes, journal):
    root = ET.fromstring(xml_bytes)

    entries = [
        element
        for element in root.iter()
        if local_name(element.tag) in ("item", "entry")
    ]

    papers = []

    for entry in entries[:MAX_FEED_ITEMS]:

        title = child_text(
            entry,
            ["title"],
        )

        link = extract_link(entry)

        date_text = child_text(
            entry,
            [
                "pubdate",
                "published",
                "updated",
                "date",
                "issued",
            ],
        )

        description = child_text(
            entry,
            [
                "description",
                "summary",
                "abstract",
                "encoded",
            ],
        )

        identifiers = descendant_texts(
            entry,
            [
                "identifier",
                "doi",
            ],
        )

        categories = descendant_texts(
            entry,
            [
                "category",
                "type",
                "section",
                "subject",
            ],
        )

        doi = find_doi(
            " ".join(identifiers),
            link,
            description,
        )

        papers.append(
            {
                "journal": journal["name"],
                "target_type": journal["target_type"],
                "title": title,
                "date_raw": date_text,
                "date": format_date(date_text),
                "doi": doi,
                "url": link,
                "feed_type_metadata": categories,
                "description": description,
            }
        )

    return papers


# ============================================================
# JOURNAL TEST
# ============================================================

def test_journal(journal, cutoff):
    print()
    print("=" * 78)
    print(journal["name"])
    print("=" * 78)

    print("Target article type :", journal["target_type"])
    print("Official feed       :", journal["feed"])
    print("Official landing    :", journal["landing"])
    print()

    try:
        raw, content_type, final_url = request_bytes(
            journal["feed"]
        )

        print("Feed request        : OK")
        print("Final feed URL      :", final_url)
        print("Content-Type        :", content_type)

    except Exception as exc:

        print("Feed request        : FAILED")
        print(
            "Error               :",
            type(exc).__name__,
            str(exc),
        )

        return {
            "journal": journal["name"],
            "status": "FEED_FAILED",
            "feed_items": 0,
            "recent_items": 0,
        }

    try:
        papers = parse_feed(
            raw,
            journal,
        )

    except Exception as exc:

        print("Feed parsing        : FAILED")
        print(
            "Error               :",
            type(exc).__name__,
            str(exc),
        )

        return {
            "journal": journal["name"],
            "status": "PARSE_FAILED",
            "feed_items": 0,
            "recent_items": 0,
        }

    print("Feed parsing        : OK")
    print("Total feed items    :", len(papers))

    recent = []
    undated = []

    for paper in papers:

        dt = parse_date(
            paper["date_raw"]
        )

        if dt is None:
            undated.append(paper)
            continue

        if dt >= cutoff:
            recent.append(paper)

    print("Recent <= 7 days    :", len(recent))
    print("Undated feed items  :", len(undated))

    print()
    print("-" * 78)
    print("RECENT ITEMS")
    print("-" * 78)

    if not recent:
        print("No dated items within the 7-day window.")

    for number, paper in enumerate(
        recent,
        start=1,
    ):

        metadata = paper["feed_type_metadata"]

        print()
        print(f"[{number}]")
        print("Title        :", paper["title"])
        print("Date         :", paper["date"])
        print(
            "DOI          :",
            paper["doi"] or "Unavailable",
        )
        print(
            "Type metadata:",
            " | ".join(metadata)
            if metadata
            else "Unavailable in feed",
        )
        print("Target type  :", paper["target_type"])
        print(
            "URL          :",
            paper["url"] or "Unavailable",
        )

        # IMPORTANT:
        # V1 deliberately does NOT decide KEEP/REJECT yet.
        # First we inspect whether RSS type metadata is reliable.
        print("Decision     : NOT YET FILTERED — V1 diagnostic")

    if undated:
        print()
        print("-" * 78)
        print("UNDATED ITEMS — FIRST 10")
        print("-" * 78)

        for number, paper in enumerate(
            undated[:10],
            start=1,
        ):

            print()
            print(f"[U{number}]")
            print("Title        :", paper["title"])
            print(
                "DOI          :",
                paper["doi"] or "Unavailable",
            )
            print(
                "Type metadata:",
                " | ".join(
                    paper["feed_type_metadata"]
                )
                if paper["feed_type_metadata"]
                else "Unavailable in feed",
            )
            print(
                "URL          :",
                paper["url"] or "Unavailable",
            )

    return {
        "journal": journal["name"],
        "status": "OK",
        "feed_items": len(papers),
        "recent_items": len(recent),
        "undated_items": len(undated),
    }


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 78)
    print("CNS ARTICLE AGENT V1 — RETRIEVAL TEST")
    print("=" * 78)

    print()
    print("Search window : 7 days")
    print("OpenAI API    : OFF")
    print("Email         : OFF")
    print("Dedup         : OFF")
    print("Type filter   : OFF — diagnostic only")

    print()
    print("Target publication types:")
    print("  Nature  -> Article")
    print("  Science -> Research Article")
    print("  Cell    -> Article")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=SEARCH_DAYS)

    print()
    print("Current UTC   :", now.isoformat())
    print("Cutoff UTC    :", cutoff.isoformat())

    diagnostics = []

    for journal in JOURNALS:
        result = test_journal(
            journal,
            cutoff,
        )

        diagnostics.append(result)

    print()
    print("=" * 78)
    print("V1 SUMMARY")
    print("=" * 78)

    for result in diagnostics:

        print(
            f"{result['journal']}: "
            f"{result['status']} | "
            f"feed_items={result.get('feed_items', 0)} | "
            f"recent_7d={result.get('recent_items', 0)} | "
            f"undated={result.get('undated_items', 0)}"
        )

    print()
    print("IMPORTANT:")
    print(
        "V1 does not yet trust RSS article-type metadata."
    )
    print(
        "The next step will use these diagnostics to determine "
        "the correct type-detection strategy for each publisher."
    )

    print()
    print("=" * 78)
    print("CNS ARTICLE AGENT V1 COMPLETED")
    print("=" * 78)


if __name__ == "__main__":
    main()
