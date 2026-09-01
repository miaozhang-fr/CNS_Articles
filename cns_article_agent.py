#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CNS Article Agent V6 — Production

Journals:
- Nature  -> Article
- Science -> Research Article
- Cell    -> Article

Production pipeline:
1. Retrieve official RSS feeds
2. Apply exact publication-type filtering
3. Normalize DOI / stable paper key
4. Compare against persistent state
5. Retrieve scientific text:
      official publisher
      -> PubMed
      -> Crossref
      -> RSS summary
6. Analyze new papers with OpenAI
7. Send English HTML email
8. ONLY after successful email:
      update agent_state.json

Important:
- First production run = BASELINE ONLY
- Baseline run sends NO historical-paper email
- Subsequent runs process only unseen papers
- No strict publication-date cutoff
- Cell discovery does not depend on embedded RSS dates
"""

import os
import re
import html
import json
import ssl
import smtplib
import hashlib
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parsedate_to_datetime


# ============================================================
# SETTINGS
# ============================================================

VERSION = "6.0"

TIMEOUT = 30
OPENAI_TIMEOUT = 120

MAX_FEED_ITEMS = 500

STATE_FILE = "agent_state.json"

OPENAI_MODEL = "gpt-5.6-luna"

EMAIL_FROM = "zhangmiao092@gmail.com"
EMAIL_TO = "miao.zhang@universite-paris-saclay.fr"

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465

UA = (
    "Mozilla/5.0 "
    "(compatible; CNSArticleAgent/6.0; +https://github.com/)"
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
            text = clean(
                "".join(child.itertext())
            )

            if text:
                return text

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
                "".join(child.itertext())
            )

            if (
                text
                and text not in values
            ):
                values.append(text)

    return values


def extract_link(element):
    for child in element.iter():
        if local_name(child.tag) != "link":
            continue

        href = child.attrib.get("href")

        if href:
            return href.strip()

        text = clean(
            "".join(child.itertext())
        )

        if text.startswith("http"):
            return text

    return ""


# ============================================================
# DOI / PAPER KEY
# ============================================================

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

    return (
        match.group(0)
        .rstrip(".,;)")
        .lower()
    )


def find_doi(*values):
    for value in values:
        doi = normalize_doi(value)

        if doi:
            return doi

    return ""


def paper_key(paper):
    """
    DOI is the primary persistent identifier.

    If DOI is unavailable, use normalized URL.
    If URL is also unavailable, use a stable title hash.
    """

    doi = normalize_doi(
        paper.get("doi", "")
    )

    if doi:
        return "doi:" + doi

    url = clean(
        paper.get("url", "")
    ).lower()

    if url:
        return "url:" + url

    title = clean(
        paper.get("title", "")
    ).lower()

    digest = hashlib.sha256(
        title.encode("utf-8")
    ).hexdigest()

    return "title:" + digest


# ============================================================
# DATE
# ============================================================

def parse_date(value):
    if not value:
        return None

    value = clean(value)

    try:
        dt = parsedate_to_datetime(value)

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


def utc_now_iso():
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
    )


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

def parse_feed(xml_bytes, journal):
    root = ET.fromstring(xml_bytes)

    entries = [
        element
        for element in root.iter()
        if local_name(element.tag)
        in ("item", "entry")
    ]

    papers = []

    for entry in entries[:MAX_FEED_ITEMS]:
        title = child_text(
            entry,
            ["title"],
        )

        link = extract_link(entry)

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

        paper = {
            "journal": journal["name"],
            "target_type": (
                journal["target_type"]
            ),
            "title": title,
            "url": link,
            "doi": doi,
            "date_raw": date_raw,
            "date": format_date(
                date_raw
            ),
            "feed_types": categories,
            "feed_description": description,
        }

        papers.append(paper)

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
    """
    Prefer publisher-provided abstract/description metadata.

    This retrieval logic has already been validated against
    the production feeds during V3-V5 testing.
    """

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

    candidates.sort(reverse=True)

    _, key, text = candidates[0]

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
                local_name(element.tag)
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

        return clean(abstract)

    except Exception:
        return ""


# ============================================================
# ARTICLE TYPE FILTERS
# ============================================================

def normalized_types(paper):
    return [
        clean(value).lower()
        for value in paper["feed_types"]
    ]


def science_is_article(paper):
    """
    Science production rule:
    normalized exact RSS type == Research Article
    """

    return (
        "research article"
        in normalized_types(paper)
    )


def cell_is_article(paper):
    """
    Cell production rule:
    normalized exact RSS type == Article
    """

    return (
        "article"
        in normalized_types(paper)
    )


def nature_is_candidate(paper):
    """
    Nature s41586 is only a candidate prefilter.

    It is NOT the final publication-type classifier.
    """

    return (
        paper.get("doi", "")
        .lower()
        .startswith(
            "10.1038/s41586-"
        )
    )


def classify_nature_article(paper):
    """
    Nature final rule:

    DOI s41586 candidate
        ->
    official Nature article page
        ->
    citation_article_type == Article

    This prevents Review papers with s41586 DOIs from
    entering the production digest.
    """

    if not nature_is_candidate(
        paper
    ):
        return None

    try:
        page, _, _ = request_text(
            paper["url"]
        )

    except Exception as exc:
        print(
            "Nature page failed:",
            paper["title"],
            "|",
            type(exc).__name__,
        )

        return None

    article_type = extract_meta_tag(
        page,
        "citation_article_type",
    )

    if (
        article_type.strip().lower()
        != "article"
    ):
        return None

    result = dict(paper)

    result["article_type"] = "Article"
    result["_cached_page"] = page

    return result


# ============================================================
# SCIENTIFIC TEXT
# ============================================================

def enrich_scientific_text(
    paper,
    cached_page=None,
):
    """
    Retrieval priority:

    1. Official publisher page
    2. PubMed
    3. Crossref
    4. RSS description

    HIGH:
      official / PubMed / Crossref

    MEDIUM:
      RSS >= 500 chars

    LOW:
      RSS 150-499 chars

    NONE:
      insufficient scientific text
    """

    # --------------------------------------------------------
    # 1. OFFICIAL PAGE
    # --------------------------------------------------------

    page = cached_page

    if page is None:
        try:
            page, _, _ = request_text(
                paper["url"]
            )

        except Exception:
            page = None

    if page:
        text, source = official_abstract(
            page
        )

        if text:
            result = dict(paper)

            result.update(
                {
                    "scientific_text": text,
                    "source": source,
                    "quality": "HIGH",
                }
            )

            result.pop(
                "_cached_page",
                None,
            )

            return result

    # --------------------------------------------------------
    # 2. PUBMED
    # --------------------------------------------------------

    text = pubmed_abstract_by_doi(
        paper.get("doi", "")
    )

    if len(text) >= 100:
        result = dict(paper)

        result.update(
            {
                "scientific_text": text,
                "source": "PUBMED",
                "quality": "HIGH",
            }
        )

        result.pop(
            "_cached_page",
            None,
        )

        return result

    # --------------------------------------------------------
    # 3. CROSSREF
    # --------------------------------------------------------

    text = crossref_abstract_by_doi(
        paper.get("doi", "")
    )

    if len(text) >= 100:
        result = dict(paper)

        result.update(
            {
                "scientific_text": text,
                "source": "CROSSREF",
                "quality": "HIGH",
            }
        )

        result.pop(
            "_cached_page",
            None,
        )

        return result

    # --------------------------------------------------------
    # 4. RSS
    # --------------------------------------------------------

    text = clean(
        paper.get(
            "feed_description",
            "",
        )
    )

    if len(text) >= 500:
        quality = "MEDIUM"

    elif len(text) >= 150:
        quality = "LOW"

    else:
        quality = "NONE"

    result = dict(paper)

    result.update(
        {
            "scientific_text": text,
            "source": (
                "RSS_DESCRIPTION"
                if text
                else "UNAVAILABLE"
            ),
            "quality": quality,
        }
    )

    result.pop(
        "_cached_page",
        None,
    )

    return result


# ============================================================
# DISCOVERY
# ============================================================

def discover_target_articles():
    """
    Discover all currently visible target-type papers.

    IMPORTANT:
    No publication-date cutoff is used.

    Persistent state is the discovery boundary.
    """

    targets = []

    print()
    print("=" * 78)
    print("DISCOVERY")
    print("=" * 78)

    # --------------------------------------------------------
    # NATURE
    # --------------------------------------------------------

    print()
    print("Fetching Nature...")

    nature_feed = fetch_feed(
        JOURNALS[0]
    )

    nature_count = 0

    for paper in nature_feed:
        classified = (
            classify_nature_article(
                paper
            )
        )

        if not classified:
            continue

        targets.append(classified)
        nature_count += 1

    print(
        "Nature target Articles:",
        nature_count,
    )

    # --------------------------------------------------------
    # SCIENCE
    # --------------------------------------------------------

    print()
    print("Fetching Science...")

    science_feed = fetch_feed(
        JOURNALS[1]
    )

    science_count = 0

    for paper in science_feed:
        if not science_is_article(
            paper
        ):
            continue

        result = dict(paper)

        result["article_type"] = (
            "Research Article"
        )

        targets.append(result)
        science_count += 1

    print(
        "Science target Research Articles:",
        science_count,
    )

    # --------------------------------------------------------
    # CELL
    # --------------------------------------------------------

    print()
    print("Fetching Cell...")

    cell_feed = fetch_feed(
        JOURNALS[2]
    )

    cell_count = 0

    for paper in cell_feed:
        if not cell_is_article(
            paper
        ):
            continue

        result = dict(paper)

        result["article_type"] = (
            "Article"
        )

        targets.append(result)
        cell_count += 1

    print(
        "Cell target Articles:",
        cell_count,
    )

    print()
    print(
        "Total target papers visible:",
        len(targets),
    )

    return targets


# ============================================================
# STATE
# ============================================================

def default_state():
    return {
        "version": 1,
        "initialized": False,
        "last_successful_run": None,
        "seen_papers": {},
    }


def load_state():
    if not os.path.exists(
        STATE_FILE
    ):
        print(
            "State file does not exist. "
            "Using fresh state."
        )

        return default_state()

    try:
        with open(
            STATE_FILE,
            "r",
            encoding="utf-8",
        ) as handle:
            state = json.load(handle)

    except Exception as exc:
        raise RuntimeError(
            "Could not read agent_state.json: "
            + str(exc)
        )

    if not isinstance(
        state.get("seen_papers"),
        dict,
    ):
        state["seen_papers"] = {}

    state.setdefault(
        "version",
        1,
    )

    state.setdefault(
        "initialized",
        False,
    )

    state.setdefault(
        "last_successful_run",
        None,
    )

    return state


def state_record(paper, timestamp):
    return {
        "journal": paper.get(
            "journal",
            "",
        ),
        "title": paper.get(
            "title",
            "",
        ),
        "doi": paper.get(
            "doi",
            "",
        ),
        "url": paper.get(
            "url",
            "",
        ),
        "publication_date": paper.get(
            "date",
            "",
        ),
        "article_type": paper.get(
            "article_type",
            "",
        ),
        "seen_at": timestamp,
    }


def save_state_atomic(state):
    """
    Write a temporary file first and then replace state.

    This reduces the chance of leaving a partially written
    JSON file if the process is interrupted.
    """

    temp_file = (
        STATE_FILE + ".tmp"
    )

    with open(
        temp_file,
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            state,
            handle,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        handle.write("\n")

    os.replace(
        temp_file,
        STATE_FILE,
    )


def initialize_baseline(
    state,
    targets,
):
    """
    FIRST PRODUCTION RUN ONLY.

    Record all currently visible target papers as seen.

    Send NO historical digest.

    This prevents the first production run from emailing
    the entire existing feed history.
    """

    timestamp = utc_now_iso()

    for paper in targets:
        key = paper_key(paper)

        state["seen_papers"][key] = (
            state_record(
                paper,
                timestamp,
            )
        )

    state["initialized"] = True

    state["last_successful_run"] = (
        timestamp
    )

    save_state_atomic(state)

    return len(targets)


def unseen_papers(
    state,
    targets,
):
    seen = state.get(
        "seen_papers",
        {},
    )

    results = []

    for paper in targets:
        key = paper_key(paper)

        if key in seen:
            continue

        results.append(paper)

    return results


# ============================================================
# OPENAI PROMPT
# ============================================================

def build_analysis_prompt(paper):
    evidence_warning = ""

    if paper["quality"] in (
        "LOW",
        "MEDIUM",
    ):
        evidence_warning = """
