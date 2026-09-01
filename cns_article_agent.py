#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CNS Article Agent V3.5 — Scientific Text Retrieval Diagnostic

Targets:
- Nature  -> Article
- Science -> Research Article
- Cell    -> Article

Article-type rules established by V2:
- Nature:
    RSS candidate -> official Nature page
    citation_article_type == "Article"
- Science:
    RSS type == "Research Article"
- Cell:
    RSS type == "Article"

Scientific-text retrieval priority:
1. Official publisher page
2. PubMed
3. Crossref
4. RSS description

Evidence quality:
- HIGH   = official abstract / PubMed / Crossref abstract
- MEDIUM = substantive RSS scientific text >= 500 chars
- LOW    = short RSS scientific text >= 150 chars
- NONE   = no usable scientific text

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
    "(compatible; CNSArticleAgent/3.5; +https://github.com/)"
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

    value = re.sub(
        r"<!\[CDATA\[|\]\]>",
        "",
        value,
    )

    value = re.sub(
        r"<[^>]+>",
        " ",
        value,
    )

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    return value.strip()


def local_name(tag):
    return tag.split("}", 1)[-1].lower()


def child_text(element, names):
    names = {
        name.lower()
        for name in names
    }

    for child in list(element):

        if local_name(child.tag) in names:

            text = "".join(
                child.itertext()
            )

            if text.strip():
                return clean(text)

    return ""


def descendant_texts(element, names):
    names = {
        name.lower()
        for name in names
    }

    values = []

    for child in element.iter():

        if local_name(child.tag) in names:

            text = clean(
                "".join(
                    child.itertext()
                )
            )

            if (
                text
                and text not in values
            ):
                values.append(text)

    return values


def extract_link(element):

    for child in list(element):

        if local_name(child.tag) == "link":

            href = child.attrib.get(
                "href"
            )

            if href:
                return href.strip()

            text = "".join(
                child.itertext()
            ).strip()

            if text.startswith("http"):
                return text

    for child in element.iter():

        if local_name(child.tag) == "link":

            href = child.attrib.get(
                "href"
            )

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

    return match.group(0).rstrip(
        ".,;)"
    )


def find_doi(*values):

    for value in values:

        doi = normalize_doi(
            value
        )

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

        dt = parsedate_to_datetime(
            value
        )

        if dt:

            if not dt.tzinfo:
                dt = dt.replace(
                    tzinfo=timezone.utc
                )

            return dt.astimezone(
                timezone.utc
            )

    except Exception:
        pass

    try:

        dt = datetime.fromisoformat(
            value.replace(
                "Z",
                "+00:00",
            )
        )

        if not dt.tzinfo:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt.astimezone(
            timezone.utc
        )

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

def request_bytes(
    url,
    accept=None,
):

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
            "Accept-Language": (
                "en-US,en;q=0.9"
            ),
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
            response.headers.get(
                "Content-Type",
                "",
            ),
            response.geturl(),
        )


def request_text(url):

    raw, content_type, final_url = (
        request_bytes(
            url,
            accept=(
                "text/html,"
                "application/xhtml+xml"
            ),
        )
    )

    text = raw.decode(
        "utf-8",
        errors="replace",
    )

    return (
        text,
        content_type,
        final_url,
    )


# ============================================================
# FEED PARSING
# ============================================================

def parse_feed(
    xml_bytes,
    journal,
):

    root = ET.fromstring(
        xml_bytes
    )

    entries = [
        element
        for element in root.iter()
        if local_name(
            element.tag
        ) in (
            "item",
            "entry",
        )
    ]

    papers = []

    for entry in entries[
        :MAX_FEED_ITEMS
    ]:

        title = child_text(
            entry,
            ["title"],
        )

        link = extract_link(
            entry
        )

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
            " ".join(
                identifiers
            ),
            link,
            description,
        )

        papers.append(
            {
                "journal": (
                    journal["name"]
                ),
                "target_type": (
                    journal[
                        "target_type"
                    ]
                ),
                "title": title,
                "date_raw": date_text,
                "date": format_date(
                    date_text
                ),
                "doi": doi,
                "url": link,
                "feed_types": categories,
                "feed_description": (
                    description
                ),
            }
        )

    return papers


def fetch_feed(journal):

    raw, content_type, final_url = (
        request_bytes(
            journal["feed"]
        )
    )

    papers = parse_feed(
        raw,
        journal,
    )

    print()
    print(
        journal["name"],
        "feed items:",
        len(papers),
    )

    print(
        journal["name"],
        "feed content-type:",
        content_type,
    )

    print(
        journal["name"],
        "feed final URL:",
        final_url,
    )

    return papers


