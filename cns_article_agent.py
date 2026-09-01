#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CNS Article Agent V4 — OpenAI Analysis Test

Targets:
Nature  -> Article
Science -> Research Article
Cell    -> Article

Purpose:
- Retrieve scientific text
- Select 6 diagnostic papers
- Ask OpenAI for structured scientific analysis
- Test hallucination resistance on LOW evidence

NO EMAIL
NO DEDUP
NO SCHEDULE
"""

import os
import re
import html
import json
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

OPENAI_MODEL = "gpt-5.6-luna"

UA = (
    "Mozilla/5.0 "
    "(compatible; CNSArticleAgent/4.0; +https://github.com/)"
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

    value = html.unescape(
        str(value)
    )

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

    return tag.split(
        "}",
        1,
    )[-1].lower()


def child_text(element, names):

    names = {
        name.lower()
        for name in names
    }

    for child in list(element):

        if local_name(
            child.tag
        ) in names:

            text = "".join(
                child.itertext()
            )

            if text.strip():
                return clean(text)

    return ""


def descendant_texts(
    element,
    names,
):

    names = {
        name.lower()
        for name in names
    }

    values = []

    for child in element.iter():

        if local_name(
            child.tag
        ) in names:

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

    for child in element.iter():

        if local_name(
            child.tag
        ) != "link":
            continue

        href = child.attrib.get(
            "href"
        )

        if href:
            return href.strip()

        text = "".join(
            child.itertext()
        ).strip()

        if text.startswith(
            "http"
        ):
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
# DATES
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

    return (
        raw.decode(
            "utf-8",
            errors="replace",
        ),
        content_type,
        final_url,
    )


# ============================================================
# RSS
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

        date_raw = child_text(
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

        identifiers = (
            descendant_texts(
                entry,
                [
                    "identifier",
                    "doi",
                ],
            )
        )

        categories = (
            descendant_texts(
                entry,
                [
                    "category",
                    "type",
                    "section",
                    "subject",
                ],
            )
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
                "title": title,
                "url": link,
                "doi": doi,
                "date_raw": date_raw,
                "date": format_date(
                    date_raw
                ),
                "feed_types": (
                    categories
                ),
                "feed_description": (
                    description
                ),
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
# HTML METADATA
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


def official_abstract(page):

    candidates = []

    for key in [
        "citation_abstract",
        "dc.description",
        "DC.description",
        "description",
        "og:description",
    ]:

        text = extract_meta_tag(
            page,
            key,
        )

        if len(text) >= 150:

            candidates.append(
                (
                    len(text),
                    key,
                    text,
                )
            )

    if not candidates:

        return "", ""

    candidates.sort(
        reverse=True
    )

    _, key, text = (
        candidates[0]
    )

    return (
        text,
        "OFFICIAL_PAGE/" + key,
    )


# ============================================================
# PUBMED
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

        fetch_url = (
            "https://eutils.ncbi.nlm.nih.gov/"
            "entrez/eutils/efetch.fcgi"
            "?db=pubmed"
            f"&id={ids[0]}"
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

            if text:
                parts.append(text)

        return clean(
            " ".join(parts)
        )

    except Exception:

        return ""


# ============================================================
# CROSSREF
# ============================================================

def crossref_abstract_by_doi(doi):

    if not doi:
        return ""

    encoded = urllib.parse.quote(
        doi,
        safe="",
    )

    url = (
        "https://api.crossref.org/"
        "works/"
        + encoded
    )

    try:

        raw, _, _ = request_bytes(
            url,
            accept="application/json",
        )

        data = json.loads(
            raw.decode(
                "utf-8",
                errors="replace",
            )
        )

        abstract = (
            data.get(
                "message",
                {},
            )
            .get(
                "abstract",
                "",
            )
        )

        return clean(
            abstract
        )

    except Exception:

        return ""


# ============================================================
# TYPE FILTERING
# ============================================================

def normalized_types(paper):

    return [
        clean(value).lower()
        for value
        in paper["feed_types"]
    ]


def science_is_article(paper):

    return (
        "research article"
        in normalized_types(
            paper
        )
    )


def cell_is_article(paper):

    return (
        "article"
        in normalized_types(
            paper
        )
    )


def nature_article(
    paper
):

    if not (
        paper["doi"]
        .lower()
        .startswith(
            "10.1038/s41586-"
        )
    ):

        return None

    try:

        page, _, _ = (
            request_text(
                paper["url"]
            )
        )

    except Exception:

        return None

    article_type = (
        extract_meta_tag(
            page,
            "citation_article_type",
        )
    )

    if (
        article_type
        .strip()
        .lower()
        != "article"
    ):

        return None

    text, source = (
        official_abstract(
            page
        )
    )

    if not text:
        return None

    result = dict(
        paper
    )

    result.update(
        {
            "article_type": (
                "Article"
            ),
            "scientific_text": text,
            "source": source,
            "quality": "HIGH",
        }
    )

    return result


# ============================================================
# SCIENCE / CELL SCIENTIFIC TEXT
# ============================================================

def enrich_non_nature(
    paper,
    article_type,
):

    # ------------------------------------
    # Official page
    # ------------------------------------

    try:

        page, _, _ = (
            request_text(
                paper["url"]
            )
        )

        text, source = (
            official_abstract(
                page
            )
        )

        if text:

            result = dict(
                paper
            )

            result.update(
                {
                    "article_type": (
                        article_type
                    ),
                    "scientific_text": (
                        text
                    ),
                    "source": source,
                    "quality": "HIGH",
                }
            )

            return result

    except Exception:
        pass

    # ------------------------------------
    # PubMed
    # ------------------------------------

    text = pubmed_abstract_by_doi(
        paper["doi"]
    )

    if len(text) >= 100:

        result = dict(
            paper
        )

        result.update(
            {
                "article_type": (
                    article_type
                ),
                "scientific_text": text,
                "source": "PUBMED",
                "quality": "HIGH",
            }
        )

        return result

    # ------------------------------------
    # Crossref
    # ------------------------------------

    text = crossref_abstract_by_doi(
        paper["doi"]
    )

    if len(text) >= 100:

        result = dict(
            paper
        )

        result.update(
            {
                "article_type": (
                    article_type
                ),
                "scientific_text": text,
                "source": "CROSSREF",
                "quality": "HIGH",
            }
        )

        return result

    # ------------------------------------
    # RSS
    # ------------------------------------

    text = clean(
        paper[
            "feed_description"
        ]
    )

    if len(text) >= 500:
        quality = "MEDIUM"

    elif len(text) >= 150:
        quality = "LOW"

    else:
        quality = "NONE"

    result = dict(
        paper
    )

    result.update(
        {
            "article_type": (
                article_type
            ),
            "scientific_text": text,
            "source": (
                "RSS_DESCRIPTION"
                if text
                else "UNAVAILABLE"
            ),
            "quality": quality,
        }
    )

    return result


# ============================================================
# BUILD ARTICLE POOLS
# ============================================================

def get_nature_articles():

    journal = JOURNALS[0]

    papers = fetch_feed(
        journal
    )

    results = []

    for paper in papers:

        result = nature_article(
            paper
        )

        if result:
            results.append(
                result
            )

        if len(results) >= 5:
            break

    return results


def get_science_articles():

    journal = JOURNALS[1]

    papers = fetch_feed(
        journal
    )

    results = []

    for paper in papers:

        if not science_is_article(
            paper
        ):
            continue

        result = enrich_non_nature(
            paper,
            "Research Article",
        )

        results.append(
            result
        )

        if len(results) >= 5:
            break

    return results


def get_cell_articles():

    journal = JOURNALS[2]

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

    return [
        enrich_non_nature(
            paper,
            "Article",
        )
        for paper
        in article_papers[:10]
    ]


# ============================================================
# SELECT SIX TEST PAPERS
# ============================================================

def select_test_articles(
    nature,
    science,
    cell,
):

    selected = []

    # Nature: first 2 HIGH
    nature_high = [
        paper
        for paper in nature
        if paper["quality"] == "HIGH"
    ]

    selected.extend(
        nature_high[:2]
    )

    # Science: first 2 HIGH
    science_high = [
        paper
        for paper in science
        if paper["quality"] == "HIGH"
    ]

    selected.extend(
        science_high[:2]
    )

    # Cell: one HIGH
    cell_high = [
        paper
        for paper in cell
        if paper["quality"] == "HIGH"
    ]

    if cell_high:
        selected.append(
            cell_high[0]
        )

    # Cell: one LOW
    cell_low = [
        paper
        for paper in cell
        if paper["quality"] == "LOW"
    ]

    if cell_low:
        selected.append(
            cell_low[0]
        )

    return selected


# ============================================================
# OPENAI
# ============================================================

def build_analysis_prompt(
    paper
):

    evidence_warning = ""

    if paper["quality"] == "LOW":

        evidence_warning = """
