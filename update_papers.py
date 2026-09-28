import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path


# ============================================================
# SETTINGS
# ============================================================

DATA_FILE = Path("papers.json")

LOOKBACK_DAYS = 14
MAX_RESULTS_PER_SOURCE = 100

PUBMED_ESEARCH = (
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
)
PUBMED_EFETCH = (
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
)
EUROPE_PMC_SEARCH = (
    "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
)

# PubMed query.
# Search primarily in title/abstract so generic metadata matches
# don't overwhelm the results.
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

USER_AGENT = (
    "GFAP-Literature-Agent/1.0 "
    "(weekly literature monitoring for GFAP Atlas)"
)


# ============================================================
# GENERAL HELPERS
# ============================================================

def request_url(url):
    """Open a URL with an identifying User-Agent."""

    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT},
    )

    with urllib.request.urlopen(
        request,
        timeout=60,
    ) as response:
        return response.read()


def clean_text(text):
    """Collapse whitespace and remove simple HTML tags."""

    if not text:
        return ""

    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def normalize_doi(doi):
    if not doi:
        return ""

    doi = doi.strip().lower()

    doi = doi.replace("https://doi.org/", "")
    doi = doi.replace("http://doi.org/", "")
    doi = doi.replace("doi:", "")

    return doi.strip()


# ============================================================
# PUBMED
# ============================================================

def search_pubmed():
    """
    Search PubMed for recent GFAP literature.

    Returns a list of standardized paper dictionaries.
    """

    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=LOOKBACK_DAYS)

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
    search_data = json.loads(raw.decode("utf-8"))

    pmids = (
        search_data
        .get("esearchresult", {})
        .get("idlist", [])
    )

    print(f"PubMed returned {len(pmids)} records.")

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

    for article in root.findall(".//PubmedArticle"):

        # ----------------------------
        # PMID
        # ----------------------------

        pmid = ""

        pmid_element = article.find(
            ".//MedlineCitation/PMID"
        )

        if pmid_element is not None:
            pmid = clean_text(
                "".join(pmid_element.itertext())
            )

        # ----------------------------
        # Title
        # ----------------------------

        title_element = article.find(
            ".//Article/ArticleTitle"
        )

        title = ""

        if title_element is not None:
            title = clean_text(
                "".join(title_element.itertext())
            )

        # ----------------------------
        # Abstract
        # ----------------------------

        abstract_parts = []

        for abstract_element in article.findall(
            ".//Article/Abstract/AbstractText"
        ):
            section_text = clean_text(
                "".join(abstract_element.itertext())
            )

            label = abstract_element.attrib.get(
                "Label",
                "",
            )

            if label and section_text:
                abstract_parts.append(
                    f"{label}: {section_text}"
                )
            elif section_text:
                abstract_parts.append(section_text)

        abstract = " ".join(abstract_parts)

        # ----------------------------
        # Authors
        # ----------------------------

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

            last_name = author.findtext(
                "LastName"
            ) or ""

            initials = author.findtext(
                "Initials"
            ) or ""

            name = " ".join(
                part
                for part in [last_name, initials]
                if part
            )

            if name:
                authors.append(name)

        author_string = ", ".join(authors)

        # ----------------------------
        # Journal
        # ----------------------------

        journal = clean_text(
            article.findtext(
                ".//Article/Journal/Title"
            )
            or ""
        )

        # ----------------------------
        # DOI
        # ----------------------------

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

        # ----------------------------
        # Publication date
        # ----------------------------

        pub_date = extract_pubmed_date(article)

        papers.append(
            {
                "source": "PubMed",
                "pmid": pmid,
                "doi": doi,
                "title": title,
                "authors": author_string,
                "journal": journal,
                "date": pub_date,
                "abstract": abstract,
            }
        )

    return papers


