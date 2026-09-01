#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CNS Article Agent V2 — Type & Coverage Diagnostic

Targets:
- Nature  -> Article
- Science -> Research Article
- Cell    -> Article

Purpose:
1. Science:
   Test exact RSS metadata filtering for "Research Article".

2. Nature:
   RSS does not expose article type reliably.
   Inspect DOI pattern + official article-page metadata.

3. Cell:
   RSS returned items but none inside the previous 7-day window.
   Print ALL feed items regardless of date and inspect type/date metadata.

No OpenAI.
No email.
No dedup.
"""

import html
import json
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

# Avoid making too many requests to Nature during diagnostics.
MAX_NATURE_PAGE_CHECKS = 30

UA = (
    "Mozilla/5.0 "
    "(compatible; CNSArticleAgent/2.0; +https://github.com/)"
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
    for child in list(element):
        if local_name(child.tag) == "link":
            href = child.attrib.get("href")

            if href:
                return href.strip()

            text = "".join(child.itertext()).strip()

            if text.startswith("http"):
                return text

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

    try:
        dt = parsedate_to_datetime(value)

        if dt:
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)

            return dt.astimezone(timezone.utc)

    except Exception:
        pass

    try:
        iso_value = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso_value)

        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc)

    except Exception:
        pass

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

def request_bytes(url, accept=None):
    if accept is None:
        accept = (
            "application/rss+xml,"
            "application/atom+xml,"
            "application/xml,"
            "text/xml,"
            "text/html;q=0.8"
        )

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": accept,
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

        title = child_text(entry, ["title"])
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


def fetch_feed(journal):
    raw, content_type, final_url = request_bytes(
        journal["feed"]
    )

    papers = parse_feed(
        raw,
        journal,
    )

    return papers, content_type, final_url


# ============================================================
# NATURE PAGE METADATA
# ============================================================

def extract_meta_tag(page, key):
    """
    Try both:
    <meta name="..." content="...">
    <meta property="..." content="...">
    """

    patterns = [
        rf'<meta[^>]+name=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']{re.escape(key)}["\']',
        rf'<meta[^>]+property=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{re.escape(key)}["\']',
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            page,
            flags=re.I,
        )

        if match:
            return clean(match.group(1))

    return ""


def extract_jsonld_types(page):
    results = []

    pattern = re.compile(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        flags=re.I | re.S,
    )

    for match in pattern.finditer(page):
        raw = html.unescape(match.group(1)).strip()

        try:
            data = json.loads(raw)
        except Exception:
            continue

        stack = [data]

        while stack:
            obj = stack.pop()

            if isinstance(obj, dict):

                obj_type = obj.get("@type")

                if isinstance(obj_type, str):
                    if obj_type not in results:
                        results.append(obj_type)

                elif isinstance(obj_type, list):
                    for item in obj_type:
                        if isinstance(item, str):
                            if item not in results:
                                results.append(item)

                stack.extend(obj.values())

            elif isinstance(obj, list):
                stack.extend(obj)

    return results


def detect_nature_page_type(url):
    if not url:
        return {
            "status": "NO_URL",
            "article_type": "",
            "citation_article_type": "",
            "dc_type": "",
            "og_type": "",
            "jsonld_types": [],
            "page_signals": [],
        }

    try:
        raw, content_type, final_url = request_bytes(
            url,
            accept="text/html,application/xhtml+xml",
        )

        page = raw.decode(
            "utf-8",
            errors="replace",
        )

    except Exception as exc:

        return {
            "status": (
                f"FAILED: {type(exc).__name__}: {exc}"
            ),
            "article_type": "",
            "citation_article_type": "",
            "dc_type": "",
            "og_type": "",
            "jsonld_types": [],
            "page_signals": [],
        }

    citation_article_type = extract_meta_tag(
        page,
        "citation_article_type",
    )

    dc_type = (
        extract_meta_tag(page, "dc.type")
        or extract_meta_tag(page, "DC.type")
        or extract_meta_tag(page, "DC.Type")
    )

    og_type = extract_meta_tag(
        page,
        "og:type",
    )

    jsonld_types = extract_jsonld_types(
        page
    )

    page_signals = []

    # Diagnostic only:
    # search visible/HTML source for likely Nature content labels.
    labels = [
        "Article",
        "Review Article",
        "News",
        "News Feature",
        "Editorial",
        "Comment",
        "World View",
        "Career Column",
        "Nature Briefing",
    ]

    for label in labels:

        pattern = (
            r">\s*"
            + re.escape(label)
            + r"\s*<"
        )

        if re.search(
            pattern,
            page,
            flags=re.I,
        ):
            page_signals.append(label)

    article_type = (
        citation_article_type
        or dc_type
        or ""
    )

    return {
        "status": "OK",
        "final_url": final_url,
        "content_type": content_type,
        "article_type": article_type,
        "citation_article_type": citation_article_type,
        "dc_type": dc_type,
        "og_type": og_type,
        "jsonld_types": jsonld_types,
        "page_signals": page_signals,
    }


# ============================================================
# NATURE DIAGNOSTIC
# ============================================================

def diagnose_nature(journal, cutoff):
    print()
    print("=" * 78)
    print("NATURE — ARTICLE TYPE DIAGNOSTIC")
    print("=" * 78)

    try:
        papers, content_type, final_url = fetch_feed(
            journal
        )

    except Exception as exc:
        print(
            "Nature feed FAILED:",
            type(exc).__name__,
            str(exc),
        )
        return

    recent = []

    for paper in papers:
        dt = parse_date(
            paper["date_raw"]
        )

        if dt and dt >= cutoff:
            recent.append(paper)

    print("Feed URL        :", final_url)
    print("Content-Type    :", content_type)
    print("Feed items      :", len(papers))
    print("Recent 7d       :", len(recent))

    research_like = [
        paper
        for paper in recent
        if paper["doi"].lower().startswith(
            "10.1038/s41586-"
        )
    ]

    d41586 = [
        paper
        for paper in recent
        if paper["doi"].lower().startswith(
            "10.1038/d41586-"
        )
    ]

    other = [
        paper
        for paper in recent
        if paper not in research_like
        and paper not in d41586
    ]

    print()
    print(
        "s41586 DOI candidates :",
        len(research_like),
    )
    print(
        "d41586 DOI candidates :",
        len(d41586),
    )
    print(
        "Other DOI candidates  :",
        len(other),
    )

    print()
    print("-" * 78)
    print(
        "CHECKING NATURE s41586 CANDIDATES AGAINST OFFICIAL PAGES"
    )
    print("-" * 78)

    checked = 0

    for number, paper in enumerate(
        research_like,
        start=1,
    ):

        if checked >= MAX_NATURE_PAGE_CHECKS:
            print()
            print(
                "Nature page-check limit reached:",
                MAX_NATURE_PAGE_CHECKS,
            )
            break

        checked += 1

        metadata = detect_nature_page_type(
            paper["url"]
        )

        print()
        print(f"[N{number}]")
        print("Title                  :", paper["title"])
        print("Date                   :", paper["date"])
        print(
            "DOI                    :",
            paper["doi"] or "Unavailable",
        )
        print("URL                    :", paper["url"])
        print(
            "Page request           :",
            metadata["status"],
        )
        print(
            "citation_article_type  :",
            metadata.get(
                "citation_article_type"
            )
            or "Unavailable",
        )
        print(
            "dc.type                :",
            metadata.get("dc_type")
            or "Unavailable",
        )
        print(
            "og:type                :",
            metadata.get("og_type")
            or "Unavailable",
        )
        print(
            "JSON-LD @type          :",
            " | ".join(
                metadata.get(
                    "jsonld_types",
                    [],
                )
            )
            or "Unavailable",
        )
        print(
            "Page label signals     :",
            " | ".join(
                metadata.get(
                    "page_signals",
                    [],
                )
            )
            or "Unavailable",
        )

        detected = metadata.get(
            "article_type",
            "",
        )

        if detected:
            decision = (
                "KEEP"
                if detected.strip().lower()
                == "article"
                else "REVIEW"
            )
        else:
            decision = "UNRESOLVED"

        print(
            "Diagnostic decision    :",
            decision,
        )

    print()
    print("-" * 78)
    print("NATURE d41586 SAMPLE — FIRST 10")
    print("-" * 78)

    for number, paper in enumerate(
        d41586[:10],
        start=1,
    ):
        print()
        print(f"[D{number}]")
        print("Title :", paper["title"])
        print("Date  :", paper["date"])
        print("DOI   :", paper["doi"])
        print("URL   :", paper["url"])


# ============================================================
# SCIENCE DIAGNOSTIC
# ============================================================

def diagnose_science(journal, cutoff):
    print()
    print("=" * 78)
    print("SCIENCE — EXACT RSS TYPE FILTER")
    print("=" * 78)

    try:
        papers, content_type, final_url = fetch_feed(
            journal
        )

    except Exception as exc:
        print(
            "Science feed FAILED:",
            type(exc).__name__,
            str(exc),
        )
        return

    recent = []

    for paper in papers:
        dt = parse_date(
            paper["date_raw"]
        )

        if dt and dt >= cutoff:
            recent.append(paper)

    keep = []
    reject = []

    for paper in recent:

        types = [
            value.strip()
            for value
            in paper["feed_type_metadata"]
        ]

        if "Research Article" in types:
            keep.append(paper)
        else:
            reject.append(paper)

    print("Feed URL        :", final_url)
    print("Content-Type    :", content_type)
    print("Feed items      :", len(papers))
    print("Recent 7d       :", len(recent))
    print("KEEP            :", len(keep))
    print("REJECT          :", len(reject))

    print()
    print("-" * 78)
    print("SCIENCE KEEP — RESEARCH ARTICLE")
    print("-" * 78)

    for number, paper in enumerate(
        keep,
        start=1,
    ):

        print()
        print(f"[S{number}]")
        print("Title    :", paper["title"])
        print("Date     :", paper["date"])
        print("DOI      :", paper["doi"])
        print(
            "RSS type :",
            " | ".join(
                paper["feed_type_metadata"]
            ),
        )
        print("Decision : KEEP")

    print()
    print("-" * 78)
    print("SCIENCE REJECT TYPES")
    print("-" * 78)

    reject_types = {}

    for paper in reject:
        label = (
            " | ".join(
                paper["feed_type_metadata"]
            )
            or "Unavailable"
        )

        reject_types[label] = (
            reject_types.get(label, 0)
            + 1
        )

    for label, count in sorted(
        reject_types.items()
    ):
        print(f"{label}: {count}")


# ============================================================
# CELL DIAGNOSTIC
# ============================================================

def diagnose_cell(journal, cutoff):
    print()
    print("=" * 78)
    print("CELL — FULL FEED COVERAGE DIAGNOSTIC")
    print("=" * 78)

    try:
        papers, content_type, final_url = fetch_feed(
            journal
        )

    except Exception as exc:
        print(
            "Cell feed FAILED:",
            type(exc).__name__,
            str(exc),
        )
        return

    print("Feed URL        :", final_url)
    print("Content-Type    :", content_type)
    print("Total feed items:", len(papers))

    dated = []

    for paper in papers:
        dt = parse_date(
            paper["date_raw"]
        )

        if dt:
            dated.append(
                (
                    dt,
                    paper,
                )
            )

    dated.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    if dated:
        newest = dated[0][0]
        oldest = dated[-1][0]

        print(
            "Newest feed date:",
            newest.isoformat(),
        )
        print(
            "Oldest feed date:",
            oldest.isoformat(),
        )

    recent = [
        paper
        for dt, paper in dated
        if dt >= cutoff
    ]

    print(
        "Recent <= 7 days:",
        len(recent),
    )

    print()
    print("-" * 78)
    print("ALL CELL FEED ITEMS")
    print("-" * 78)

    for number, paper in enumerate(
        papers,
        start=1,
    ):

        dt = parse_date(
            paper["date_raw"]
        )

        in_window = (
            bool(dt and dt >= cutoff)
        )

        print()
        print(f"[C{number}]")
        print("Title        :", paper["title"])
        print("Raw date     :", paper["date_raw"])
        print("Parsed date  :", paper["date"])
        print(
            "Within 7 days:",
            "YES" if in_window else "NO",
        )
        print(
            "DOI          :",
            paper["doi"]
            or "Unavailable",
        )
        print(
            "Type metadata:",
            " | ".join(
                paper["feed_type_metadata"]
            )
            if paper["feed_type_metadata"]
            else "Unavailable",
        )
        print(
            "URL          :",
            paper["url"]
            or "Unavailable",
        )

    print()
    print("-" * 78)
    print("CELL TYPE SUMMARY")
    print("-" * 78)

    type_counts = {}

    for paper in papers:

        label = (
            " | ".join(
                paper["feed_type_metadata"]
            )
            or "Unavailable"
        )

        type_counts[label] = (
            type_counts.get(label, 0)
            + 1
        )

    for label, count in sorted(
        type_counts.items()
    ):
        print(f"{label}: {count}")

    print()
    print("Official current issue page:")
    print(journal["landing"])


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 78)
    print(
        "CNS ARTICLE AGENT V2 — TYPE & COVERAGE DIAGNOSTIC"
    )
    print("=" * 78)

    print()
    print("OpenAI API : OFF")
    print("Email      : OFF")
    print("Dedup      : OFF")

    print()
    print("Target publication types:")
    print("  Nature  -> Article")
    print("  Science -> Research Article")
    print("  Cell    -> Article")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(
        days=SEARCH_DAYS
    )

    print()
    print(
        "Current UTC:",
        now.isoformat(),
    )
    print(
        "Cutoff UTC :",
        cutoff.isoformat(),
    )

    journal_map = {
        journal["name"]: journal
        for journal in JOURNALS
    }

    diagnose_nature(
        journal_map["Nature"],
        cutoff,
    )

    diagnose_science(
        journal_map["Science"],
        cutoff,
    )

    diagnose_cell(
        journal_map["Cell"],
        cutoff,
    )

    print()
    print("=" * 78)
    print("V2 FINISHED")
    print("=" * 78)

    print()
    print(
        "Next step:"
    )
    print(
        "Use this run to choose the final reliable "
        "Article-type rule for Nature and Cell."
    )


if __name__ == "__main__":
    main()
