import json
import re
import time
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path


# ============================================================
# GFAP ATLAS — WEEKLY LITERATURE AGENT
# ============================================================

DATA_FILE = Path("papers.json")

# Look back 14 days each week so papers that are indexed late
# are less likely to be missed.
LOOKBACK_DAYS = 14

MAX_RESULTS_PER_SOURCE = 100


# ============================================================
# DATABASE URLS
# ============================================================

PUBMED_ESEARCH = (
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
)

PUBMED_EFETCH = (
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
)

EUROPE_PMC_SEARCH = (
    "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
)


# ============================================================
# SEARCH QUERIES
# ============================================================

# PubMed search is restricted to title/abstract to reduce
# irrelevant matches.
PUBMED_QUERY = (
    '("glial fibrillary acidic protein"[Title/Abstract] '
    'OR GFAP[Title/Abstract] '
    'OR "Alexander disease"[Title/Abstract])'
)

EUROPE_PMC_QUERY = (
    '("glial fibrillary acidic protein" '
    'OR GFAP '
    'OR "Alexander disease")'
)


# ============================================================
# REQUEST SETTINGS
# ============================================================

USER_AGENT = (
    "GFAP-Literature-Agent/1.0 "
    "(GFAP Atlas literature monitor)"
)


# ============================================================
# GENERAL HELPERS
# ============================================================

def clean_text(text):
    """Remove simple HTML tags and extra whitespace."""

    if not text:
        return ""

    text = re.sub(r"<[^>]+>", " ", str(text))
    return " ".join(text.split())


def normalize_doi(doi):
    """Normalize DOI formatting for duplicate detection."""

    if not doi:
        return ""

    doi = str(doi).strip().lower()

    prefixes = [
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
        "doi:",
    ]

    for prefix in prefixes:
        if doi.startswith(prefix):
            doi = doi[len(prefix):]

    return doi.strip()


def request_url(url, retries=4, base_delay=3):
    """
    Make an HTTP request.

    Temporary failures such as 429, 500, 502, 503, and 504
    are retried automatically before giving up.
    """

    retryable_codes = {
        429,
        500,
        502,
        503,
        504,
    }

    for attempt in range(retries):

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": (
                    "application/json, "
                    "application/xml, "
                    "text/xml, */*"
                ),
            },
        )

        try:

            with urllib.request.urlopen(
                request,
                timeout=60,
            ) as response:

                return response.read()

        except urllib.error.HTTPError as error:

            if (
                error.code in retryable_codes
                and attempt < retries - 1
            ):

                delay = base_delay * (2 ** attempt)

                print(
                    f"HTTP {error.code}. "
                    f"Retrying in {delay} seconds..."
                )

                time.sleep(delay)
                continue

            raise

        except (
            urllib.error.URLError,
            TimeoutError,
        ) as error:

            if attempt < retries - 1:

                delay = base_delay * (2 ** attempt)

                print(
                    f"Network error: {error}. "
                    f"Retrying in {delay} seconds..."
                )

                time.sleep(delay)
                continue

            raise

    raise RuntimeError(
        "Request failed after all retry attempts."
    )


# ============================================================
# PUBMED
# ============================================================

def search_pubmed():
    """Search PubMed for recent GFAP literature."""

    today = datetime.now(timezone.utc).date()

    start_date = (
        today
        - timedelta(days=LOOKBACK_DAYS)
    )

    search_params = {
        "db": "pubmed",
        "term": PUBMED_QUERY,
        "mindate": start_date.strftime("%Y/%m/%d"),
        "maxdate": today.strftime("%Y/%m/%d"),
        "datetype": "pdat",
        "retmax": MAX_RESULTS_PER_SOURCE,
        "retmode": "json",
        "sort": "pub_date",
        "tool": "gfap_literature_agent",
    }

    search_url = (
        PUBMED_ESEARCH
        + "?"
        + urllib.parse.urlencode(search_params)
    )

    print("Searching PubMed...")

    raw = request_url(search_url)

    search_data = json.loads(
        raw.decode("utf-8")
    )

    pmids = (
        search_data
        .get("esearchresult", {})
        .get("idlist", [])
    )

    print(
        f"PubMed returned {len(pmids)} records."
    )

    if not pmids:
        return []

    fetch_params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
        "tool": "gfap_literature_agent",
    }

    fetch_url = (
        PUBMED_EFETCH
        + "?"
        + urllib.parse.urlencode(fetch_params)
    )

    raw_xml = request_url(fetch_url)

    root = ET.fromstring(raw_xml)

    papers = []

    for article in root.findall(
        ".//PubmedArticle"
    ):

        paper = parse_pubmed_article(
            article
        )

        if paper["title"]:
            papers.append(paper)

    return papers