The supplied source is incomplete.

Be especially conservative.

Do not reconstruct experimental or computational methods that are not
explicitly stated in the supplied scientific text.

If methodological information is absent, explicitly state:
"Not stated in available source."
"""

    return f"""
You are analyzing a scientific research paper for a professional
scientific literature digest.

Your task is to extract and interpret ONLY information supported by
the supplied scientific text.

============================================================
STRICT EVIDENCE RULES
============================================================

1. Never invent information.

2. Never infer a method from the paper title.

3. Never infer experimental or computational methods from general
   scientific knowledge or from what would normally be done in the field.

4. Report a method only when it is explicitly stated or directly
   described in the supplied scientific text.

5. If a method is not stated, write:
   "Not stated in available source."

6. Clearly distinguish:
   - what the authors did,
   - what the authors found,
   - the broader conceptual significance.

7. Do not exaggerate novelty.

8. Do not automatically treat a named framework, algorithm, platform,
   technique, assay, or workflow as a methodological innovation.

9. Report methodological innovation only when the supplied scientific
   text supports a specific new methodological capability, design,
   platform, workflow, technical advance, or substantial technical
   improvement.

10. If methodological innovation is not sufficiently supported, write:
    "Not stated in available source."

11. Evidence limitations are mandatory.

12. Do not claim study limitations that are not stated in the supplied
    text. Instead, describe limitations of the AVAILABLE SOURCE, such as
    missing sample sizes, experimental details, statistics, controls,
    validation procedures, or methodological details.

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
OUTPUT REQUIREMENTS
============================================================