IMPORTANT:
The supplied source is LOW-EVIDENCE scientific text.
It may be only a short publisher summary.

You MUST be especially conservative.
Do not reconstruct experimental procedures.
Do not infer likely methods from the topic.
If methods are not explicitly stated, report:
"Not stated in available source."
"""

    return f"""
You are analyzing a scientific research paper.

Your job is to extract and interpret ONLY what is supported
by the supplied scientific text.

============================================================
STRICT EVIDENCE RULES
============================================================

1. Never invent information.

2. Never infer a method merely because it would normally
   be used in this research field.

3. Never infer methods from the paper title.

4. Never infer experimental design from general scientific
   knowledge.

5. A method may be reported ONLY when it is explicitly
   stated or directly described in the supplied scientific
   text.

6. If the available source does not state a method, write:

   "Not stated in available source."

7. Distinguish clearly between:
   - what the authors did,
   - what they found,
   - what you interpret as the conceptual significance.

8. Do not claim methodological innovation unless the supplied
   source explicitly supports that interpretation.

9. Do not exaggerate novelty.

10. Evidence limitations are mandatory.

{evidence_warning}

============================================================
PAPER METADATA
============================================================

Journal:
{paper["journal"]}

Title:
{paper["title"]}

DOI:
{paper["doi"]}