def parse_pubmed_article(article):
    """Convert one PubMed record into our standard format."""

    # PMID
    pmid = ""

    pmid_element = article.find(
        ".//MedlineCitation/PMID"
    )

    if pmid_element is not None:

        pmid = clean_text(
            "".join(
                pmid_element.itertext()
            )
        )

    # Title
    title = ""

    title_element = article.find(
        ".//Article/ArticleTitle"
    )

    if title_element is not None:

        title = clean_text(
            "".join(
                title_element.itertext()
            )
        )

    # Abstract
    abstract_parts = []

    for abstract_element in article.findall(
        ".//Article/Abstract/AbstractText"
    ):

        section_text = clean_text(
            "".join(
                abstract_element.itertext()
            )
        )

        label = clean_text(
            abstract_element.attrib.get(
                "Label",
                "",
            )
        )

        if not section_text:
            continue

        if label:
            abstract_parts.append(
                f"{label}: {section_text}"
            )
        else:
            abstract_parts.append(
                section_text
            )

    abstract = " ".join(
        abstract_parts
    )

    # Authors
    authors = []

    for author in article.findall(
        ".//Article/AuthorList/Author"
    ):

        collective = author.findtext(
            "CollectiveName"
        )

        if collective:

            authors.append(
                clean_text(collective)
            )

            continue

        last_name = clean_text(
            author.findtext("LastName")
            or ""
        )

        initials = clean_text(
            author.findtext("Initials")
            or ""
        )

        name = " ".join(
            item
            for item in [
                last_name,
                initials,
            ]
            if item
        )

        if name:
            authors.append(name)

    author_string = ", ".join(
        authors
    )

    # Journal
    journal = clean_text(
        article.findtext(
            ".//Article/Journal/Title"
        )
        or ""
    )

    # DOI
    doi = ""

    for article_id in article.findall(
        ".//PubmedData/ArticleIdList/ArticleId"
    ):

        if (
            article_id.attrib.get("IdType")
            == "doi"
        ):

            doi = normalize_doi(
                article_id.text or ""
            )

            break

    # Publication date
    pub_date = extract_pubmed_date(
        article
    )

    return {
        "source": "PubMed",
        "sources": ["PubMed"],
        "pmid": pmid,
        "doi": doi,
        "title": title,
        "authors": author_string,
        "journal": journal,
        "date": pub_date,
        "abstract": abstract,
    }


def extract_pubmed_date(article):
    """Extract the best available PubMed publication date."""

    year = article.findtext(
        ".//Article/Journal/JournalIssue/PubDate/Year"
    )

    month = article.findtext(
        ".//Article/Journal/JournalIssue/PubDate/Month"
    )

    day = article.findtext(
        ".//Article/Journal/JournalIssue/PubDate/Day"
    )

    medline_date = article.findtext(
        ".//Article/Journal/JournalIssue/PubDate/MedlineDate"
    )

    if year:

        parts = [year]

        if month:
            parts.append(month)

        if day:
            parts.append(day)

        return " ".join(parts)

    if medline_date:
        return clean_text(medline_date)

    return ""


# ============================================================
# EUROPE PMC
# ============================================================

def search_europe_pmc():
    """Search Europe PMC for recent GFAP literature."""

    today = datetime.now(
        timezone.utc
    ).date()

    start_date = (
        today
        - timedelta(days=LOOKBACK_DAYS)
    )

    query = (
        f'{EUROPE_PMC_QUERY} '
        f'AND FIRST_PDATE:['
        f'{start_date.isoformat()} '
        f'TO {today.isoformat()}]'
    )

    params = {
        "query": query,
        "format": "json",
        "pageSize": MAX_RESULTS_PER_SOURCE,
        "resultType": "core",
        "sort": "FIRST_PDATE_D",
    }

    url = (
        EUROPE_PMC_SEARCH
        + "?"
        + urllib.parse.urlencode(params)
    )

    print("Searching Europe PMC...")

    raw = request_url(url)

    data = json.loads(
        raw.decode("utf-8")
    )

    results = (
        data
        .get("resultList", {})
        .get("result", [])
    )

    print(
        f"Europe PMC returned "
        f"{len(results)} records."
    )

    papers = []

    for result in results:

        paper = {
            "source": "Europe PMC",

            "sources": [
                "Europe PMC"
            ],

            "pmid": str(
                result.get("pmid", "")
                or ""
            ),

            "doi": normalize_doi(
                result.get("doi", "")
                or ""
            ),

            "title": clean_text(
                result.get("title", "")
                or ""
            ),

            "authors": clean_text(
                result.get(
                    "authorString",
                    "",
                )
                or ""
            ),

            "journal": clean_text(
                result.get(
                    "journalTitle",
                    "",
                )
                or ""
            ),

            "date": clean_text(
                result.get(
                    "firstPublicationDate",
                    "",
                )
                or ""
            ),

            "abstract": clean_text(
                result.get(
                    "abstractText",
                    "",
                )
                or ""
            ),
        }

        if paper["title"]:
            papers.append(paper)

    return papers


