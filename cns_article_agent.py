#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CNS Article Agent V5.1 — English Email Test

Pipeline:
RSS
 -> exact article-type filtering
 -> scientific-text retrieval
 -> OpenAI analysis in English
 -> HTML email

TEST MODE:
- Nature:  1 HIGH paper
- Science: 1 HIGH paper
- Cell:    1 HIGH paper

Email: ON
OpenAI: ON
Dedup: OFF
State: OFF
Schedule: OFF
"""

import os
import re
import html
import json
import ssl
import smtplib
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parsedate_to_datetime


# ============================================================
# SETTINGS
# ============================================================

TIMEOUT = 30
MAX_FEED_ITEMS = 500

OPENAI_MODEL = "gpt-5.6-luna"

# ============================================================
# IMPORTANT — USE YOUR GMAIL ADDRESS
# ============================================================

EMAIL_FROM = "zhangmiao092@gmail.com"
EMAIL_TO = "miao.zhang@universite-paris-saclay.fr"

# ============================================================

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465

UA = (
    "Mozilla/5.0 "
    "(compatible; CNSArticleAgent/5.1; +https://github.com/)"
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
    names = {x.lower() for x in names}

    for child in list(element):
        if local_name(child.tag) in names:
            text = clean(
                "".join(child.itertext())
            )

            if text:
                return text

    return ""


def descendant_texts(element, names):
    names = {x.lower() for x in names}
    values = []

    for child in element.iter():
        if local_name(child.tag) in names:
            text = clean(
                "".join(child.itertext())
            )

            if text and text not in values:
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

        papers.append(
            {
                "journal": journal["name"],
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

        xml_bytes, _, _ = request_bytes(
            fetch_url,
            accept=(
                "application/xml,"
                "text/xml"
            ),
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
# ARTICLE FILTERING
# ============================================================

def normalized_types(paper):
    return [
        clean(value).lower()
        for value in paper["feed_types"]
    ]


def science_is_article(paper):
    return (
        "research article"
        in normalized_types(paper)
    )


def cell_is_article(paper):
    return (
        "article"
        in normalized_types(paper)
    )


def nature_article(paper):
    if not (
        paper["doi"]
        .lower()
        .startswith(
            "10.1038/s41586-"
        )
    ):
        return None

    try:
        page, _, _ = request_text(
            paper["url"]
        )

    except Exception:
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

    text, source = official_abstract(
        page
    )

    if not text:
        return None

    result = dict(paper)

    result.update(
        {
            "article_type": "Article",
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
    # Official publisher page
    try:
        page, _, _ = request_text(
            paper["url"]
        )

        text, source = official_abstract(
            page
        )

        if text:
            result = dict(paper)

            result.update(
                {
                    "article_type": article_type,
                    "scientific_text": text,
                    "source": source,
                    "quality": "HIGH",
                }
            )

            return result

    except Exception:
        pass

    # PubMed
    text = pubmed_abstract_by_doi(
        paper["doi"]
    )

    if len(text) >= 100:
        result = dict(paper)

        result.update(
            {
                "article_type": article_type,
                "scientific_text": text,
                "source": "PUBMED",
                "quality": "HIGH",
            }
        )

        return result

    # Crossref
    text = crossref_abstract_by_doi(
        paper["doi"]
    )

    if len(text) >= 100:
        result = dict(paper)

        result.update(
            {
                "article_type": article_type,
                "scientific_text": text,
                "source": "CROSSREF",
                "quality": "HIGH",
            }
        )

        return result

    # RSS
    text = clean(
        paper["feed_description"]
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
            "article_type": article_type,
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
# SELECT THREE TEST PAPERS
# ============================================================

def get_test_papers():
    selected = []

    # Nature — one HIGH
    nature_papers = fetch_feed(
        JOURNALS[0]
    )

    for paper in nature_papers:
        result = nature_article(
            paper
        )

        if (
            result
            and result["quality"] == "HIGH"
        ):
            selected.append(result)
            break

    # Science — one HIGH
    science_papers = fetch_feed(
        JOURNALS[1]
    )

    for paper in science_papers:
        if not science_is_article(
            paper
        ):
            continue

        result = enrich_non_nature(
            paper,
            "Research Article",
        )

        if result["quality"] == "HIGH":
            selected.append(result)
            break

    # Cell — one HIGH
    cell_papers = fetch_feed(
        JOURNALS[2]
    )

    cell_papers = [
        paper
        for paper in cell_papers
        if cell_is_article(paper)
    ]

    cell_papers.sort(
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

    for paper in cell_papers:
        result = enrich_non_nature(
            paper,
            "Article",
        )

        if result["quality"] == "HIGH":
            selected.append(result)
            break

    return selected


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
                "name": "scientific_analysis",
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
        timeout=120,
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

    return json.loads(output_text)


# ============================================================
# HTML EMAIL
# ============================================================

def esc(value):
    return html.escape(
        str(value or "")
    )


def analysis_html(
    paper,
    analysis,
):
    methods = ""

    for method in analysis["methods"]:
        methods += f"""
        <li style="margin-bottom:10px;">
          <strong>{esc(method["method"])}</strong><br>
          <span style="color:#555555;">
            Purpose: {esc(method["purpose"])}
          </span>
        </li>
        """

    findings = ""

    for finding in analysis[
        "key_findings"
    ]:
        findings += (
            "<li style='margin-bottom:8px;'>"
            + esc(finding)
            + "</li>"
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
        · {esc(paper["article_type"])}
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
        Date: {esc(paper["date"])}<br>
        DOI: {esc(paper["doi"])}<br>
        Scientific source: {esc(paper["source"])}<br>
        Evidence: <strong>{esc(paper["quality"])}</strong>
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
      <p style="line-height:1.65;">
        {esc(analysis["research_question"])}
      </p>

      <h3>Study system</h3>
      <p style="line-height:1.65;">
        {esc(analysis["study_system"])}
      </p>

      <h3>Methods</h3>
      <ol style="line-height:1.55;">
        {methods}
      </ol>

      <h3>Key findings</h3>
      <ul style="line-height:1.55;">
        {findings}
      </ul>

      <h3>Conceptual innovation</h3>
      <p style="line-height:1.65;">
        {esc(analysis["conceptual_innovation"])}
      </p>

      <h3>Methodological innovation</h3>
      <p style="line-height:1.65;">
        {esc(analysis["methodological_innovation"])}
      </p>

      <h3>Why it matters</h3>
      <p style="line-height:1.65;">
        {esc(analysis["why_it_matters"])}
      </p>

      <h3>Evidence limitations</h3>
      <p style="line-height:1.65;">
        {esc(analysis["evidence_limitations"])}
      </p>

    </div>
    """