# ============================================================
# HTML META
# ============================================================

def extract_meta_tag(
    page,
    key,
):

    patterns = [
        (
            rf'<meta[^>]+'
            rf'name=["\']{re.escape(key)}["\']'
            rf'[^>]+content=["\']([^"\']+)["\']'
        ),
        (
            rf'<meta[^>]+'
            rf'content=["\']([^"\']+)["\']'
            rf'[^>]+name=["\']{re.escape(key)}["\']'
        ),
        (
            rf'<meta[^>]+'
            rf'property=["\']{re.escape(key)}["\']'
            rf'[^>]+content=["\']([^"\']+)["\']'
        ),
        (
            rf'<meta[^>]+'
            rf'content=["\']([^"\']+)["\']'
            rf'[^>]+property=["\']{re.escape(key)}["\']'
        ),
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


# ============================================================
# JSON-LD
# ============================================================

def extract_jsonld(page):

    blocks = []

    pattern = re.compile(
        (
            r'<script[^>]+'
            r'type=["\']application/ld\+json["\']'
            r'[^>]*>(.*?)</script>'
        ),
        flags=re.I | re.S,
    )

    for match in pattern.finditer(
        page
    ):

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

    if isinstance(
        value,
        dict,
    ):

        yield value

        for child in value.values():

            yield from walk_json(
                child
            )

    elif isinstance(
        value,
        list,
    ):

        for child in value:

            yield from walk_json(
                child
            )


# ============================================================
# OFFICIAL PAGE ABSTRACT
# ============================================================

def abstract_from_jsonld(page):

    candidates = []

    for block in extract_jsonld(
        page
    ):

        for obj in walk_json(
            block
        ):

            abstract = obj.get(
                "abstract"
            )

            if isinstance(
                abstract,
                str,
            ):

                text = clean(
                    abstract
                )

                if len(text) >= 100:
                    candidates.append(
                        text
                    )

            description = obj.get(
                "description"
            )

            if isinstance(
                description,
                str,
            ):

                text = clean(
                    description
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
        key=lambda item: len(
            item[1]
        ),
        reverse=True,
    )

    key, text = candidates[0]

    return (
        text,
        "HTML_META:" + key,
    )


def abstract_from_html_patterns(
    page
):

    patterns = [
        (
            r'<div[^>]+'
            r'id=["\']Abs1-content["\']'
            r'[^>]*>(.*?)</div>'
        ),
        (
            r'<div[^>]+'
            r'class=["\'][^"\']*abstract[^"\']*["\']'
            r'[^>]*>(.*?)</div>'
        ),
        (
            r'<section[^>]+'
            r'class=["\'][^"\']*abstract[^"\']*["\']'
            r'[^>]*>(.*?)</section>'
        ),
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

        page, _, final_url = (
            request_text(url)
        )

    except Exception as exc:

        return {
            "status": (
                "FAILED:"
                + type(exc).__name__
            ),
            "abstract": "",
            "source": "",
            "final_url": "",
        }

    # ------------------------------------
    # Meta
    # ------------------------------------

    abstract, source = (
        abstract_from_meta(
            page
        )
    )

    if abstract:

        return {
            "status": "OK",
            "abstract": abstract,
            "source": source,
            "final_url": final_url,
        }

    # ------------------------------------
    # JSON-LD
    # ------------------------------------

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

    # ------------------------------------
    # HTML abstract block
    # ------------------------------------

    abstract = (
        abstract_from_html_patterns(
            page
        )
    )

    if abstract:

        return {
            "status": "OK",
            "abstract": abstract,
            "source": (
                "HTML_ABSTRACT_BLOCK"
            ),
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
                {},
            )
            .get(
                "idlist",
                [],
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

        xml_bytes, _, _ = (
            request_bytes(
                fetch_url,
                accept=(
                    "application/xml,"
                    "text/xml"
                ),
            )
        )

        root = ET.fromstring(
            xml_bytes
        )

        parts = []

        for element in root.iter():

            if (
                local_name(
                    element.tag
                )
                != "abstracttext"
            ):
                continue

            text = clean(
                "".join(
                    element.itertext()
                )
            )

            label = (
                element.attrib.get(
                    "Label",
                    "",
                )
                .strip()
            )

            if label and text:

                parts.append(
                    f"{label}: {text}"
                )

            elif text:

                parts.append(
                    text
                )

        return clean(
            " ".join(parts)
        )

    except Exception:

        return ""


# ============================================================
# CROSSREF FALLBACK
# ============================================================

def crossref_abstract_by_doi(doi):

    if not doi:

        return (
            "",
            "NO_DOI",
        )

    encoded_doi = urllib.parse.quote(
        doi,
        safe="",
    )

    url = (
        "https://api.crossref.org/works/"
        + encoded_doi
    )

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json",
        },
    )

    try:

        context = (
            ssl.create_default_context()
        )

        with urllib.request.urlopen(
            request,
            timeout=TIMEOUT,
            context=context,
        ) as response:

            raw = response.read()

        data = json.loads(
            raw.decode(
                "utf-8",
                errors="replace",
            )
        )

        message = data.get(
            "message",
            {},
        )

        abstract = message.get(
            "abstract",
            "",
        )

        if not abstract:

            return (
                "",
                "NO_ABSTRACT",
            )

        abstract = clean(
            abstract
        )

        if len(abstract) < 100:

            return (
                abstract,
                "TOO_SHORT",
            )

        return (
            abstract,
            "OK",
        )

    except Exception as exc:

        return (
            "",
            (
                "FAILED:"
                + type(exc).__name__
            ),
        )


# ============================================================
# ARTICLE TYPE FILTERS
# ============================================================

def nature_is_article(paper):

    """
    s41586 is only a candidate restriction.

    Final Nature type decision:
    official page
    citation_article_type == Article
    """

    doi = paper["doi"].lower()

    if not doi.startswith(
        "10.1038/s41586-"
    ):

        return (
            False,
            "",
            None,
        )

    if not paper["url"]:

        return (
            False,
            "NO_URL",
            None,
        )

    try:

        page, _, final_url = (
            request_text(
                paper["url"]
            )
        )

    except Exception as exc:

        return (
            False,
            (
                "PAGE_FAILED:"
                + type(exc).__name__
            ),
            None,
        )

    article_type = (
        extract_meta_tag(
            page,
            "citation_article_type",
        )
    )

    keep = (
        article_type.strip().lower()
        == "article"
    )

    return (
        keep,
        (
            article_type
            or "Unavailable"
        ),
        {
            "page": page,
            "final_url": final_url,
        },
    )


def science_is_article(paper):

    normalized_types = [
        clean(value).lower()
        for value
        in paper["feed_types"]
    ]

    return (
        "research article"
        in normalized_types
    )


def cell_is_article(paper):

    normalized_types = [
        clean(value).lower()
        for value
        in paper["feed_types"]
    ]

    return (
        "article"
        in normalized_types
    )


# ============================================================
# SCIENTIFIC TEXT RETRIEVAL
# ============================================================

def get_scientific_text(
    paper,
    cached_page=None,
):

    """
    Retrieval priority:

    1. Official publisher page
    2. PubMed
    3. Crossref
    4. RSS description

    Returns:
        text
        source
        evidence_quality
        diagnostics
    """

    diagnostics = []

    # ========================================================
    # 1. OFFICIAL PUBLISHER PAGE
    # ========================================================

    if cached_page:

        page = cached_page["page"]

        abstract, source = (
            abstract_from_meta(
                page
            )
        )

        if abstract:

            return (
                abstract,
                (
                    "OFFICIAL_PAGE/"
                    + source
                ),
                "HIGH",
                diagnostics,
            )

        abstract = (
            abstract_from_jsonld(
                page
            )
        )

        if abstract:

            return (
                abstract,
                (
                    "OFFICIAL_PAGE/"
                    "JSON_LD"
                ),
                "HIGH",
                diagnostics,
            )

        abstract = (
            abstract_from_html_patterns(
                page
            )
        )

        if abstract:

            return (
                abstract,
                (
                    "OFFICIAL_PAGE/"
                    "HTML_ABSTRACT_BLOCK"
                ),
                "HIGH",
                diagnostics,
            )

        diagnostics.append(
            "Official page: no abstract"
        )

    else:

        result = (
            retrieve_page_abstract(
                paper["url"]
            )
        )

        if result["abstract"]:

            return (
                result["abstract"],
                (
                    "OFFICIAL_PAGE/"
                    + result["source"]
                ),
                "HIGH",
                diagnostics,
            )

        diagnostics.append(
            (
                "Official page: "
                + result["status"]
            )
        )

    # ========================================================
    # 2. PUBMED
    # ========================================================

    pubmed_abstract = (
        pubmed_abstract_by_doi(
            paper["doi"]
        )
    )

    if len(
        pubmed_abstract
    ) >= 100:

        return (
            pubmed_abstract,
            "PUBMED",
            "HIGH",
            diagnostics,
        )

    diagnostics.append(
        "PubMed: unavailable"
    )

    # ========================================================
    # 3. CROSSREF
    # ========================================================

    (
        crossref_abstract,
        crossref_status,
    ) = crossref_abstract_by_doi(
        paper["doi"]
    )

    if len(
        crossref_abstract
    ) >= 100:

        return (
            crossref_abstract,
            "CROSSREF",
            "HIGH",
            diagnostics,
        )

    diagnostics.append(
        (
            "Crossref: "
            + crossref_status
        )
    )

    # ========================================================
    # 4. RSS DESCRIPTION
    # ========================================================

    rss_text = clean(
        paper["feed_description"]
    )

    if len(rss_text) >= 500:

        return (
            rss_text,
            "RSS_DESCRIPTION",
            "MEDIUM",
            diagnostics,
        )

    if len(rss_text) >= 150:

        return (
            rss_text,
            "RSS_DESCRIPTION",
            "LOW",
            diagnostics,
        )

    diagnostics.append(
        "RSS: insufficient"
    )

    return (
        "",
        "UNAVAILABLE",
        "NONE",
        diagnostics,
    )


# ============================================================
# OUTPUT
# ============================================================

def print_paper(
    number,
    paper,
    article_type,
    scientific_text,
    source,
    quality,
    diagnostics,
):

    print()
    print("-" * 78)

    print(
        f"[{paper['journal']} {number}]"
    )

    print("-" * 78)

    print(
        "Title            :",
        paper["title"],
    )

    print(
        "Date             :",
        paper["date"],
    )

    print(
        "DOI              :",
        (
            paper["doi"]
            or "Unavailable"
        ),
    )

    print(
        "Article type     :",
        article_type,
    )

    print(
        "URL              :",
        (
            paper["url"]
            or "Unavailable"
        ),
    )

    print(
        "Scientific source:",
        source,
    )

    print(
        "Evidence quality :",
        quality,
    )

    print(
        "Text chars       :",
        len(scientific_text),
    )

    if diagnostics:

        print(
            "Fallback path    :",
            " -> ".join(
                diagnostics
            ),
        )

    else:

        print(
            "Fallback path    :",
            "Direct success",
        )

    print()
    print("SCIENTIFIC TEXT")
    print()

    if scientific_text:

        print(
            scientific_text
        )

    else:

        print(
            "[NO SUBSTANTIVE "
            "SCIENTIFIC TEXT FOUND]"
        )


# ============================================================
# NATURE
# ============================================================

def process_nature(journal):

    print()
    print("=" * 78)
    print(
        "NATURE — ARTICLE + SCIENTIFIC TEXT"
    )
    print("=" * 78)

    papers = fetch_feed(
        journal
    )

    targets = []

    for paper in papers:

        if (
            len(targets)
            >= MAX_TARGETS_PER_JOURNAL
        ):
            break

        (
            keep,
            article_type,
            cached_page,
        ) = nature_is_article(
            paper
        )

        if not keep:
            continue

        (
            scientific_text,
            source,
            quality,
            diagnostics,
        ) = get_scientific_text(
            paper,
            cached_page=cached_page,
        )

        targets.append(
            {
                "paper": paper,
                "article_type": (
                    article_type
                ),
                "scientific_text": (
                    scientific_text
                ),
                "source": source,
                "quality": quality,
                "diagnostics": (
                    diagnostics
                ),
            }
        )

    for number, item in enumerate(
        targets,
        start=1,
    ):

        print_paper(
            number,
            item["paper"],
            item["article_type"],
            item["scientific_text"],
            item["source"],
            item["quality"],
            item["diagnostics"],
        )

    return targets


# ============================================================
# SCIENCE
# ============================================================

def process_science(journal):

    print()
    print("=" * 78)
    print(
        "SCIENCE — RESEARCH ARTICLE + SCIENTIFIC TEXT"
    )
    print("=" * 78)

    papers = fetch_feed(
        journal
    )

    targets = []

    for paper in papers:

        if (
            len(targets)
            >= MAX_TARGETS_PER_JOURNAL
        ):
            break

        if not science_is_article(
            paper
        ):
            continue

        (
            scientific_text,
            source,
            quality,
            diagnostics,
        ) = get_scientific_text(
            paper
        )

        targets.append(
            {
                "paper": paper,
                "article_type": (
                    "Research Article"
                ),
                "scientific_text": (
                    scientific_text
                ),
                "source": source,
                "quality": quality,
                "diagnostics": (
                    diagnostics
                ),
            }
        )

    for number, item in enumerate(
        targets,
        start=1,
    ):

        print_paper(
            number,
            item["paper"],
            item["article_type"],
            item["scientific_text"],
            item["source"],
            item["quality"],
            item["diagnostics"],
        )

    return targets


# ============================================================
# CELL
# ============================================================

def process_cell(journal):

    print()
    print("=" * 78)
    print(
        "CELL — ARTICLE + SCIENTIFIC TEXT"
    )
    print("=" * 78)

    papers = fetch_feed(
        journal
    )

    article_papers = [
        paper
        for paper in papers
        if cell_is_article(
            paper
        )
    ]

    # Cell RSS is not necessarily ordered by
    # publication date, so sort explicitly.

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

    targets = []

    for paper in article_papers[
        :MAX_TARGETS_PER_JOURNAL
    ]:

        (
            scientific_text,
            source,
            quality,
            diagnostics,
        ) = get_scientific_text(
            paper
        )

        targets.append(
            {
                "paper": paper,
                "article_type": (
                    "Article"
                ),
                "scientific_text": (
                    scientific_text
                ),
                "source": source,
                "quality": quality,
                "diagnostics": (
                    diagnostics
                ),
            }
        )

    for number, item in enumerate(
        targets,
        start=1,
    ):

        print_paper(
            number,
            item["paper"],
            item["article_type"],
            item["scientific_text"],
            item["source"],
            item["quality"],
            item["diagnostics"],
        )

    return targets


# ============================================================
# SUMMARY
# ============================================================

def summarize(
    journal_name,
    targets,
):

    total = len(targets)

    high = sum(
        1
        for item in targets
        if item["quality"] == "HIGH"
    )

    medium = sum(
        1
        for item in targets
        if item["quality"] == "MEDIUM"
    )

    low = sum(
        1
        for item in targets
        if item["quality"] == "LOW"
    )

    none = sum(
        1
        for item in targets
        if item["quality"] == "NONE"
    )

    sources = {}

    lengths = []

    for item in targets:

        source = item["source"]

        sources[source] = (
            sources.get(
                source,
                0,
            )
            + 1
        )

        lengths.append(
            len(
                item["scientific_text"]
            )
        )

    average_length = (
        round(
            sum(lengths)
            / len(lengths)
        )
        if lengths
        else 0
    )

    print()
    print(journal_name)

    print(
        "  Target papers     :",
        total,
    )

    print(
        "  HIGH evidence     :",
        high,
    )

    print(
        "  MEDIUM evidence   :",
        medium,
    )

    print(
        "  LOW evidence      :",
        low,
    )

    print(
        "  NONE              :",
        none,
    )

    print(
        "  Average text chars:",
        average_length,
    )

    print(
        "  Sources           :",
        sources,
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 78)

    print(
        "CNS ARTICLE AGENT V3.5 — "
        "SCIENTIFIC TEXT RETRIEVAL"
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
    print("Article filtering rules:")

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

    print()
    print(
        "Scientific-text retrieval:"
    )

    print(
        "  Official publisher page"
    )

    print(
        "      -> PubMed"
    )

    print(
        "      -> Crossref"
    )

    print(
        "      -> RSS description"
    )

    print()
    print(
        "Evidence quality:"
    )

    print(
        "  HIGH   = official abstract / "
        "PubMed / Crossref"
    )

    print(
        "  MEDIUM = RSS >= 500 chars"
    )

    print(
        "  LOW    = RSS 150-499 chars"
    )

    print(
        "  NONE   = insufficient text"
    )

    print()
    print(
        "Current UTC:",
        datetime.now(
            timezone.utc
        ).isoformat(),
    )

    nature = next(
        journal
        for journal in JOURNALS
        if journal["name"] == "Nature"
    )

    science = next(
        journal
        for journal in JOURNALS
        if journal["name"] == "Science"
    )

    cell = next(
        journal
        for journal in JOURNALS
        if journal["name"] == "Cell"
    )

    # --------------------------------------------------------
    # RUN
    # --------------------------------------------------------

    nature_targets = (
        process_nature(
            nature
        )
    )

    science_targets = (
        process_science(
            science
        )
    )

    cell_targets = (
        process_cell(
            cell
        )
    )

    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------

    print()
    print("=" * 78)
    print(
        "V3.5 SUMMARY"
    )
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
    print(
        "V3.5 FINISHED"
    )
    print("=" * 78)

    print()
    print(
        "Interpretation:"
    )

    print(
        "HIGH evidence is suitable for "
        "method-focused AI analysis."
    )

    print(
        "MEDIUM evidence may be usable "
        "with conservative AI instructions."
    )

    print(
        "LOW evidence should not be used "
        "to infer detailed methods."
    )

    print(
        "NONE means the paper needs "
        "another scientific-text source."
    )


if __name__ == "__main__":
    main()