# ============================================================
# SAFE SEARCHING
# ============================================================

def safe_search_pubmed():
    """
    If PubMed temporarily fails, allow Europe PMC
    to continue independently.
    """

    try:

        return search_pubmed()

    except Exception as error:

        print("")
        print(
            "WARNING: PubMed search failed."
        )

        print(
            f"PubMed error: {error}"
        )

        print(
            "Continuing with Europe PMC "
            "if available."
        )
        print("")

        return []


def safe_search_europe_pmc():
    """
    If Europe PMC temporarily fails, allow PubMed
    to continue independently.
    """

    try:

        return search_europe_pmc()

    except Exception as error:

        print("")
        print(
            "WARNING: Europe PMC search failed."
        )

        print(
            f"Europe PMC error: {error}"
        )

        print(
            "Continuing with PubMed "
            "if available."
        )
        print("")

        return []


# ============================================================
# MERGING AND DUPLICATES
# ============================================================

def paper_key(paper):
    """Generate the best available identifier for a paper."""

    pmid = clean_text(
        paper.get("pmid", "")
    )

    doi = normalize_doi(
        paper.get("doi", "")
    )

    title = clean_text(
        paper.get("title", "")
    ).lower()

    if pmid:
        return "pmid:" + pmid

    if doi:
        return "doi:" + doi

    return "title:" + title


def merge_papers(
    pubmed_papers,
    europe_pmc_papers,
):
    """
    Merge PubMed and Europe PMC records.

    A paper found by both services appears only once.
    """

    merged = {}

    for paper in (
        pubmed_papers
        + europe_pmc_papers
    ):

        if not paper.get("title"):
            continue

        key = paper_key(paper)

        if key not in merged:

            merged[key] = paper.copy()

            merged[key]["sources"] = list(
                dict.fromkeys(
                    paper.get(
                        "sources",
                        [paper.get("source", "")],
                    )
                )
            )

            continue

        existing = merged[key]

        for source in paper.get(
            "sources",
            [paper.get("source", "")],
        ):

            if (
                source
                and source
                not in existing["sources"]
            ):

                existing[
                    "sources"
                ].append(source)

        # Keep whichever database has the more complete
        # value for each field.
        for field in [
            "abstract",
            "authors",
            "journal",
            "date",
            "pmid",
            "doi",
        ]:

            old_value = str(
                existing.get(field, "")
                or ""
            )

            new_value = str(
                paper.get(field, "")
                or ""
            )

            if (
                len(new_value)
                > len(old_value)
            ):

                existing[field] = (
                    new_value
                )

    return list(
        merged.values()
    )


# ============================================================
# GFAP RELEVANCE SCORING
# ============================================================

def relevance_score(paper):
    """
    Rank papers for GFAP Atlas.

    Direct Alexander disease / GFAP research ranks
    above generic biomarker or staining papers.
    """

    title = clean_text(
        paper.get("title", "")
    ).lower()

    abstract = clean_text(
        paper.get("abstract", "")
    ).lower()

    text = (
        title
        + " "
        + abstract
    )

    score = 0

    # Alexander disease — highest priority
    if "alexander disease" in text:
        score += 20

    if "alexander disease" in title:
        score += 10

    # Direct GFAP focus
    if "gfap" in title:
        score += 8

    if (
        "glial fibrillary acidic protein"
        in title
    ):
        score += 8

    if "gfap" in abstract:
        score += 3

    if (
        "glial fibrillary acidic protein"
        in abstract
    ):
        score += 3

    # Variants / genetics
    genetics_terms = [
        "mutation",
        "mutations",
        "variant",
        "variants",
        "genotype",
        "genotypes",
        "pathogenic",
        "missense",
    ]

    if any(
        term in text
        for term in genetics_terms
    ):
        score += 6

    # Aggregation
    aggregation_terms = [
        "aggregation",
        "aggregate",
        "aggregates",
        "rosenthal fiber",
        "rosenthal fibers",
    ]

    if any(
        term in text
        for term in aggregation_terms
    ):
        score += 6

    # Intermediate filament biology
    if "intermediate filament" in text:
        score += 5

    # Astrocyte biology
    if (
        "astrocyte" in text
        or "astrocytes" in text
    ):
        score += 2

    # Biomarker papers
    if "biomarker" in text:
        score += 1

    # Reduce ranking of papers where GFAP appears to
    # simply be a generic staining marker.
    generic_marker_terms = [
        "immunohistochemistry",
        "immunostaining",
        "immunofluorescence",
    ]

    if (
        "gfap" not in title
        and
        "glial fibrillary acidic protein"
        not in title
        and
        "alexander disease"
        not in title
        and
        any(
            term in text
            for term in generic_marker_terms
        )
    ):
        score -= 2

    return score