Return ONLY valid JSON.

Write the entire analysis in English.

Use clear, concise, professional scientific English suitable for
a researcher reading a scientific literature digest.

Be precise rather than verbose.

Preserve standard scientific terminology, gene names, protein names,
method names, algorithm names, instrument names, dataset names,
species names, and other specialist terminology in their conventional
English forms.

Do not translate the analysis into Chinese or any other language.

For "research_question":
- State the central scientific question or problem addressed.
- Do not add hypotheses not supported by the supplied text.

For "study_system":
- Identify the organisms, cells, tissues, materials, datasets,
  experimental systems, computational systems, or other study systems
  explicitly described.
- If the source does not state them, say:
  "Not stated in available source."

For "methods":
- Include only methods explicitly supported by the supplied text.
- Give the purpose of each method.
- Do not convert a biological result into a method.
- Do not infer standard procedures that are not mentioned.

If no methods are explicitly supported, return:

[
  {{
    "method": "Not stated in available source.",
    "purpose": "Not stated in available source."
  }}
]

For "key_findings":
- Report the principal findings supported by the supplied text.
- Preserve important quantitative results when explicitly stated.

For "conceptual_innovation":
- Explain the new conceptual insight or scientific interpretation
  supported by the text.
- Do not exaggerate novelty.