Publication date:
{paper["date"]}

Article type:
{paper["article_type"]}

Scientific-text source:
{paper["source"]}

Evidence quality:
{paper["quality"]}

============================================================
SCIENTIFIC TEXT
============================================================

{paper["scientific_text"]}

============================================================
OUTPUT
============================================================

Return ONLY valid JSON.

Use exactly this structure:

{{
  "research_question": "",
  "study_system": "",
  "methods": [
    {{
      "method": "",
      "purpose": ""
    }}
  ],
  "key_findings": [
    ""
  ],
  "conceptual_innovation": "",
  "methodological_innovation": "",
  "why_it_matters": "",
  "evidence_limitations": ""
}}

Write the scientific analysis in Chinese.

Keep technical method names, gene names, proteins,
algorithms, instruments, datasets and specialist terminology
in their standard English form where appropriate.

For "methods":
- include only explicitly supported methods;
- give the purpose of each method;
- if no method is explicitly supported, return:

[
  {{
    "method": "Not stated in available source.",
    "purpose": "Not stated in available source."
  }}
]

For methodological_innovation:
if not explicitly supported, write:
"Not stated in available source."

Return JSON only.
""".strip()


def call_openai(
    paper
):

    api_key = os.environ.get(
        "OPENAI_API_KEY",
        "",
    ).strip()

    if not api_key:

        raise RuntimeError(
            "OPENAI_API_KEY is missing."
        )

    prompt = build_analysis_prompt(
        paper
    )

    payload = {
        "model": OPENAI_MODEL,
        "input": prompt,
        "reasoning": {
            "effort": "low"
        },
        "text": {
            "format": {
                "type": "json_schema",
                "name": (
                    "scientific_analysis"
                ),
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "research_question": {
                            "type": "string"
                        },
                        "study_system": {
                            "type": "string"
                        },
                        "methods": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "method": {
                                        "type": "string"
                                    },
                                    "purpose": {
                                        "type": "string"
                                    },
                                },
                                "required": [
                                    "method",
                                    "purpose",
                                ],
                                "additionalProperties": False,
                            },
                        },
                        "key_findings": {
                            "type": "array",
                            "items": {
                                "type": "string"
                            },
                        },
                        "conceptual_innovation": {
                            "type": "string"
                        },
                        "methodological_innovation": {
                            "type": "string"
                        },
                        "why_it_matters": {
                            "type": "string"
                        },
                        "evidence_limitations": {
                            "type": "string"
                        },
                    },
                    "required": [
                        "research_question",
                        "study_system",
                        "methods",
                        "key_findings",
                        "conceptual_innovation",
                        "methodological_innovation",
                        "why_it_matters",
                        "evidence_limitations",
                    ],
                    "additionalProperties": False,
                },
            }
        },
    }

    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(
            payload
        ).encode(
            "utf-8"
        ),
        headers={
            "Authorization": (
                "Bearer "
                + api_key
            ),
            "Content-Type": (
                "application/json"
            ),
        },
        method="POST",
    )

    context = ssl.create_default_context()

    with urllib.request.urlopen(
        request,
        timeout=120,
        context=context,
    ) as response:

        raw = response.read()

    data = json.loads(
        raw.decode(
            "utf-8"
        )
    )

    # Responses API output parsing
    output_text = ""

    for item in data.get(
        "output",
        [],
    ):

        if item.get(
            "type"
        ) != "message":
            continue

        for content in item.get(
            "content",
            [],
        ):

            if content.get(
                "type"
            ) == "output_text":

                output_text += (
                    content.get(
                        "text",
                        "",
                    )
                )

    if not output_text:

        raise RuntimeError(
            "OpenAI returned no output_text."
        )

    return json.loads(
        output_text
    )


# ============================================================
# PRINT AI RESULT
# ============================================================

def print_analysis(
    number,
    paper,
    analysis,
):

    print()
    print("=" * 78)

    print(
        f"AI ANALYSIS {number}/6"
    )

    print("=" * 78)

    print(
        "Journal :",
        paper["journal"],
    )

    print(
        "Title   :",
        paper["title"],
    )

    print(
        "DOI     :",
        paper["doi"],
    )

    print(
        "Source  :",
        paper["source"],
    )

    print(
        "Quality :",
        paper["quality"],
    )

    print(
        "Chars   :",
        len(
            paper[
                "scientific_text"
            ]
        ),
    )

    print()
    print(
        "RESEARCH QUESTION"
    )

    print(
        analysis[
            "research_question"
        ]
    )

    print()
    print(
        "STUDY SYSTEM"
    )

    print(
        analysis[
            "study_system"
        ]
    )

    print()
    print(
        "METHODS"
    )

    for index, method in enumerate(
        analysis["methods"],
        start=1,
    ):

        print(
            f"{index}.",
            method["method"],
        )

        print(
            "   Purpose:",
            method["purpose"],
        )

    print()
    print(
        "KEY FINDINGS"
    )

    for finding in analysis[
        "key_findings"
    ]:

        print(
            "-",
            finding,
        )

    print()
    print(
        "CONCEPTUAL INNOVATION"
    )

    print(
        analysis[
            "conceptual_innovation"
        ]
    )

    print()
    print(
        "METHODOLOGICAL INNOVATION"
    )

    print(
        analysis[
            "methodological_innovation"
        ]
    )

    print()
    print(
        "WHY IT MATTERS"
    )

    print(
        analysis[
            "why_it_matters"
        ]
    )

    print()
    print(
        "EVIDENCE LIMITATIONS"
    )

    print(
        analysis[
            "evidence_limitations"
        ]
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 78)

    print(
        "CNS ARTICLE AGENT V4 "
        "— OPENAI ANALYSIS TEST"
    )

    print("=" * 78)

    print()
    print(
        "Model :",
        OPENAI_MODEL,
    )

    print(
        "Email : OFF"
    )

    print(
        "Dedup : OFF"
    )

    print(
        "Test  : 6 papers"
    )

    print()
    print(
        "Retrieving article pools..."
    )

    nature = (
        get_nature_articles()
    )

    science = (
        get_science_articles()
    )

    cell = (
        get_cell_articles()
    )

    print()
    print(
        "Nature pool:",
        len(nature),
    )

    print(
        "Science pool:",
        len(science),
    )

    print(
        "Cell pool:",
        len(cell),
    )

    selected = (
        select_test_articles(
            nature,
            science,
            cell,
        )
    )

    print()
    print("=" * 78)
    print(
        "SELECTED TEST PAPERS"
    )
    print("=" * 78)

    for index, paper in enumerate(
        selected,
        start=1,
    ):

        print()

        print(
            index,
            paper["journal"],
            "|",
            paper["quality"],
            "|",
            paper["title"],
        )

    if len(selected) != 6:

        raise RuntimeError(
            "Expected exactly 6 test papers, "
            f"but selected {len(selected)}."
        )

    print()
    print("=" * 78)
    print(
        "STARTING OPENAI ANALYSIS"
    )
    print("=" * 78)

    successes = 0
    failures = 0

    for index, paper in enumerate(
        selected,
        start=1,
    ):

        try:

            analysis = call_openai(
                paper
            )

            print_analysis(
                index,
                paper,
                analysis,
            )

            successes += 1

        except Exception as exc:

            failures += 1

            print()
            print("=" * 78)

            print(
                f"AI ANALYSIS {index}/6 FAILED"
            )

            print("=" * 78)

            print(
                paper["journal"],
                "|",
                paper["title"],
            )

            print(
                type(exc).__name__,
                ":",
                exc,
            )

    print()
    print("=" * 78)
    print(
        "V4 SUMMARY"
    )
    print("=" * 78)

    print(
        "Selected papers :",
        len(selected),
    )

    print(
        "AI successes    :",
        successes,
    )

    print(
        "AI failures     :",
        failures,
    )

    print()
    print(
        "Expected diagnostic:"
    )

    print(
        "- HIGH evidence should produce "
        "specific supported methods."
    )

    print(
        "- LOW evidence should NOT cause "
        "the model to invent methods."
    )

    print()
    print("=" * 78)
    print(
        "V4 FINISHED"
    )
    print("=" * 78)


if __name__ == "__main__":
    main()