def extract_pubmed_date(article):
    """
    Try several PubMed date locations.
    """

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
    """
    Search Europe PMC for recent GFAP literature.
    """

    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=LOOKBACK_DAYS)

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
    data = json.loads(raw.decode("utf-8"))

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

        papers.append(
            {
                "source": "Europe PMC",

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
                    result.get("authorString", "")
                    or ""
                ),

                "journal": clean_text(
                    result.get("journalTitle", "")
                    or ""
                ),

                "date": (
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
        )

    return papers


# ============================================================
# MERGING / DEDUPLICATION
# ============================================================

def paper_key(paper):
    """
    Create the best identifier available.

    PMID is preferred because PubMed and Europe PMC
    commonly share it.
    """

    if paper.get("pmid"):
        return "pmid:" + paper["pmid"]

    if paper.get("doi"):
        return "doi:" + normalize_doi(
            paper["doi"]
        )

    return (
        "title:"
        + paper.get("title", "").lower().strip()
    )


def merge_papers(pubmed, europe_pmc):
    """
    Merge duplicate records.

    If Europe PMC has an abstract that PubMed lacks,
    or vice versa, keep the richer metadata.
    """

    merged = {}

    for paper in pubmed + europe_pmc:

        key = paper_key(paper)

        if not paper.get("title"):
            continue

        if key not in merged:
            merged[key] = paper.copy()
            merged[key]["sources"] = [
                paper["source"]
            ]
            continue

        existing = merged[key]

        if (
            paper["source"]
            not in existing["sources"]
        ):
            existing["sources"].append(
                paper["source"]
            )

        # Prefer whichever source has more complete data.

        for field in [
            "abstract",
            "authors",
            "journal",
            "date",
            "pmid",
            "doi",
        ]:

            existing_value = (
                existing.get(field, "")
                or ""
            )

            new_value = (
                paper.get(field, "")
                or ""
            )

            if (
                len(str(new_value))
                > len(str(existing_value))
            ):
                existing[field] = new_value

    return list(merged.values())


# ============================================================
# GFAP RELEVANCE
# ============================================================

def relevance_score(paper):

    title = paper.get(
        "title",
        "",
    ).lower()

    abstract = paper.get(
        "abstract",
        "",
    ).lower()

    text = title + " " + abstract

    score = 0

    # ----------------------------
    # Highest priority
    # ----------------------------

    if "alexander disease" in text:
        score += 20

    if "alexander disease" in title:
        score += 10

    # ----------------------------
    # Direct GFAP focus
    # ----------------------------

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

    # ----------------------------
    # Variants / genetics
    # ----------------------------

    genetics_terms = [
        "mutation",
        "mutations",
        "variant",
        "variants",
        "genotype",
        "pathogenic",
    ]

    if any(
        term in text
        for term in genetics_terms
    ):
        score += 6

    # ----------------------------
    # Aggregation
    # ----------------------------

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

    # ----------------------------
    # Intermediate filament biology
    # ----------------------------

    if "intermediate filament" in text:
        score += 5

    # ----------------------------
    # Astrocyte biology
    # ----------------------------

    if (
        "astrocyte" in text
        or "astrocytes" in text
    ):
        score += 2

    # ----------------------------
    # Biomarker papers
    # ----------------------------

    if "biomarker" in text:
        score += 1

    # Penalize papers where GFAP is likely only
    # a generic staining marker.

    generic_marker_terms = [
        "immunohistochemistry",
        "immunostaining",
        "immunofluorescence",
    ]

    if (
        "gfap" not in title
        and "alexander disease" not in title
        and any(
            term in text
            for term in generic_marker_terms
        )
    ):
        score -= 2

    return score


def determine_topics(paper):

    text = (
        paper.get("title", "")
        + " "
        + paper.get("abstract", "")
    ).lower()

    topics = []

    if "alexander disease" in text:
        topics.append(
            "Alexander disease"
        )

    if (
        "mutation" in text
        or "variant" in text
    ):
        topics.append(
            "GFAP variants"
        )

    if (
        "aggregation" in text
        or "aggregate" in text
        or "rosenthal fiber" in text
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
# SUMMARIES
# ============================================================

def create_summary(abstract):
    """
    Temporary non-AI summary.

    Uses the actual abstract so that the initial version
    cannot hallucinate a scientific conclusion.
    """

    abstract = clean_text(abstract)

    if not abstract:
        return (
            "Abstract not available through "
            "PubMed or Europe PMC."
        )

    # Try to use roughly the first 2 sentences.

    sentences = re.split(
        r"(?<=[.!?])\s+",
        abstract,
    )

    if len(sentences) >= 2:
        summary = " ".join(
            sentences[:2]
        )
    else:
        summary = abstract

    if len(summary) > 650:
        summary = (
            summary[:647]
            .rsplit(" ", 1)[0]
            + "..."
        )

    return summary


def why_it_matters(paper):

    text = (
        paper.get("title", "")
        + " "
        + paper.get("abstract", "")
    ).lower()

    if "alexander disease" in text:
        return (
            "Directly relevant to Alexander disease "
            "and GFAP-associated disease biology."
        )

    if (
        "mutation" in text
        or "variant" in text
    ):
        return (
            "Relevant to GFAP variants and their "
            "potential molecular or clinical effects."
        )

    if (
        "aggregation" in text
        or "aggregate" in text
        or "rosenthal fiber" in text
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
            "Relevant to the use of GFAP as a "
            "neurological biomarker."
        )

    return (
        "Relevant to current research involving GFAP."
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

    with open(
        DATA_FILE,
        "r",
        encoding="utf-8",
    ) as file:

        return json.load(file)


def existing_paper_ids(data):

    identifiers = set()

    for week in data.get(
        "weeks",
        [],
    ):

        for paper in week.get(
            "papers",
            [],
        ):

            if paper.get("pmid"):
                identifiers.add(
                    "pmid:" + paper["pmid"]
                )

            if paper.get("doi"):
                identifiers.add(
                    "doi:"
                    + normalize_doi(
                        paper["doi"]
                    )
                )

    return identifiers


def already_seen(paper, identifiers):

    if paper.get("pmid"):
        if (
            "pmid:" + paper["pmid"]
            in identifiers
        ):
            return True

    if paper.get("doi"):
        if (
            "doi:"
            + normalize_doi(paper["doi"])
            in identifiers
        ):
            return True

    return False


# ============================================================
# FINAL RECORD
# ============================================================

def prepare_paper(paper):

    pmid = paper.get(
        "pmid",
        "",
    )

    doi = normalize_doi(
        paper.get(
            "doi",
            "",
        )
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
        "title": paper.get(
            "title",
            "",
        ),

        "authors": paper.get(
            "authors",
            "",
        ),

        "journal": paper.get(
            "journal",
            "",
        ),

        "date": paper.get(
            "date",
            "",
        ),

        "pmid": pmid,

        "doi": doi,

        "url": url,

        "sources": paper.get(
            "sources",
            [paper.get("source", "")],
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
# MAIN
# ============================================================

def main():

    print(
        "\nGFAP Literature Agent"
    )

    print(
        "=====================\n"
    )

    # Search both databases.

    pubmed_results = search_pubmed()

    europe_pmc_results = (
        search_europe_pmc()
    )

    # Merge and deduplicate.

    merged = merge_papers(
        pubmed_results,
        europe_pmc_results,
    )

    print(
        f"\n{len(merged)} unique papers "
        f"after merging databases."
    )

    # Load archive.

    data = load_existing_data()

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

        # Remove extremely weak matches.
        if score < 2:
            continue

        new_papers.append(
            prepare_paper(paper)
        )

    # Highest relevance first.

    new_papers.sort(
        key=lambda paper: (
            paper["relevance_score"],
            paper["date"],
        ),
        reverse=True,
    )

    now = datetime.now(
        timezone.utc
    )

    today = now.date()

    week_entry = {
        "week": (
            "Week of "
            + today.strftime(
                "%B %d, %Y"
            )
        ),

        "generated": (
            now.isoformat()
        ),

        "paper_count": (
            len(new_papers)
        ),

        "papers": new_papers,
    }

    # Put newest week first.

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

    print(
        f"\nAdded {len(new_papers)} "
        f"new papers."
    )

    print(
        "papers.json updated successfully."
    )

    if new_papers:

        print(
            "\nHighest-ranked papers:"
        )

        for paper in new_papers[:5]:

            print(
                f"\n"
                f"[{paper['relevance_score']}] "
                f"{paper['title']}"
            )


if __name__ == "__main__":
    main()