def build_email_html(results):
    today = datetime.now(
        timezone.utc
    ).date().isoformat()

    cards = ""

    for paper, analysis in results:
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
          ">
            Nature · Science · Cell
            &nbsp;|&nbsp;
            V5.1 English Email Test
            &nbsp;|&nbsp;
            {today}
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
          retrieved by the agent. Evidence quality reflects
          the completeness and provenance of the available
          scientific text. LOW-evidence summaries may not
          contain sufficient methodological detail.
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

    if (
        "YOUR_GMAIL" in EMAIL_FROM
        or "YOUR_GMAIL" in EMAIL_TO
    ):
        raise RuntimeError(
            "Replace EMAIL_FROM and EMAIL_TO "
            "with your Gmail address."
        )

    msg = EmailMessage()

    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    msg["Subject"] = (
        "[TEST] CNS Research Digest "
        f"— {len(results)} papers"
    )

    msg.set_content(
        (
            "CNS Research Digest English test email.\n\n"
            "Please view the HTML version."
        )
    )

    msg.add_alternative(
        build_email_html(results),
        subtype="html",
    )

    context = ssl.create_default_context()

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
# MAIN
# ============================================================

def main():
    print("=" * 78)

    print(
        "CNS ARTICLE AGENT V5.1 "
        "— ENGLISH EMAIL TEST"
    )

    print("=" * 78)

    print()
    print("OpenAI : ON")
    print("Language: ENGLISH")
    print("Email  : ON")
    print("Dedup  : OFF")
    print("State  : OFF")
    print("Schedule: OFF")
    print("Test papers: 3")

    print()

    print(
        "Retrieving one HIGH-evidence "
        "paper per journal..."
    )

    papers = get_test_papers()

    if len(papers) != 3:
        raise RuntimeError(
            "Expected exactly 3 papers, "
            f"but selected {len(papers)}."
        )

    print()
    print("=" * 78)
    print("SELECTED PAPERS")
    print("=" * 78)

    for index, paper in enumerate(
        papers,
        start=1,
    ):
        print(
            index,
            paper["journal"],
            "|",
            paper["quality"],
            "|",
            paper["source"],
            "|",
            paper["title"],
        )

    results = []

    print()
    print("=" * 78)
    print("OPENAI ANALYSIS")
    print("=" * 78)

    for index, paper in enumerate(
        papers,
        start=1,
    ):
        print()

        print(
            f"Analyzing {index}/3:",
            paper["journal"],
            "-",
            paper["title"],
        )

        analysis = call_openai(
            paper
        )

        results.append(
            (
                paper,
                analysis,
            )
        )

        print(
            f"AI {index}/3: SUCCESS"
        )

    print()
    print("=" * 78)
    print("SENDING EMAIL")
    print("=" * 78)

    send_email(results)

    print()
    print("EMAIL: SUCCESS")

    print()
    print("=" * 78)

    print(
        "V5.1 ENGLISH EMAIL TEST SUCCESS"
    )

    print("=" * 78)

    print()
    print(
        "No state file was changed."
    )

    print(
        "No papers were marked as seen."
    )

    print(
        "No schedule is active."
    )


if __name__ == "__main__":
    main()