For "methodological_innovation":
- Report only explicitly supported methodological or technical advances.
- A named method alone is not sufficient evidence of methodological
  innovation.
- If not sufficiently supported, write:
  "Not stated in available source."

For "why_it_matters":
- Explain the scientific importance or potential significance supported
  by the supplied text.
- Avoid unsupported clinical, technological, agricultural, or societal
  extrapolation.

For "evidence_limitations":
- Describe what cannot be confidently assessed from the available
  scientific text.
- Distinguish limitations of the supplied source from limitations of
  the actual study.

Use exactly this JSON structure:

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
""".strip()


# ============================================================
# OPENAI
# ============================================================

def call_openai(paper):
    api_key = os.environ.get(
        "OPENAI_API_KEY",
        "",
    ).strip()

    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is missing."
        )

    schema = {
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
    }

    payload = {
        "model": OPENAI_MODEL,
        "input": build_analysis_prompt(
            paper
        ),
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
                "schema": schema,
            }
        },
    }

    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(
            payload
        ).encode("utf-8"),
        headers={
            "Authorization": (
                "Bearer " + api_key
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
        timeout=OPENAI_TIMEOUT,
        context=context,
    ) as response:
        raw = response.read()

    data = json.loads(
        raw.decode("utf-8")
    )

    output_text = ""

    for item in data.get(
        "output",
        [],
    ):
        if item.get("type") != "message":
            continue

        for content in item.get(
            "content",
            [],
        ):
            if (
                content.get("type")
                == "output_text"
            ):
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
# HTML EMAIL
# ============================================================

def esc(value):
    return html.escape(
        str(value or "")
    )


def quality_description(quality):
    if quality == "HIGH":
        return (
            "HIGH — scientific abstract/text "
            "retrieved from an authoritative source"
        )

    if quality == "MEDIUM":
        return (
            "MEDIUM — extended RSS scientific summary"
        )

    if quality == "LOW":
        return (
            "LOW — limited RSS summary; "
            "methodological detail may be incomplete"
        )

    return (
        "NONE — insufficient scientific text"
    )


def analysis_html(
    paper,
    analysis,
):
    methods = ""

    for method in analysis["methods"]:
        methods += f"""
        <li style="
            margin-bottom:10px;
        ">
          <strong>
            {esc(method["method"])}
          </strong>
          <br>
          <span style="
              color:#555555;
          ">
            Purpose:
            {esc(method["purpose"])}
          </span>
        </li>
        """

    findings = ""

    for finding in analysis[
        "key_findings"
    ]:
        findings += f"""
        <li style="
            margin-bottom:8px;
        ">
          {esc(finding)}
        </li>
        """

    doi_html = (
        esc(paper["doi"])
        if paper.get("doi")
        else "Unavailable"
    )

    return f"""
    <div style="
        border:1px solid #dddddd;
        border-radius:12px;
        padding:24px;
        margin:0 0 28px 0;
        background:#ffffff;
    ">

      <div style="
          font-size:13px;
          font-weight:700;
          letter-spacing:0.5px;
          margin-bottom:8px;
      ">
        {esc(paper["journal"])}
        ·
        {esc(paper["article_type"])}
      </div>

      <h2 style="
          margin:0 0 12px 0;
          font-size:22px;
          line-height:1.35;
      ">
        {esc(paper["title"])}
      </h2>

      <div style="
          font-size:13px;
          color:#666666;
          margin-bottom:18px;
          line-height:1.7;
      ">
        Publication date:
        {esc(paper["date"])}
        <br>

        DOI:
        {doi_html}
        <br>

        Scientific source:
        {esc(paper["source"])}
        <br>

        Evidence:
        <strong>
          {esc(
              quality_description(
                  paper["quality"]
              )
          )}
        </strong>
      </div>

      <p>
        <a href="{esc(paper["url"])}">
          Open publisher article
        </a>
      </p>

      <hr style="
          border:none;
          border-top:1px solid #eeeeee;
          margin:22px 0;
      ">

      <h3>Research question</h3>

      <p style="
          line-height:1.65;
      ">
        {esc(
            analysis[
                "research_question"
            ]
        )}
      </p>

      <h3>Study system</h3>

      <p style="
          line-height:1.65;
      ">
        {esc(
            analysis[
                "study_system"
            ]
        )}
      </p>

      <h3>Methods</h3>

      <ol style="
          line-height:1.55;
      ">
        {methods}
      </ol>

      <h3>Key findings</h3>

      <ul style="
          line-height:1.55;
      ">
        {findings}
      </ul>

      <h3>Conceptual innovation</h3>

      <p style="
          line-height:1.65;
      ">
        {esc(
            analysis[
                "conceptual_innovation"
            ]
        )}
      </p>

      <h3>Methodological innovation</h3>

      <p style="
          line-height:1.65;
      ">
        {esc(
            analysis[
                "methodological_innovation"
            ]
        )}
      </p>

      <h3>Why it matters</h3>

      <p style="
          line-height:1.65;
      ">
        {esc(
            analysis[
                "why_it_matters"
            ]
        )}
      </p>

      <h3>Evidence limitations</h3>

      <p style="
          line-height:1.65;
      ">
        {esc(
            analysis[
                "evidence_limitations"
            ]
        )}
      </p>

    </div>
    """


def build_email_html(results):
    now = datetime.now(
        timezone.utc
    )

    date_label = (
        now.date().isoformat()
    )

    cards = ""

    journal_counts = {
        "Nature": 0,
        "Science": 0,
        "Cell": 0,
    }

    for paper, analysis in results:
        journal_counts[
            paper["journal"]
        ] += 1

        cards += analysis_html(
            paper,
            analysis,
        )

    return f"""
    <!doctype html>

    <html>

    <body style="
        margin:0;
        padding:0;
        background:#f5f5f5;
        font-family:
          Arial,
          Helvetica,
          sans-serif;
        color:#222222;
    ">

      <div style="
          max-width:820px;
          margin:0 auto;
          padding:32px 18px;
      ">

        <div style="
            margin-bottom:28px;
        ">

          <h1 style="
              margin:0 0 8px 0;
              font-size:30px;
          ">
            CNS Research Digest
          </h1>

          <div style="
              color:#666666;
              font-size:14px;
              line-height:1.7;
          ">
            Nature · Science · Cell
            <br>

            {date_label}
            &nbsp;·&nbsp;
            {len(results)} new paper(s)
            <br>

            Nature:
            {journal_counts["Nature"]}
            &nbsp;|&nbsp;

            Science:
            {journal_counts["Science"]}
            &nbsp;|&nbsp;

            Cell:
            {journal_counts["Cell"]}
          </div>

        </div>

        {cards}

        <div style="
            color:#777777;
            font-size:12px;
            line-height:1.6;
            margin-top:20px;
        ">
          AI analysis is based only on scientific text
          retrieved by the agent.

          Evidence quality reflects the provenance and
          completeness of the available scientific text.

          LOW-evidence summaries may not contain sufficient
          methodological detail.
        </div>

      </div>

    </body>

    </html>
    """


# ============================================================
# EMAIL
# ============================================================

def send_email(results):
    password = os.environ.get(
        "GMAIL_APP_PASSWORD",
        "",
    ).strip()

    if not password:
        raise RuntimeError(
            "GMAIL_APP_PASSWORD is missing."
        )

    msg = EmailMessage()

    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    today = datetime.now(
        timezone.utc
    ).date().isoformat()

    msg["Subject"] = (
        "CNS Research Digest"
        f" — {today}"
        f" — {len(results)} new paper(s)"
    )

    msg.set_content(
        (
            "CNS Research Digest\n\n"
            f"{len(results)} new paper(s).\n\n"
            "Please view the HTML version "
            "for the full analysis."
        )
    )

    msg.add_alternative(
        build_email_html(results),
        subtype="html",
    )

    context = (
        ssl.create_default_context()
    )

    with smtplib.SMTP_SSL(
        SMTP_HOST,
        SMTP_PORT,
        context=context,
    ) as server:
        server.login(
            EMAIL_FROM,
            password,
        )

        server.send_message(msg)


# ============================================================
# PROCESS NEW PAPERS
# ============================================================

def process_new_papers(papers):
    results = []

    total = len(papers)

    print()
    print("=" * 78)
    print("SCIENTIFIC TEXT + OPENAI")
    print("=" * 78)

    for index, paper in enumerate(
        papers,
        start=1,
    ):
        print()
        print(
            f"[{index}/{total}]",
            paper["journal"],
            "|",
            paper["title"],
        )

        cached_page = paper.get(
            "_cached_page"
        )

        enriched = (
            enrich_scientific_text(
                paper,
                cached_page=cached_page,
            )
        )

        print(
            "Source:",
            enriched["source"],
        )

        print(
            "Evidence:",
            enriched["quality"],
        )

        print(
            "Scientific text chars:",
            len(
                enriched[
                    "scientific_text"
                ]
            ),
        )

        if (
            enriched["quality"]
            == "NONE"
        ):
            print(
                "SKIP: insufficient "
                "scientific text."
            )

            continue

        print(
            "Running OpenAI..."
        )

        analysis = call_openai(
            enriched
        )

        print(
            "OpenAI: SUCCESS"
        )

        results.append(
            (
                enriched,
                analysis,
            )
        )

    return results


# ============================================================
# UPDATE STATE AFTER SUCCESSFUL EMAIL
# ============================================================

def update_state_after_email(
    state,
    results,
):
    timestamp = utc_now_iso()

    for paper, _analysis in results:
        key = paper_key(paper)

        state["seen_papers"][key] = (
            state_record(
                paper,
                timestamp,
            )
        )

    state["last_successful_run"] = (
        timestamp
    )

    state["initialized"] = True

    save_state_atomic(state)


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 78)

    print(
        "CNS ARTICLE AGENT V6 "
        "— PRODUCTION"
    )

    print("=" * 78)

    print()
    print(
        "Version :",
        VERSION,
    )

    print(
        "OpenAI  : ON"
    )

    print(
        "Language: ENGLISH"
    )

    print(
        "Email   : ON"
    )

    print(
        "Dedup   : ON"
    )

    print(
        "State   : ON"
    )

    print(
        "Date cutoff: OFF"
    )

    print()

    # --------------------------------------------------------
    # LOAD STATE
    # --------------------------------------------------------

    state = load_state()

    print(
        "State initialized:",
        state["initialized"],
    )

    print(
        "Seen papers:",
        len(
            state["seen_papers"]
        ),
    )

    print(
        "Last successful run:",
        state["last_successful_run"],
    )

    # --------------------------------------------------------
    # DISCOVER CURRENT TARGET PAPERS
    # --------------------------------------------------------

    targets = (
        discover_target_articles()
    )

    # --------------------------------------------------------
    # FIRST RUN = BASELINE
    # --------------------------------------------------------

    if not state["initialized"]:
        print()
        print("=" * 78)
        print("FIRST PRODUCTION RUN")
        print("=" * 78)

        print()
        print(
            "Creating baseline."
        )

        print(
            "Current feed papers will be "
            "recorded as seen."
        )

        print(
            "NO historical digest email "
            "will be sent."
        )

        count = initialize_baseline(
            state,
            targets,
        )

        print()
        print(
            "Baseline papers recorded:",
            count,
        )

        print()
        print("=" * 78)

        print(
            "BASELINE INITIALIZATION SUCCESS"
        )

        print("=" * 78)

        return

    # --------------------------------------------------------
    # FIND UNSEEN PAPERS
    # --------------------------------------------------------

    new_papers = unseen_papers(
        state,
        targets,
    )

    print()
    print("=" * 78)
    print("DEDUP")
    print("=" * 78)

    print()
    print(
        "Target papers currently visible:",
        len(targets),
    )

    print(
        "New unseen papers:",
        len(new_papers),
    )

    # --------------------------------------------------------
    # NOTHING NEW
    # --------------------------------------------------------

    if not new_papers:
        print()
        print(
            "No new target papers."
        )

        print(
            "No OpenAI calls."
        )

        print(
            "No email sent."
        )

        print(
            "State unchanged."
        )

        print()
        print("=" * 78)
        print("V6 FINISHED — NOTHING NEW")
        print("=" * 78)

        return

    # --------------------------------------------------------
    # PRINT NEW PAPERS
    # --------------------------------------------------------

    print()
    print("NEW PAPERS:")

    for index, paper in enumerate(
        new_papers,
        start=1,
    ):
        print(
            index,
            paper["journal"],
            "|",
            paper["article_type"],
            "|",
            paper["title"],
            "|",
            paper["doi"],
        )

    # --------------------------------------------------------
    # SCIENTIFIC TEXT + AI
    # --------------------------------------------------------

    results = process_new_papers(
        new_papers
    )

    print()
    print("=" * 78)
    print("ANALYSIS SUMMARY")
    print("=" * 78)

    print()
    print(
        "New papers discovered:",
        len(new_papers),
    )

    print(
        "Papers successfully analyzed:",
        len(results),
    )

    # --------------------------------------------------------
    # IMPORTANT SAFETY RULE
    # --------------------------------------------------------

    if len(results) != len(
        new_papers
    ):
        raise RuntimeError(
            "Not all new papers were successfully "
            "analyzed. Email and state update aborted. "
            "No paper will be marked as seen."
        )

    # --------------------------------------------------------
    # EMAIL
    # --------------------------------------------------------

    print()
    print("=" * 78)
    print("SENDING EMAIL")
    print("=" * 78)

    send_email(results)

    print()
    print(
        "EMAIL: SUCCESS"
    )

    # --------------------------------------------------------
    # ONLY NOW UPDATE STATE
    # --------------------------------------------------------

    print()
    print("=" * 78)
    print("UPDATING STATE")
    print("=" * 78)

    update_state_after_email(
        state,
        results,
    )

    print()
    print(
        "STATE UPDATE: SUCCESS"
    )

    print(
        "Papers marked as seen:",
        len(results),
    )

    print()
    print("=" * 78)

    print(
        "V6 PRODUCTION RUN SUCCESS"
    )

    print("=" * 78)


if __name__ == "__main__":
    main()
