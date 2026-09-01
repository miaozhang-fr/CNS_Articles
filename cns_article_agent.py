#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CNS Article Agent V3 — Abstract Retrieval Test

Targets:
- Nature  -> Article
- Science -> Research Article
- Cell    -> Article

Purpose:
1. Apply the article-type rules established in V2.
2. Retrieve up to 10 target papers per journal.
3. Try to obtain a substantive abstract/scientific summary.
4. Report abstract source, length, and text.

No OpenAI.
No email.
No persistent dedup.
"""

import html
import json
import re
import ssl
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


# ============================================================
# SETTINGS
# ============================================================

TIMEOUT = 30
MAX_FEED_ITEMS = 500
MAX_TARGETS_PER_JOURNAL = 10

UA = (
    "Mozilla/5.0 "
    "(compatible; CNSArticleAgent/3.0; +https://github.com/)"
)

JOURNALS = [
    {
        "name": "Nature",
        "target_type": "Article",
        "feed": "https://www.nature.com/nature.rss",
    },
    {
        "name": "Science",
        "target_type": "Research Article",
        "feed": (
            "https://www.science.org/action/showFeed"
            "?type=etoc&feed=rss&jc=science"
        ),
    },
    {
        "name": "Cell",
        "target_type": "Article",
        "feed": (
            "https://www.cell.com/action/showFeed"
            "?type=etoc&feed=rss&jc=cell"
        ),
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
    names = {x.lower() for x in names}

    for child in list(element):
        if local_name(child.tag) in names:
            text = "".join(child.itertext())

            if text.strip():
                return clean(text)

    return ""


def descendant_texts(element, names):
    names = {x.lower() for x in names}
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
        dt = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

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


def request_text(url):
    raw, content_type, final_url = request_bytes(
        url,
        accept="text/html,application/xhtml+xml",
    )

    text = raw.decode(
        "utf-8",
        errors="replace",
    )

    return text, content_type, final_url


# ============================================================
# FEED
# ============================================================

def parse_feed(xml_bytes, journal):
    root = ET.fromstring(xml_bytes)

    entries = [
        x
        for x in root.iter()
        if local_name(x.tag) in ("item", "entry")
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
            ["identifier", "doi"],
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
                "feed_types": categories,
                "feed_description": description,
            }
        )

    return papers


def fetch_feed(journal):
    raw, _, _ = request_bytes(
        journal["feed"]
    )

    return parse_feed(
        raw,
        journal,
    )


# ============================================================
# HTML META
# ============================================================

def extract_meta_tag(page, key):
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
            return clean(
                match.group(1)
            )

    return ""


def extract_jsonld(page):
    blocks = []

    pattern = re.compile(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        flags=re.I | re.S,
    )

    for match in pattern.finditer(page):

        raw = html.unescape(
            match.group(1)
        ).strip()

        try:
            blocks.append(
                json.loads(raw)
            )
        except Exception:
            continue

    return blocks


def walk_json(value):
    if isinstance(value, dict):

        yield value

        for child in value.values():
            yield from walk_json(child)

    elif isinstance(value, list):

        for child in value:
            yield from walk_json(child)


# ============================================================
# ABSTRACT EXTRACTION
# ============================================================

def abstract_from_jsonld(page):
    candidates = []

    for block in extract_jsonld(page):

        for obj in walk_json(block):

            value = obj.get("abstract")

            if isinstance(value, str):
                text = clean(value)

                if len(text) >= 100:
                    candidates.append(text)

            value = obj.get("description")

            if isinstance(value, str):
                text = clean(value)

                if len(text) >= 150:
                    candidates.append(text)

    if not candidates:
        return ""

    candidates.sort(
        key=len,
        reverse=True,
    )

    return candidates[0]


def abstract_from_meta(page):
    keys = [
        "citation_abstract",
        "dc.description",
        "DC.description",
        "description",
        "og:description",
    ]

    candidates = []

    for key in keys:
        text = extract_meta_tag(
            page,
            key,
        )

        if len(text) >= 150:
            candidates.append(
                (
                    key,
                    text,
                )
            )

    if not candidates:
        return "", ""

    candidates.sort(
        key=lambda x: len(x[1]),
        reverse=True,
    )

    return candidates[0][1], (
        "HTML_META:"
        + candidates[0][0]
    )


def abstract_from_html_patterns(page):
    """
    Diagnostic fallback.

    Tries common abstract containers used by publisher pages.
    """

    patterns = [
        r'<div[^>]+id=["\']Abs1-content["\'][^>]*>(.*?)</div>',
        r'<div[^>]+class=["\'][^"\']*abstract[^"\']*["\'][^>]*>(.*?)</div>',
        r'<section[^>]+class=["\'][^"\']*abstract[^"\']*["\'][^>]*>(.*?)</section>',
    ]

    candidates = []

    for pattern in patterns:

        for match in re.finditer(
            pattern,
            page,
            flags=re.I | re.S,
        ):

            text = clean(
                match.group(1)
            )

            if len(text) >= 150:
                candidates.append(
                    text
                )

    if not candidates:
        return ""

    candidates.sort(
        key=len,
        reverse=True,
    )

    return candidates[0]


def retrieve_page_abstract(url):
    if not url:
        return {
            "status": "NO_URL",
            "abstract": "",
            "source": "",
            "final_url": "",
        }

    try:
        page, _, final_url = request_text(
            url
        )

    except Exception as exc:
        return {
            "status": (
                f"FAILED: "
                f"{type(exc).__name__}: {exc}"
            ),
            "abstract": "",
            "source": "",
            "final_url": "",
        }

    # 1. citation/meta abstract
    abstract, source = abstract_from_meta(
        page
    )

    if abstract:
        return {
            "status": "OK",
            "abstract": abstract,
            "source": source,
            "final_url": final_url,
        }

    # 2. JSON-LD
    abstract = abstract_from_jsonld(
        page
    )

    if abstract:
        return {
            "status": "OK",
            "abstract": abstract,
            "source": "JSON_LD",
            "final_url": final_url,
        }

    # 3. HTML fallback
    abstract = abstract_from_html_patterns(
        page
    )

    if abstract:
        return {
            "status": "OK",
            "abstract": abstract,
            "source": "HTML_ABSTRACT_BLOCK",
            "final_url": final_url,
        }

    return {
        "status": "NO_ABSTRACT_FOUND",
        "abstract": "",
        "source": "",
        "final_url": final_url,
    }


# ============================================================
# PUBMED FALLBACK
# ============================================================

def pubmed_abstract_by_doi(doi):
    """
    Uses NCBI E-utilities without an API key.

    DOI -> PubMed ID -> abstract.
    """

    if not doi:
        return ""

    query = urllib.parse.quote(
        f'"{doi}"[AID]'
    )

    search_url = (
        "https://eutils.ncbi.nlm.nih.gov/"
        "entrez/eutils/esearch.fcgi"
        "?db=pubmed"
        f"&term={query}"
        "&retmode=json"
    )

    try:
        raw, _, _ = request_bytes(
            search_url,
            accept="application/json",
        )

        data = json.loads(
            raw.decode(
                "utf-8",
                errors="replace",
            )
        )

        ids = (
            data.get(
                "esearchresult",
                {}
            )
            .get(
                "idlist",
                []
            )
        )

        if not ids:
            return ""

        pmid = ids[0]

        fetch_url = (
            "https://eutils.ncbi.nlm.nih.gov/"
            "entrez/eutils/efetch.fcgi"
            "?db=pubmed"
            f"&id={pmid}"
            "&retmode=xml"
        )

        xml_bytes, _, _ = request_bytes(
            fetch_url,
            accept="application/xml,text/xml",
        )

        root = ET.fromstring(
            xml_bytes
        )

        parts = []

        for element in root.iter():

            if local_name(
                element.tag
            ) != "abstracttext":
                continue

            text = clean(
                "".join(
                    element.itertext()
                )
            )

            label = element.attrib.get(
                "Label",
                "",
            ).strip()

            if label and text:
                parts.append(
                    f"{label}: {text}"
                )

            elif text:
                parts.append(text)

        return clean(
            " ".join(parts)
        )

    except Exception:
        return ""


# ============================================================
# ARTICLE TYPE FILTER
# ============================================================

def nature_is_article(paper):
    """
    V2 established:
    - s41586 is a useful candidate restriction
    - final decision comes from official Nature page
      citation_article_type == Article
    """

    if not paper["doi"].lower().startswith(
        "10.1038/s41586-"
    ):
        return False, "", None

    try:
        page, _, final_url = request_text(
            paper["url"]
        )

    except Exception as exc:
        return (
            False,
            (
                f"PAGE_FAILED:"
                f"{type(exc).__name__}"
            ),
            None,
        )

    article_type = extract_meta_tag(
        page,
        "citation_article_type",
    )

    keep = (
        article_type.strip().lower()
        == "article"
    )

    return (
        keep,
        article_type
        or "Unavailable",
        {
            "page": page,
            "final_url": final_url,
        },
    )


def science_is_article(paper):
    return (
        "Research Article"
        in paper["feed_types"]
    )


def cell_is_article(paper):
    return (
        "Article"
        in paper["feed_types"]
    )


# ============================================================
# ABSTRACT FOR ONE PAPER
# ============================================================

def get_abstract(paper, cached_page=None):
    """
    Priority:
    1. Official article page
    2. PubMed DOI lookup
    3. RSS description, only if substantive
    """

    # Reuse Nature page already downloaded
    if cached_page:

        page = cached_page["page"]

        abstract, source = abstract_from_meta(
            page
        )

        if abstract:
            return (
                abstract,
                "OFFICIAL_PAGE/"
                + source,
            )

        abstract = abstract_from_jsonld(
            page
        )

        if abstract:
            return (
                abstract,
                "OFFICIAL_PAGE/JSON_LD",
            )

        abstract = abstract_from_html_patterns(
            page
        )

        if abstract:
            return (
                abstract,
                "OFFICIAL_PAGE/"
                "HTML_ABSTRACT_BLOCK",
            )

    else:
        result = retrieve_page_abstract(
            paper["url"]
        )

        if result["abstract"]:
            return (
                result["abstract"],
                "OFFICIAL_PAGE/"
                + result["source"],
            )

    # PubMed fallback
    abstract = pubmed_abstract_by_doi(
        paper["doi"]
    )

    if len(abstract) >= 100:
        return (
            abstract,
            "PUBMED",
        )

    # RSS fallback
    rss_text = clean(
        paper["feed_description"]
    )

    if len(rss_text) >= 150:
        return (
            rss_text,
            "RSS_DESCRIPTION",
        )

    return "", "UNAVAILABLE"


# ============================================================
# OUTPUT
# ============================================================

def print_paper(number, paper, article_type, abstract, source):
    print()
    print("-" * 78)
    print(
        f"[{paper['journal']} {number}]"
    )
    print("-" * 78)

    print(
        "Title         :",
        paper["title"],
    )

    print(
        "Date          :",
        paper["date"],
    )

    print(
        "DOI           :",
        paper["doi"]
        or "Unavailable",
    )

    print(
        "Article type  :",
        article_type,
    )

    print(
        "URL           :",
        paper["url"]
        or "Unavailable",
    )

    print(
        "Abstract source:",
        source,
    )

    print(
        "Abstract chars :",
        len(abstract),
    )

    print()
    print("ABSTRACT")
    print()

    if abstract:
        print(abstract)
    else:
        print(
            "[NO SUBSTANTIVE ABSTRACT FOUND]"
        )


# ============================================================
# JOURNAL PROCESSORS
# ============================================================

def process_nature(journal):
    print()
    print("=" * 78)
    print("NATURE — TARGET ARTICLES + ABSTRACTS")
    print("=" * 78)

    papers = fetch_feed(
        journal
    )

    targets = []

    for paper in papers:

        if len(targets) >= MAX_TARGETS_PER_JOURNAL:
            break

        keep, article_type, cached_page = (
            nature_is_article(
                paper
            )
        )

        if not keep:
            continue

        abstract, source = get_abstract(
            paper,
            cached_page=cached_page,
        )

        targets.append(
            (
                paper,
                article_type,
                abstract,
                source,
            )
        )

    for number, item in enumerate(
        targets,
        start=1,
    ):
        print_paper(
            number,
            *item,
        )

    return targets


def process_science(journal):
    print()
    print("=" * 78)
    print("SCIENCE — TARGET ARTICLES + ABSTRACTS")
    print("=" * 78)

    papers = fetch_feed(
        journal
    )

    targets = []

    for paper in papers:

        if len(targets) >= MAX_TARGETS_PER_JOURNAL:
            break

        if not science_is_article(
            paper
        ):
            continue

        abstract, source = get_abstract(
            paper
        )

        targets.append(
            (
                paper,
                "Research Article",
                abstract,
                source,
            )
        )

    for number, item in enumerate(
        targets,
        start=1,
    ):
        print_paper(
            number,
            *item,
        )

    return targets


def process_cell(journal):
    print()
    print("=" * 78)
    print("CELL — TARGET ARTICLES + ABSTRACTS")
    print("=" * 78)

    papers = fetch_feed(
        journal
    )

    targets = []

    # Cell RSS ordering is not necessarily publication-date ordering.
    # Filter all Articles first, then sort by parsed date.

    article_papers = [
        paper
        for paper in papers
        if cell_is_article(
            paper
        )
    ]

    article_papers.sort(
        key=lambda paper: (
            parse_date(
                paper["date_raw"]
            )
            or datetime.min.replace(
                tzinfo=timezone.utc
            )
        ),
        reverse=True,
    )

    for paper in article_papers[
        :MAX_TARGETS_PER_JOURNAL
    ]:

        abstract, source = get_abstract(
            paper
        )

        targets.append(
            (
                paper,
                "Article",
                abstract,
                source,
            )
        )

    for number, item in enumerate(
        targets,
        start=1,
    ):
        print_paper(
            number,
            *item,
        )

    return targets


# ============================================================
# SUMMARY
# ============================================================

def summarize(name, targets):
    total = len(targets)

    with_abstract = sum(
        1
        for _, _, abstract, _
        in targets
        if len(abstract) >= 100
    )

    sources = {}

    for _, _, _, source in targets:
        sources[source] = (
            sources.get(source, 0)
            + 1
        )

    print()
    print(name)
    print(
        "  Target papers :",
        total,
    )
    print(
        "  Abstract OK   :",
        with_abstract,
    )
    print(
        "  Missing       :",
        total - with_abstract,
    )

    print(
        "  Sources       :",
        sources,
    )


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 78)
    print(
        "CNS ARTICLE AGENT V3 — ABSTRACT RETRIEVAL TEST"
    )
    print("=" * 78)

    print()
    print("OpenAI API : OFF")
    print("Email      : OFF")
    print("Dedup      : OFF")

    print()
    print(
        "Maximum target papers per journal:",
        MAX_TARGETS_PER_JOURNAL,
    )

    print()
    print("Filtering rules:")
    print(
        "  Nature  -> official page "
        "citation_article_type == Article"
    )
    print(
        "  Science -> RSS type == Research Article"
    )
    print(
        "  Cell    -> RSS type == Article"
    )

    nature = next(
        x
        for x in JOURNALS
        if x["name"] == "Nature"
    )

    science = next(
        x
        for x in JOURNALS
        if x["name"] == "Science"
    )

    cell = next(
        x
        for x in JOURNALS
        if x["name"] == "Cell"
    )

    nature_targets = process_nature(
        nature
    )

    science_targets = process_science(
        science
    )

    cell_targets = process_cell(
        cell
    )

    print()
    print("=" * 78)
    print("V3 SUMMARY")
    print("=" * 78)

    summarize(
        "Nature",
        nature_targets,
    )

    summarize(
        "Science",
        science_targets,
    )

    summarize(
        "Cell",
        cell_targets,
    )

    print()
    print("=" * 78)
    print("V3 FINISHED")
    print("=" * 78)


if __name__ == "__main__":
    main()
