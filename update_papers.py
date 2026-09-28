"""
GFAP Atlas Weekly Literature Agent
==================================

Searches PubMed for recently published literature involving:
- GFAP
- glial fibrillary acidic protein
- Alexander disease

The script:
1. Searches PubMed.
2. Downloads publication metadata and abstracts.
3. Scores papers for relevance to GFAP Atlas.
4. Removes papers already logged in previous weeks.
5. Creates or refreshes the current week's entry.
6. Saves everything to papers.json.

No third-party Python packages are required.
No API key is required for this low-volume weekly workflow.
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from datetime import datetime, timedelta, timezone
from pathlib import Path


# ============================================================
# CONFIGURATION
# ============================================================

DATA_FILE = Path("papers.json")

# Look back two weeks each time.
# This helps catch papers that PubMed indexes a few days late.
LOOKBACK_DAYS = 14

# NCBI recommends POST for more than about 200 IDs.
# Keeping this at 200 or below lets this simple script use GET.
MAX_RESULTS = 200

PUBMED_ESEARCH = (
    "https://eutils.ncbi.nlm.nih.gov/"
    "entrez/eutils/esearch.fcgi"
)

PUBMED_EFETCH = (
    "https://eutils.ncbi.nlm.nih.gov/"
    "entrez/eutils/efetch.fcgi"
)

# Search Title/Abstract rather than every PubMed field.
SEARCH_QUERY = (
    '('
    '"glial fibrillary acidic protein"[Title/Abstract] '
    'OR GFAP[Title/Abstract] '
    'OR "Alexander disease"[Title/Abstract]'
    ')'
)

USER_AGENT = (
    "GFAP-Literature-Agent/1.0"
)


# ============================================================
# BASIC HELPERS
# ============================================================

def clean_text(value):
    """
    Convert text to a clean single-line string.
    """

    if value is None:
        return ""

    text = str(value)

    # Remove simple HTML tags if any appear.
    text = re.sub(
        r"<[^>]+>",
        " ",
        text,
    )

    # Collapse whitespace.
    text = " ".join(
        text.split()
    )

    return text.strip()


def element_text(element):
    """
    Safely extract all text contained inside an XML element.

    This is useful because PubMed titles and abstracts can
    contain nested XML formatting.
    """

    if element is None:
        return ""

    return clean_text(
        "".join(
            element.itertext()
        )
    )


def normalize_doi(doi):
    """
    Convert DOI variants into one consistent format.
    """

    doi = clean_text(
        doi
    ).lower()

    if not doi:
        return ""

    prefixes = [
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
        "doi:",
    ]

    for prefix in prefixes:

        if doi.startswith(
            prefix
        ):

            doi = doi[
                len(prefix):
            ]

            break

    return doi.strip()


# ============================================================
# NETWORK REQUEST
# ============================================================

def request_url(
    url,
    retries=5,
):
    """
    Request a URL from PubMed.

    Temporary server or network errors are retried
    automatically using progressively longer delays.
    """

    retryable_http_codes = {
        429,
        500,
        502,
        503,
        504,
    }

    delays = [
        3,
        6,
        12,
        24,
    ]

    for attempt in range(
        retries
    ):

        request = (
            urllib.request.Request(
                url,
                headers={
                    "User-Agent": (
                        USER_AGENT
                    ),
                    "Accept": "*/*",
                },
            )
        )

        try:

            with (
                urllib.request.urlopen(
                    request,
                    timeout=60,
                )
                as response
            ):

                return (
                    response.read()
                )

        except (
            urllib.error.HTTPError
        ) as error:

            final_attempt = (
                attempt
                == retries - 1
            )

            if (
                error.code
                in retryable_http_codes
                and not final_attempt
            ):

                delay = delays[
                    min(
                        attempt,
                        len(delays) - 1,
                    )
                ]

                print(
                    f"PubMed returned "
                    f"HTTP {error.code}."
                )

                print(
                    f"Retrying in "
                    f"{delay} seconds..."
                )

                time.sleep(
                    delay
                )

                continue

            raise

        except (
            urllib.error.URLError,
            TimeoutError,
        ) as error:

            final_attempt = (
                attempt
                == retries - 1
            )

            if not final_attempt:

                delay = delays[
                    min(
                        attempt,
                        len(delays) - 1,
                    )
                ]

                print(
                    "Temporary network "
                    f"problem: {error}"
                )

                print(
                    f"Retrying in "
                    f"{delay} seconds..."
                )

                time.sleep(
                    delay
                )

                continue

            raise

    raise RuntimeError(
        "PubMed request failed "
        "after all retry attempts."
    )


# ============================================================
# PUBMED SEARCH
# ============================================================

def search_pubmed():
    """
    Search PubMed and return a list of PMIDs.
    """

    today = (
        datetime.now(
            timezone.utc
        ).date()
    )

    start_date = (
        today
        - timedelta(
            days=LOOKBACK_DAYS
        )
    )

    parameters = {
        "db": "pubmed",
        "term": SEARCH_QUERY,
        "mindate": (
            start_date.strftime(
                "%Y/%m/%d"
            )
        ),
        "maxdate": (
            today.strftime(
                "%Y/%m/%d"
            )
        ),
        "datetype": "pdat",
        "retmax": MAX_RESULTS,
        "retmode": "json",
        "sort": "pub_date",
        "tool": (
            "gfap_literature_agent"
        ),
    }

    url = (
        PUBMED_ESEARCH
        + "?"
        + urllib.parse.urlencode(
            parameters
        )
    )

    print(
        "Searching PubMed..."
    )

    raw_response = (
        request_url(
            url
        )
    )

    try:

        data = json.loads(
            raw_response.decode(
                "utf-8"
            )
        )

    except Exception as error:

        raise RuntimeError(
            "PubMed search returned "
            "data that could not be "
            "read as JSON."
        ) from error

    search_result = (
        data.get(
            "esearchresult",
            {},
        )
    )

    pmids = (
        search_result.get(
            "idlist",
            [],
        )
    )

    if not isinstance(
        pmids,
        list,
    ):

        raise RuntimeError(
            "PubMed returned an "
            "unexpected search format."
        )

    pmids = [
        clean_text(pmid)
        for pmid in pmids
        if clean_text(pmid)
    ]

    print(
        f"PubMed found "
        f"{len(pmids)} records."
    )

    return pmids


# ============================================================
# PUBMED FETCH
# ============================================================

def fetch_pubmed_records(
    pmids,
):
    """
    Retrieve complete PubMed records for the supplied PMIDs.
    """

    if not pmids:
        return []

    parameters = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
        "tool": (
            "gfap_literature_agent"
        ),
    }

    url = (
        PUBMED_EFETCH
        + "?"
        + urllib.parse.urlencode(
            parameters
        )
    )

    print(
        "Downloading PubMed "
        "article information..."
    )

    raw_xml = (
        request_url(
            url
        )
    )

    try:

        root = (
            ET.fromstring(
                raw_xml
            )
        )

    except (
        ET.ParseError
    ) as error:

        raise RuntimeError(
            "PubMed returned XML "
            "that could not be parsed."
        ) from error

    articles = []

    for article in (
        root.findall(
            ".//PubmedArticle"
        )
    ):

        try:

            parsed = (
                parse_pubmed_article(
                    article
                )
            )

        except Exception as error:

            # One unusual PubMed record should
            # not crash the entire weekly update.
            print(
                "WARNING: Skipping one "
                "PubMed record because "
                f"it could not be parsed: "
                f"{error}"
            )

            continue

        if parsed["title"]:

            articles.append(
                parsed
            )

    print(
        f"Successfully parsed "
        f"{len(articles)} papers."
    )

    return articles


# ============================================================
# PUBMED RECORD PARSING
# ============================================================

def parse_pubmed_article(
    article,
):
    """
    Convert one PubMed XML record into a Python dictionary.
    """

    # ----------------------------
    # PMID
    # ----------------------------

    pmid_element = (
        article.find(
            "./MedlineCitation/PMID"
        )
    )

    pmid = element_text(
        pmid_element
    )

    # ----------------------------
    # ARTICLE NODE
    # ----------------------------

    article_node = (
        article.find(
            "./MedlineCitation/"
            "Article"
        )
    )

    if article_node is None:

        return {
            "pmid": pmid,
            "doi": "",
            "title": "",
            "authors": "",
            "journal": "",
            "date": "",
            "abstract": "",
        }

    # ----------------------------
    # TITLE
    # ----------------------------

    title = element_text(
        article_node.find(
            "ArticleTitle"
        )
    )

    # ----------------------------
    # ABSTRACT
    # ----------------------------

    abstract_sections = []

    abstract_node = (
        article_node.find(
            "Abstract"
        )
    )

    if (
        abstract_node
        is not None
    ):

        for section in (
            abstract_node.findall(
                "AbstractText"
            )
        ):

            section_text = (
                element_text(
                    section
                )
            )

            if not section_text:
                continue

            label = clean_text(
                section.attrib.get(
                    "Label",
                    "",
                )
            )

            if label:

                abstract_sections.append(
                    f"{label}: "
                    f"{section_text}"
                )

            else:

                abstract_sections.append(
                    section_text
                )

    abstract = " ".join(
        abstract_sections
    )

    # ----------------------------
    # AUTHORS
    # ----------------------------

    authors = []

    author_list = (
        article_node.find(
            "AuthorList"
        )
    )

    if (
        author_list
        is not None
    ):

        for author in (
            author_list.findall(
                "Author"
            )
        ):

            collective = (
                clean_text(
                    author.findtext(
                        "CollectiveName"
                    )
                )
            )

            if collective:

                authors.append(
                    collective
                )

                continue

            last_name = (
                clean_text(
                    author.findtext(
                        "LastName"
                    )
                )
            )

            fore_name = (
                clean_text(
                    author.findtext(
                        "ForeName"
                    )
                )
            )

            initials = (
                clean_text(
                    author.findtext(
                        "Initials"
                    )
                )
            )

            if (
                last_name
                and fore_name
            ):

                name = (
                    f"{last_name} "
                    f"{fore_name}"
                )

            elif (
                last_name
                and initials
            ):

                name = (
                    f"{last_name} "
                    f"{initials}"
                )

            else:

                name = (
                    last_name
                    or fore_name
                    or initials
                )

            if name:

                authors.append(
                    name
                )

    author_string = (
        ", ".join(
            authors
        )
    )

    # ----------------------------
    # JOURNAL
    # ----------------------------

    journal = element_text(
        article_node.find(
            "./Journal/Title"
        )
    )

    # ----------------------------
    # DOI
    # ----------------------------

    doi = ""

    article_id_list = (
        article.find(
            "./PubmedData/"
            "ArticleIdList"
        )
    )

    if (
        article_id_list
        is not None
    ):

        for identifier in (
            article_id_list.findall(
                "ArticleId"
            )
        ):

            id_type = (
                identifier.attrib.get(
                    "IdType",
                    "",
                )
            )

            if id_type.lower() == "doi":

                doi = normalize_doi(
                    element_text(
                        identifier
                    )
                )

                break

    # ----------------------------
    # PUBLICATION DATE
    # ----------------------------

    publication_date = (
        extract_publication_date(
            article,
            article_node,
        )
    )

    return {
        "pmid": pmid,
        "doi": doi,
        "title": title,
        "authors": author_string,
        "journal": journal,
        "date": publication_date,
        "abstract": abstract,
    }


# ============================================================
# DATE EXTRACTION
# ============================================================

def extract_publication_date(
    article,
    article_node,
):
    """
    Try multiple PubMed date fields and return the
    best available publication date.
    """

    # First preference:
    # electronic publication date.

    article_date = (
        article_node.find(
            "ArticleDate"
        )
    )

    if (
        article_date
        is not None
    ):

        year = clean_text(
            article_date.findtext(
                "Year"
            )
        )

        month = clean_text(
            article_date.findtext(
                "Month"
            )
        )

        day = clean_text(
            article_date.findtext(
                "Day"
            )
        )

        if year:

            parts = [year]

            if month:
                parts.append(month)

            if day:
                parts.append(day)

            return "-".join(
                parts
            )

    # Second preference:
    # journal issue publication date.

    pub_date = (
        article_node.find(
            "./Journal/"
            "JournalIssue/"
            "PubDate"
        )
    )

    if pub_date is not None:

        year = clean_text(
            pub_date.findtext(
                "Year"
            )
        )

        month = clean_text(
            pub_date.findtext(
                "Month"
            )
        )

        day = clean_text(
            pub_date.findtext(
                "Day"
            )
        )

        medline_date = (
            clean_text(
                pub_date.findtext(
                    "MedlineDate"
                )
            )
        )

        if year:

            parts = [year]

            if month:
                parts.append(month)

            if day:
                parts.append(day)

            return " ".join(
                parts
            )

        if medline_date:

            return medline_date

    # Third preference:
    # PubMed history dates.

    history = (
        article.find(
            "./PubmedData/"
            "History"
        )
    )

    if history is not None:

        preferred_statuses = [
            "pubmed",
            "entrez",
            "medline",
        ]

        for desired_status in (
            preferred_statuses
        ):

            for date_node in (
                history.findall(
                    "PubMedPubDate"
                )
            ):

                status = (
                    date_node.attrib.get(
                        "PubStatus",
                        "",
                    )
                )

                if (
                    status
                    != desired_status
                ):
                    continue

                year = clean_text(
                    date_node.findtext(
                        "Year"
                    )
                )

                month = clean_text(
                    date_node.findtext(
                        "Month"
                    )
                )

                day = clean_text(
                    date_node.findtext(
                        "Day"
                    )
                )

                if year:

                    parts = [year]

                    if month:
                        parts.append(
                            month
                        )

                    if day:
                        parts.append(
                            day
                        )

                    return "-".join(
                        parts
                    )

    return ""


# ============================================================
# RELEVANCE SCORING
# ============================================================

def relevance_score(
    paper,
):
    """
    Score each paper for relevance to GFAP Atlas.

    Direct Alexander disease and GFAP biology receive
    the highest priority.
    """

    title = (
        paper.get(
            "title",
            ""
        ).lower()
    )

    abstract = (
        paper.get(
            "abstract",
            ""
        ).lower()
    )

    text = (
        title
        + " "
        + abstract
    )

    score = 0

    # ----------------------------
    # ALEXANDER DISEASE
    # ----------------------------

    if (
        "alexander disease"
        in text
    ):
        score += 30

    if (
        "alexander disease"
        in title
    ):
        score += 15

    # ----------------------------
    # DIRECT GFAP FOCUS
    # ----------------------------

    if "gfap" in title:
        score += 12

    if (
        "glial fibrillary "
        "acidic protein"
        in title
    ):
        score += 12

    if "gfap" in abstract:
        score += 4

    if (
        "glial fibrillary "
        "acidic protein"
        in abstract
    ):
        score += 4

    # ----------------------------
    # VARIANTS / GENETICS
    # ----------------------------

    genetics_terms = [
        "mutation",
        "mutations",
        "variant",
        "variants",
        "missense",
        "pathogenic",
        "genotype",
        "genotypes",
    ]

    if any(
        term in text
        for term in genetics_terms
    ):
        score += 8

    # ----------------------------
    # AGGREGATION
    # ----------------------------

    aggregation_terms = [
        "aggregation",
        "aggregates",
        "aggregate",
        "rosenthal fiber",
        "rosenthal fibers",
    ]

    if any(
        term in text
        for term in aggregation_terms
    ):
        score += 8

    # ----------------------------
    # INTERMEDIATE FILAMENTS
    # ----------------------------

    if (
        "intermediate filament"
        in text
    ):
        score += 7

    # ----------------------------
    # ASTROCYTES
    # ----------------------------

    if any(
        term in text
        for term in [
            "astrocyte",
            "astrocytes",
            "astrocytic",
        ]
    ):
        score += 3

    # ----------------------------
    # BIOMARKERS
    # ----------------------------

    if "biomarker" in text:
        score += 2

    # ----------------------------
    # GENERIC STAINING PENALTY
    # ----------------------------

    generic_terms = [
        "immunohistochemistry",
        "immunofluorescence",
        "immunostaining",
    ]

    direct_title_focus = (
        "gfap" in title
        or
        "glial fibrillary "
        "acidic protein"
        in title
        or
        "alexander disease"
        in title
    )

    if (
        not direct_title_focus
        and any(
            term in text
            for term in generic_terms
        )
    ):
        score -= 3

    return score


# ============================================================
# TOPIC LABELS
# ============================================================

def determine_topics(
    paper,
):
    """
    Assign useful GFAP Atlas topic labels.
    """

    text = (
        paper.get(
            "title",
            ""
        )
        + " "
        + paper.get(
            "abstract",
            ""
        )
    ).lower()

    topics = []

    if (
        "alexander disease"
        in text
    ):

        topics.append(
            "Alexander disease"
        )

    if any(
        term in text
        for term in [
            "mutation",
            "mutations",
            "variant",
            "variants",
            "missense",
            "pathogenic",
        ]
    ):

        topics.append(
            "GFAP variants"
        )

    if any(
        term in text
        for term in [
            "aggregation",
            "aggregate",
            "aggregates",
            "rosenthal fiber",
            "rosenthal fibers",
        ]
    ):

        topics.append(
            "GFAP aggregation"
        )

    if (
        "intermediate filament"
        in text
    ):

        topics.append(
            "Intermediate filaments"
        )

    if any(
        term in text
        for term in [
            "astrocyte",
            "astrocytes",
            "astrocytic",
        ]
    ):

        topics.append(
            "Astrocytes"
        )

    if (
        "biomarker"
        in text
    ):

        topics.append(
            "Biomarker"
        )

    if not topics:

        topics.append(
            "GFAP"
        )

    return topics


# ============================================================
# ABSTRACT PREVIEW
# ============================================================

def create_summary(
    abstract,
):
    """
    Create a short preview using the real PubMed abstract.

    This is intentionally NOT an AI-generated interpretation.
    That avoids fabricated scientific claims while we establish
    the automated literature pipeline.
    """

    abstract = clean_text(
        abstract
    )

    if not abstract:

        return (
            "No abstract is currently "
            "available through PubMed."
        )

    sentences = re.split(
        r"(?<=[.!?])\s+",
        abstract,
    )

    sentences = [
        sentence.strip()
        for sentence in sentences
        if sentence.strip()
    ]

    if len(sentences) >= 2:

        summary = " ".join(
            sentences[:2]
        )

    elif sentences:

        summary = (
            sentences[0]
        )

    else:

        summary = abstract

    # Prevent very large previews.
    if len(summary) > 650:

        shortened = (
            summary[:647]
        )

        if " " in shortened:

            shortened = (
                shortened.rsplit(
                    " ",
                    1,
                )[0]
            )

        summary = (
            shortened
            + "..."
        )

    return summary


# ============================================================
# WHY IT MATTERS
# ============================================================

def why_it_matters(
    paper,
):
    """
    Generate a conservative category-level relevance note.

    This does not claim a paper found something that the
    metadata does not support.
    """

    text = (
        paper.get(
            "title",
            ""
        )
        + " "
        + paper.get(
            "abstract",
            ""
        )
    ).lower()

    if (
        "alexander disease"
        in text
    ):

        return (
            "Directly relevant to "
            "Alexander disease and "
            "GFAP-associated disease biology."
        )

    if any(
        term in text
        for term in [
            "mutation",
            "mutations",
            "variant",
            "variants",
            "missense",
            "pathogenic",
        ]
    ):

        return (
            "Relevant to GFAP variation, "
            "genetics, or potential "
            "disease-associated variants."
        )

    if any(
        term in text
        for term in [
            "aggregation",
            "aggregate",
            "aggregates",
            "rosenthal fiber",
            "rosenthal fibers",
        ]
    ):

        return (
            "Relevant to GFAP aggregation, "
            "Rosenthal fibers, or "
            "intermediate filament biology."
        )

    if (
        "intermediate filament"
        in text
    ):

        return (
            "Relevant to GFAP and "
            "intermediate filament biology."
        )

    if any(
        term in text
        for term in [
            "astrocyte",
            "astrocytes",
            "astrocytic",
        ]
    ):

        return (
            "Relevant to GFAP-associated "
            "astrocyte biology."
        )

    if (
        "biomarker"
        in text
    ):

        return (
            "Relevant to GFAP as a "
            "neurological biomarker."
        )

    return (
        "Relevant to recent research "
        "involving GFAP."
    )


# ============================================================
# ARCHIVE LOADING
# ============================================================

def load_archive():
    """
    Safely load papers.json.
    """

    if not DATA_FILE.exists():

        return {
            "last_updated": "",
            "weeks": [],
        }

    try:

        with open(
            DATA_FILE,
            "r",
            encoding="utf-8",
        ) as file:

            data = (
                json.load(
                    file
                )
            )

    except (
        json.JSONDecodeError,
        OSError,
    ) as error:

        raise RuntimeError(
            "papers.json could not "
            "be read safely. "
            "No changes were made."
        ) from error

    if not isinstance(
        data,
        dict,
    ):

        raise RuntimeError(
            "papers.json has an "
            "unexpected format."
        )

    weeks = data.get(
        "weeks"
    )

    if weeks is None:

        data["weeks"] = []

    elif not isinstance(
        weeks,
        list,
    ):

        raise RuntimeError(
            "The weeks field in "
            "papers.json is invalid."
        )

    return data


# ============================================================
# WEEK LABEL
# ============================================================

def get_week_label(
    today,
):
    """
    Weeks are labeled using Monday.

    Example:
    Week of September 28, 2026
    """

    monday = (
        today
        - timedelta(
            days=today.weekday()
        )
    )

    return (
        "Week of "
        + monday.strftime(
            "%B %d, %Y"
        )
    )


# ============================================================
# PREVIOUS PAPER IDS
# ============================================================

def get_previous_ids(
    data,
    current_week,
):
    """
    Collect identifiers from PREVIOUS weeks only.

    We intentionally ignore the current week so manually
    rerunning the workflow refreshes the current week's list.
    """

    identifiers = set()

    for week in data.get(
        "weeks",
        [],
    ):

        if (
            week.get("week")
            == current_week
        ):
            continue

        papers = week.get(
            "papers",
            [],
        )

        if not isinstance(
            papers,
            list,
        ):
            continue

        for paper in papers:

            pmid = clean_text(
                paper.get(
                    "pmid",
                    "",
                )
            )

            doi = normalize_doi(
                paper.get(
                    "doi",
                    "",
                )
            )

            if pmid:

                identifiers.add(
                    "pmid:"
                    + pmid
                )

            if doi:

                identifiers.add(
                    "doi:"
                    + doi
                )

    return identifiers


def paper_already_seen(
    paper,
    previous_ids,
):
    """
    Determine whether a paper was logged in an older week.
    """

    pmid = clean_text(
        paper.get(
            "pmid",
            "",
        )
    )

    doi = normalize_doi(
        paper.get(
            "doi",
            "",
        )
    )

    if (
        pmid
        and
        "pmid:" + pmid
        in previous_ids
    ):

        return True

    if (
        doi
        and
        "doi:" + doi
        in previous_ids
    ):

        return True

    return False


# ============================================================
# CURRENT-WEEK DEDUPLICATION
# ============================================================

def deduplicate_current_results(
    papers,
):
    """
    Remove accidental duplicates inside the current PubMed
    result set.
    """

    seen = set()
    unique = []

    for paper in papers:

        pmid = clean_text(
            paper.get(
                "pmid",
                "",
            )
        )

        doi = normalize_doi(
            paper.get(
                "doi",
                "",
            )
        )

        title = clean_text(
            paper.get(
                "title",
                "",
            )
        ).lower()

        if pmid:

            key = (
                "pmid:"
                + pmid
            )

        elif doi:

            key = (
                "doi:"
                + doi
            )

        else:

            key = (
                "title:"
                + title
            )

        if key in seen:
            continue

        seen.add(
            key
        )

        unique.append(
            paper
        )

    return unique


# ============================================================
# FINAL WEBSITE RECORD
# ============================================================

def prepare_for_archive(
    paper,
):
    """
    Convert a parsed PubMed paper into the structure that
    GFAP Atlas will eventually display.
    """

    pmid = clean_text(
        paper.get(
            "pmid",
            "",
        )
    )

    doi = normalize_doi(
        paper.get(
            "doi",
            "",
        )
    )

    if pmid:

        publication_url = (
            "https://pubmed.ncbi.nlm.nih.gov/"
            + pmid
            + "/"
        )

    elif doi:

        publication_url = (
            "https://doi.org/"
            + doi
        )

    else:

        publication_url = ""

    return {
        "title": clean_text(
            paper.get(
                "title",
                "",
            )
        ),

        "authors": clean_text(
            paper.get(
                "authors",
                "",
            )
        ),

        "journal": clean_text(
            paper.get(
                "journal",
                "",
            )
        ),

        "date": clean_text(
            paper.get(
                "date",
                "",
            )
        ),

        "pmid": pmid,

        "doi": doi,

        "url": publication_url,

        "source": "PubMed",

        "topics": (
            determine_topics(
                paper
            )
        ),

        "relevance_score": (
            relevance_score(
                paper
            )
        ),

        "summary": (
            create_summary(
                paper.get(
                    "abstract",
                    "",
                )
            )
        ),

        "why_it_matters": (
            why_it_matters(
                paper
            )
        ),
    }


# ============================================================
# SAVE ARCHIVE SAFELY
# ============================================================

def save_archive(
    data,
):
    """
    Write JSON only after all processing succeeds.
    """

    temporary_file = (
        Path(
            "papers.json.tmp"
        )
    )

    try:

        with open(
            temporary_file,
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                data,
                file,
                indent=2,
                ensure_ascii=False,
            )

            file.write(
                "\n"
            )

        # Validate the newly written JSON before replacing
        # the real archive.
        with open(
            temporary_file,
            "r",
            encoding="utf-8",
        ) as file:

            json.load(
                file
            )

        temporary_file.replace(
            DATA_FILE
        )

    except Exception:

        if (
            temporary_file.exists()
        ):

            temporary_file.unlink()

        raise


# ============================================================
# MAIN PROGRAM
# ============================================================

def main():
    """
    Run one complete GFAP literature update.
    """

    print("")
    print(
        "GFAP Atlas Literature Agent"
    )
    print(
        "==========================="
    )
    print("")

    # ----------------------------------------
    # STEP 1: SEARCH PUBMED
    # ----------------------------------------

    pmids = (
        search_pubmed()
    )

    if not pmids:

        raise RuntimeError(
            "PubMed returned no papers "
            "for the configured search. "
            "The archive was left unchanged."
        )

    # ----------------------------------------
    # STEP 2: DOWNLOAD RECORDS
    # ----------------------------------------

    papers = (
        fetch_pubmed_records(
            pmids
        )
    )

    if not papers:

        raise RuntimeError(
            "PubMed returned IDs, but "
            "no article records could "
            "be parsed. "
            "The archive was left unchanged."
        )

    # ----------------------------------------
    # STEP 3: REMOVE DUPLICATES
    # ----------------------------------------

    papers = (
        deduplicate_current_results(
            papers
        )
    )

    print(
        f"{len(papers)} unique "
        "PubMed papers available."
    )

    # ----------------------------------------
    # STEP 4: LOAD EXISTING ARCHIVE
    # ----------------------------------------

    data = (
        load_archive()
    )

    now = (
        datetime.now(
            timezone.utc
        )
    )

    today = (
        now.date()
    )

    week_label = (
        get_week_label(
            today
        )
    )

    previous_ids = (
        get_previous_ids(
            data,
            week_label,
        )
    )

    # ----------------------------------------
    # STEP 5: FILTER AND SCORE
    # ----------------------------------------

    current_week_papers = []

    for paper in papers:

        if paper_already_seen(
            paper,
            previous_ids,
        ):

            continue

        score = (
            relevance_score(
                paper
            )
        )

        # Every search result already contains GFAP or
        # Alexander disease in its title/abstract, but this
        # threshold removes especially weak incidental hits.
        if score < 3:

            continue

        current_week_papers.append(
            prepare_for_archive(
                paper
            )
        )

    # Highest-relevance papers appear first.
    current_week_papers.sort(
        key=lambda paper: (
            paper.get(
                "relevance_score",
                0,
            ),
            paper.get(
                "date",
                "",
            ),
        ),
        reverse=True,
    )

    # ----------------------------------------
    # STEP 6: CREATE CURRENT WEEK
    # ----------------------------------------

    week_entry = {
        "week": week_label,
        "generated": (
            now.isoformat()
        ),
        "paper_count": (
            len(
                current_week_papers
            )
        ),
        "papers": (
            current_week_papers
        ),
    }

    # Remove any existing copy of this same week.
    old_weeks = (
        data.get(
            "weeks",
            [],
        )
    )

    previous_weeks = [
        week
        for week in old_weeks
        if (
            week.get("week")
            != week_label
        )
    ]

    # Newest week first.
    data["weeks"] = (
        [week_entry]
        + previous_weeks
    )

    data["last_updated"] = (
        now.isoformat()
    )

    data["source"] = (
        "PubMed"
    )

    # ----------------------------------------
    # STEP 7: SAVE
    # ----------------------------------------

    save_archive(
        data
    )

    # ----------------------------------------
    # STEP 8: REPORT
    # ----------------------------------------

    print("")
    print(
        "SUCCESS"
    )
    print(
        "-------"
    )

    print(
        f"{week_label}"
    )

    print(
        f"{len(current_week_papers)} "
        "new papers added."
    )

    print(
        "papers.json was updated "
        "successfully."
    )

    if (
        current_week_papers
    ):

        print("")
        print(
            "Top papers:"
        )

        for paper in (
            current_week_papers[:10]
        ):

            print("")

            print(
                "[Score "
                + str(
                    paper[
                        "relevance_score"
                    ]
                )
                + "] "
                + paper["title"]
            )

            if paper["pmid"]:

                print(
                    "PMID: "
                    + paper["pmid"]
                )

    print("")
    print(
        "GFAP literature update "
        "completed successfully."
    )


# ============================================================
# START PROGRAM
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as error:

        print("")
        print(
            "GFAP LITERATURE UPDATE FAILED"
        )
        print(
            "============================="
        )

        print(
            str(error)
        )

        print("")
        print(
            "The existing papers.json "
            "archive was not intentionally "
            "overwritten by this failure."
        )

        raise