# ============================================================
# TOPICS
# ============================================================

def determine_topics(paper):

    text = (
        clean_text(
            paper.get("title", "")
        )
        + " "
        + clean_text(
            paper.get("abstract", "")
        )
    ).lower()

    topics = []

    if "alexander disease" in text:
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

    if "intermediate filament" in text:
        topics.append(
            "Intermediate filaments"
        )

    if (
        "astrocyte" in text
        or "astrocytes" in text
    ):
        topics.append(
            "Astrocytes"
        )

    if "biomarker" in text:
        topics.append(
            "Biomarker"
        )

    if not topics:
        topics.append("GFAP")

    return topics


# ============================================================
# SUMMARY
# ============================================================

def create_summary(abstract):
    """
    Use text from the real abstract for now.

    We will add true AI-generated summaries separately
    after the core literature pipeline is confirmed stable.
    """

    abstract = clean_text(
        abstract
    )

    if not abstract:

        return (
            "Abstract not available through "
            "PubMed or Europe PMC."
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

        summary = sentences[0]

    else:

        summary = abstract

    if len(summary) > 650:

        summary = (
            summary[:647]
            .rsplit(" ", 1)[0]
            + "..."
        )

    return summary


# ============================================================
# WHY IT MATTERS
# ============================================================

def why_it_matters(paper):

    text = (
        clean_text(
            paper.get("title", "")
        )
        + " "
        + clean_text(
            paper.get("abstract", "")
        )
    ).lower()

    if "alexander disease" in text:

        return (
            "Directly relevant to Alexander disease "
            "and GFAP-associated disease biology."
        )

    if any(
        term in text
        for term in [
            "mutation",
            "mutations",
            "variant",
            "variants",
            "missense",
        ]
    ):

        return (
            "Relevant to GFAP variants and their "
            "potential molecular or clinical effects."
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
            "Rosenthal fibers, or intermediate "
            "filament pathobiology."
        )

    if "intermediate filament" in text:

        return (
            "Relevant to the molecular biology "
            "of GFAP as an intermediate filament."
        )

    if (
        "astrocyte" in text
        or "astrocytes" in text
    ):

        return (
            "Relevant to GFAP-associated "
            "astrocyte biology."
        )

    if "biomarker" in text:

        return (
            "Relevant to the use of GFAP as "
            "a neurological biomarker."
        )

    return (
        "Relevant to current research "
        "involving GFAP."
    )


# ============================================================
# EXISTING ARCHIVE
# ============================================================

def load_existing_data():

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

            data = json.load(file)

    except (
        json.JSONDecodeError,
        OSError,
    ) as error:

        raise RuntimeError(
            "papers.json could not be read. "
            "The existing archive was not modified."
        ) from error

    if not isinstance(data, dict):

        raise RuntimeError(
            "papers.json does not contain "
            "the expected JSON structure."
        )

    if not isinstance(
        data.get("weeks"),
        list,
    ):

        data["weeks"] = []

    return data


def existing_paper_ids(data):
    """
    Collect PMIDs and DOIs from all PREVIOUS weeks.
    """

    identifiers = set()

    for week in data.get(
        "weeks",
        [],
    ):

        for paper in week.get(
            "papers",
            [],
        ):

            pmid = clean_text(
                paper.get("pmid", "")
            )

            doi = normalize_doi(
                paper.get("doi", "")
            )

            if pmid:

                identifiers.add(
                    "pmid:" + pmid
                )

            if doi:

                identifiers.add(
                    "doi:" + doi
                )

    return identifiers


def already_seen(
    paper,
    identifiers,
):

    pmid = clean_text(
        paper.get("pmid", "")
    )

    doi = normalize_doi(
        paper.get("doi", "")
    )

    if (
        pmid
        and
        "pmid:" + pmid
        in identifiers
    ):
        return True

    if (
        doi
        and
        "doi:" + doi
        in identifiers
    ):
        return True

    return False


# ============================================================
# FINAL PAPER RECORD
# ============================================================

def prepare_paper(paper):

    pmid = clean_text(
        paper.get("pmid", "")
    )

    doi = normalize_doi(
        paper.get("doi", "")
    )

    if pmid:

        url = (
            "https://pubmed.ncbi.nlm.nih.gov/"
            + pmid
            + "/"
        )

    elif doi:

        url = (
            "https://doi.org/"
            + doi
        )

    else:

        url = ""

    return {
        "title": clean_text(
            paper.get("title", "")
        ),

        "authors": clean_text(
            paper.get("authors", "")
        ),

        "journal": clean_text(
            paper.get("journal", "")
        ),

        "date": clean_text(
            paper.get("date", "")
        ),

        "pmid": pmid,

        "doi": doi,

        "url": url,

        "sources": paper.get(
            "sources",
            [],
        ),

        "topics": determine_topics(
            paper
        ),

        "relevance_score": relevance_score(
            paper
        ),

        "summary": create_summary(
            paper.get(
                "abstract",
                "",
            )
        ),

        "why_it_matters": why_it_matters(
            paper
        ),
    }


# ============================================================
# WEEK LABELS
# ============================================================

def current_week_label(today):
    """
    Use Monday as the beginning of each weekly archive entry.

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


def remove_current_week(
    data,
    week_label,
):
    """
    If the workflow runs more than once in the same week,
    replace that week's entry instead of duplicating it.
    """

    data["weeks"] = [
        week
        for week in data.get(
            "weeks",
            [],
        )
        if week.get("week")
        != week_label
    ]


# ============================================================
# MAIN
# ============================================================

def main():

    print("")
    print(
        "GFAP Literature Agent"
    )
    print(
        "====================="
    )
    print("")

    # Search each database independently.
    pubmed_results = (
        safe_search_pubmed()
    )

    europe_pmc_results = (
        safe_search_europe_pmc()
    )

    # Protect the archive if both databases fail.
    if (
        not pubmed_results
        and
        not europe_pmc_results
    ):

        raise RuntimeError(
            "Neither PubMed nor Europe PMC "
            "returned usable results. "
            "papers.json was left unchanged."
        )

    # Merge results.
    merged = merge_papers(
        pubmed_results,
        europe_pmc_results,
    )

    print("")
    print(
        f"{len(merged)} unique papers "
        f"after merging sources."
    )

    if not merged:

        raise RuntimeError(
            "No papers remained after merging. "
            "papers.json was left unchanged."
        )

    # Load archive.
    data = load_existing_data()

    now = datetime.now(
        timezone.utc
    )

    today = now.date()

    week_label = (
        current_week_label(today)
    )

    # Remove the current week before checking previous papers.
    # This makes same-week reruns refresh the week rather than
    # creating duplicates.
    remove_current_week(
        data,
        week_label,
    )

    old_ids = existing_paper_ids(
        data
    )

    new_papers = []

    for paper in merged:

        if already_seen(
            paper,
            old_ids,
        ):
            continue

        score = relevance_score(
            paper
        )

        # Exclude extremely weak matches.
        if score < 2:
            continue

        new_papers.append(
            prepare_paper(paper)
        )

    # Highest relevance first.
    new_papers.sort(
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

    # Create this week's entry.
    week_entry = {
        "week": week_label,

        "generated": (
            now.isoformat()
        ),

        "paper_count": (
            len(new_papers)
        ),

        "papers": new_papers,
    }

    data.setdefault(
        "weeks",
        [],
    )

    data["weeks"].insert(
        0,
        week_entry,
    )

    data["last_updated"] = (
        now.isoformat()
    )

    # Save.
    with open(
        DATA_FILE,
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            data,
            file,
            indent=2,
            ensure_ascii=False,
        )

    # Console report.
    print("")
    print(
        f"Created {week_label}"
    )

    print(
        f"Added {len(new_papers)} "
        f"new relevant papers."
    )

    print(
        "papers.json updated successfully."
    )

    if new_papers:

        print("")
        print(
            "Highest-ranked papers:"
        )

        for paper in new_papers[:10]:

            print("")
            print(
                f"[Score "
                f"{paper['relevance_score']}] "
                f"{paper['title']}"
            )

            if paper["pmid"]:

                print(
                    "PMID: "
                    + paper["pmid"]
                )

            if paper["doi"]:

                print(
                    "DOI: "
                    + paper["doi"]
                )

    print("")
    print(
        "GFAP literature update complete."
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
