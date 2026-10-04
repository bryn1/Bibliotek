"""
Bok-scraper för Bibliotek-projektet.
Hämtar böcker från Libris och OpenLibrary med optimerad rate limiting.
"""

import requests
import json
import time
import sqlite3
import logging
import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class LibrisScraper:
    """Scrapar böcker från Libris API (libris.kb.se)."""

    BASE_URL = "https://libris.kb.se/api/xsearch"
    BATCH_SIZE = 10  # Libris returnerar max 10 per query
    DELAY_BETWEEN_REQUESTS = 1.0  # 1 sek mellan varje begäran

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Bibliotek-Scraper/1.0",
            "Accept": "application/json",
        })

    def _build_title_queries(self) -> List[str]:
        """Bygger query-strängar för att täcka in svenska böcker."""
        prefixes = [
            "a", "b", "c", "d", "e", "f", "g", "h", "i", "j",
            "k", "l", "m", "n", "o", "p", "q", "r", "s", "t",
            "u", "v", "w", "x", "y", "z",
            "den", "ett", "min", "minsta", "stora", "lilla",
            "bok", "svensk", "svenska", "norden", "nordic",
            "roman", "berättelse", "historia", "liv", "tid",
            "värld", "jord", "land", "stad", "by", "hus",
            "människa", "människor", "död", "kärlek",
            "krig", "fred", "frihet", "rättvisa", "sanning",
            "dröm", "drömmar", "glädje", "sorg", "lycka",
            "skola", "lära", "undervisning", "pedagogik",
            "matematik", "vetenskap", "biologi", "kemi", "fysik",
        ]
        queries = []
        for p in prefixes:
            queries.append(f"title:{p}")
        return queries

    def fetch_batch(self, query: str, limit: int = 10) -> Tuple[List[Dict], bool]:
        """Hämtar ett batch med böcker från Libris (JSON-format)."""
        try:
            params = {
                "q": query,
                "limit": limit,
                "offset": 0,
                "format": "json",
            }
            resp = self.session.get(self.BASE_URL, params=params, timeout=30)
            resp.raise_for_status()

            content_type = resp.headers.get("Content-Type", "")
            if "xml" in content_type or resp.text.startswith("<"):
                root = ET.fromstring(resp.text)
                ns = {"marc": "http://www.loc.gov/MARC21/slim"}
                records = root.findall(".//marc:record", ns)

                results = []
                for rec in records:
                    title_elem = rec.find(".//marc:datafield[@tag='245']", ns)
                    title = ""
                    if title_elem is not None:
                        subfield_a = title_elem.find(".//marc:subfield[@code='a']", ns)
                        if subfield_a is not None:
                            title = subfield_a.text or ""
                        subfield_b = title_elem.find(".//marc:subfield[@code='b']", ns)
                        if subfield_b is not None and subfield_b.text:
                            title += f" {subfield_b.text}"

                    creator = ""
                    creator_elem = rec.find(".//marc:datafield[@tag='100']", ns)
                    if creator_elem is not None:
                        subfield_a = creator_elem.find(".//marc:subfield[@code='a']", ns)
                        if subfield_a is not None:
                            creator = subfield_a.text or ""

                    isbn = ""
                    isbn_elem = rec.find(".//marc:datafield[@tag='020']", ns)
                    if isbn_elem is not None:
                        subfield_a = isbn_elem.find(".//marc:subfield[@code='a']", ns)
                        if subfield_a is not None:
                            isbn = subfield_a.text or ""

                    year = ""
                    date_elem = rec.find(".//marc:datafield[@tag='264']", ns)
                    if date_elem is not None:
                        subfield_c = date_elem.find(".//marc:subfield[@code='c']", ns)
                        if subfield_c is not None and subfield_c.text:
                            match = re.search(r'(\d{4})', subfield_c.text)
                            if match:
                                year = match.group(1)

                    subjects = []
                    for subject_tag in ["650", "655"]:
                        subject_elems = rec.findall(f".//marc:datafield[@tag='{subject_tag}']", ns)
                        for subj in subject_elems:
                            subfield_a = subj.find(".//marc:subfield[@code='a']", ns)
                            if subfield_a is not None and subfield_a.text:
                                subjects.append(subfield_a.text)

                    results.append({
                        "title": title,
                        "creator": creator,
                        "isbn": isbn,
                        "date": year,
                        "subjects": subjects,
                    })

                return results, True
            else:
                data = resp.json()
                xsearch = data.get("xsearch", {})
                results = xsearch.get("list", [])
                return results, True

        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                logger.warning("Libris rate limit (429) - väntar 30 sek")
                time.sleep(30)
                return self.fetch_batch(query, limit)
            logger.error(f"Libris HTTP error: {e}")
            return [], False
        except Exception as e:
            logger.error(f"Libris error: {e}")
            return [], False

    def scrape(self, max_books: int = 50000) -> List[Dict]:
        """Scrapar svenska böcker från Libris."""
        all_books = []
        seen_isbns = set()
        queries = self._build_title_queries()

        logger.info(f"Libris: kommer att testa {len(queries)} olika queries")

        for query in queries:
            if len(all_books) >= max_books:
                break

            results, ok = self.fetch_batch(query)
            if ok:
                for rec in results:
                    isbn = rec.get("isbn", "")
                    if not isbn:
                        continue
                    # isbn kan vara en lista, konvertera till string
                    if isinstance(isbn, list):
                        isbn_str = ",".join(str(i) for i in isbn if i)
                    else:
                        isbn_str = str(isbn)
                    if not isbn_str:
                        continue
                    if isbn_str in seen_isbns:
                        continue
                    seen_isbns.add(isbn_str)

                    book = self._parse_libris_record(rec, isbn_str)
                    if book:
                        all_books.append(book)

            time.sleep(self.DELAY_BETWEEN_REQUESTS)

        logger.info(f"Libris: {len(all_books)} unika böcker hämtade")
        return all_books

    def _parse_libris_record(self, rec: Dict, isbn: str) -> Optional[Dict]:
        """Parsoar en Libris-post till vår bok-dictionary."""
        title = rec.get("title", "")
        creator = rec.get("creator", "")
        date = rec.get("date", "")

        year = None
        if isinstance(date, str) and date:
            if date.isdigit():
                year = int(date)
            elif len(date) >= 4:
                try:
                    year = int(date[:4])
                except ValueError:
                    pass
        elif isinstance(date, list) and date and isinstance(date[0], str):
            d = date[0]
            if d.isdigit():
                year = int(d)
            elif len(d) >= 4:
                try:
                    year = int(d[:4])
                except ValueError:
                    pass

        hcf = self._classify_age(title, rec.get("title", []), rec.get("mainTitle", ""))

        return {
            "isbn": isbn or "",
            "title": title or "",
            "author": creator if isinstance(creator, str) else str(creator),
            "publisher": "",
            "year": year,
            "cover_url": "",
            "hcf_category": hcf,
            "dewey_number": None,
            "subjects": "",
            "languages": "swe",
            "source": "libris",
        }

    @staticmethod
    def _classify_age(title: str, titles: list = None, main_title: str = "") -> str:
        """Klassificerar bok efter åldersgrupp."""
        all_titles = [title]
        if isinstance(titles, list):
            all_titles.extend(titles)
        if isinstance(main_title, str) and main_title:
            all_titles.append(main_title)

        text = " ".join(all_titles).lower()

        barn_keywords = [
            "barnbok", "barnens", "barns bok", "för barn", "babysaga",
            "småbarnsbok", "lego", "disney", "pixar", "kalle anka",
            "astrid", "lindegren", "sagan", "prinsessa", "prins",
            "drake", "riddare", "troll", "tomte", "lek", "läs",
        ]
        unga_keywords = [
            "unga", "ungdom", "ungdoms", "tonår", "tonårig",
            "young adult", "ungdomsbok", "ungdomsdrama",
            "school", "high school",
        ]

        for kw in unga_keywords:
            if kw in text:
                return "unga"

        for kw in barn_keywords:
            if kw in text:
                return "barn"

        return "vuxen"


class OpenLibraryScraper:
    """Scrapar böcker från OpenLibrary med optimerad rate limiting."""

    SEARCH_URL = "https://openlibrary.org/search.json"
    BATCH_SIZE = 100
    DELAY_BETWEEN_REQUESTS = 0.15  # 150ms mellan varje begäran (optimerad från 300ms)
    MAX_RETRIES = 3

    SUBJECTS = {
        "fiction": ("skönlitteratur", "vuxen"),
        "biography": ("biografier", "vuxen"),
        "children": ("barnböcker", "barn"),
        "young_adult": ("ungdomsböcker", "unga"),
        "fantasy": ("fantasy", "vuxen"),
        "mystery": ("deckare", "vuxen"),
        "science": ("vetenskap", "vuxen"),
        "history": ("historia", "vuxen"),
        "romance": ("kärleksromaner", "vuxen"),
        "thriller": ("thriller", "vuxen"),
        "horror": ("skräck", "vuxen"),
        "adventure": ("äventyr", "vuxen"),
        "poetry": ("poesi", "vuxen"),
        "self_help": ("självhjälpsböcker", "vuxen"),
        "business": ("affärslitteratur", "vuxen"),
        "technology": ("teknik", "vuxen"),
        "art": ("konst", "vuxen"),
        "juvenile": ("barnböcker", "barn"),
        "adult": ("vuxenböcker", "vuxen"),
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Bibliotek-Scraper/1.0",
            "Accept": "application/json",
        })

    def scrape_subject(self, subject: str, max_books: int = 5000) -> List[Dict]:
        """Scrapar böcker med pagination och retry vid fel."""
        all_books = []
        seen_titles = set()
        seen_isbns = set()
        page = 1
        hcf, default_age = self.SUBJECTS.get(subject, ("okänd", "vuxen"))

        while len(all_books) < max_books:
            params = {
                "subject": subject,
                "limit": self.BATCH_SIZE,
                "page": page,
                "fields": "key,title,author_name,first_publish_year,isbn,"
                          "subjects,genre,languages,dewey_decimal_number,cover_i",
            }

            ok = False
            resp_data = None
            for attempt in range(self.MAX_RETRIES):
                try:
                    resp = self.session.get(self.SEARCH_URL, params=params, timeout=30)
                    resp.raise_for_status()
                    resp_data = resp.json()
                    ok = True
                    break
                except requests.exceptions.HTTPError as e:
                    if e.response is not None and e.response.status_code == 429:
                        logger.warning(f"OpenLibrary rate limit (429) - väntar 30 sek")
                        time.sleep(30)
                        continue
                    elif e.response is not None and e.response.status_code == 500:
                        logger.warning(f"OpenLibrary 500 (försök {attempt+1}/{self.MAX_RETRIES}) - väntar 5 sek")
                        time.sleep(5)
                        continue
                    logger.error(f"OpenLibrary HTTP error: {e}")
                    ok = False
                    break
                except Exception as e:
                    logger.error(f"OpenLibrary error: {e}")
                    ok = False
                    break

            if not ok or resp_data is None:
                logger.warning(f"OpenLibrary '{subject}': misslyckades efter {self.MAX_RETRIES} försök, hoppar över sid {page}")
                page += 1
                continue

            docs = resp_data.get("docs", [])
            if not docs:
                logger.info(f"OpenLibrary '{subject}': inga fler böcker (sid {page})")
                break

            for doc in docs:
                title = doc.get("title", "")
                if title in seen_titles:
                    continue

                # Deduplicera per titel + ISBN
                isbn = doc.get("isbn", [])
                if isinstance(isbn, str):
                    isbn = [isbn]
                key = f"{title}|{isbn[0] if isbn else ''}"
                if key in seen_isbns:
                    continue

                if len(all_books) >= max_books:
                    break

                seen_titles.add(title)
                if isbn:
                    for i in isbn:
                        seen_isbns.add(key)

                book = self._parse_ol_record(doc, subject, hcf, default_age)
                if book and book["title"]:
                    all_books.append(book)

            logger.info(
                f"OpenLibrary '{subject}': sid {page}, "
                f"{len(docs)} resultat, {len(all_books)} totalt"
            )
            page += 1

            time.sleep(self.DELAY_BETWEEN_REQUESTS)

        logger.info(f"OpenLibrary '{subject}': {len(all_books)} unika böcker hämtade")
        return all_books

    def _parse_ol_record(self, doc: Dict, subject: str, hcf: str, default_age: str) -> Optional[Dict]:
        """Parsoar en OpenLibrary-post."""
        title = doc.get("title", "")
        authors = doc.get("author_name", [])
        isbn = doc.get("isbn", [])
        subjects = doc.get("subjects", [])
        genres = doc.get("genre", [])
        languages = doc.get("languages", [])
        first_year = doc.get("first_publish_year")
        cover_id = doc.get("cover_i")

        # Översätt ämnen till Dewey-nummer
        dewey = self._subject_to_dewey(subjects)
        if not dewey and genres:
            dewey = self._subject_to_dewey(genres)

        # Klassificera HCF baserat på titel och ämne
        hcf_age = default_age
        all_subjects = [s.lower() if isinstance(s, str) else str(s).lower() for s in subjects]
        all_genres = [g.lower() if isinstance(g, str) else str(g).lower() for g in genres]
        title_lower = title.lower()

        for kw in ["children", "barn", "young adult", "ungdom", "juvenile", "teen"]:
            if kw in all_subjects or kw in title_lower:
                if kw in ["children", "barn", "juvenile"]:
                    hcf_age = "barn"
                elif kw in ["young adult", "ungdom", "teen"]:
                    hcf_age = "unga"
                break

        cover_url = ""
        if cover_id:
            cover_url = f"https://covers.openlibrary.org/b/id/{cover_id}-M.jpg"

        return {
            "isbn": ", ".join(isbn[:5]) if isbn else "",
            "title": title,
            "author": ", ".join(authors[:3]) if authors else "",
            "publisher": "",
            "year": int(first_year) if first_year else None,
            "cover_url": cover_url,
            "hcf_category": hcf_age,
            "dewey_number": dewey,
            "subjects": ", ".join(str(s) for s in subjects[:5]),
            "languages": ", ".join(languages[:3]) if languages else "",
            "source": "openlibrary",
        }

    @staticmethod
    def _subject_to_dewey(subjects: list) -> Optional[int]:
        """Översätter ämnen till Dewey Decimal Number."""
        dewey_map = {
            "fiction": 800, "science fiction": 800, "fantasy": 800,
            "mystery": 800, "thriller": 800, "romance": 800,
            "adventure": 800, "horror": 800, "biography": 900,
            "history": 900, "philosophy": 100, "religion": 200,
            "science": 500, "mathematics": 510, "physics": 530,
            "chemistry": 540, "biology": 570, "social sciences": 300,
            "psychology": 150, "self help": 158, "business": 650,
            "technology": 600, "engineering": 620, "medicine": 610,
            "art": 700, "literature": 800, "language": 400,
            "computers": 000, "programming": 000, "education": 370,
            "children": 800, "young adult": 800, "teen": 800,
            "poetry": 800, "drama": 800,
        }

        for subj in subjects:
            subj_lower = subj.lower() if isinstance(subj, str) else str(subj).lower()
            if subj_lower in dewey_map:
                return dewey_map[subj_lower]

        return None


class DatabaseManager:
    """Hanterar SQLite-databas för böcker."""

    def __init__(self, db_path: str = "books.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()
        self.conn.commit()

    def _create_tables(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                isbn TEXT UNIQUE,
                title TEXT NOT NULL,
                author TEXT,
                publisher TEXT,
                year INTEGER,
                cover_url TEXT,
                hcf_category TEXT NOT NULL,
                dewey_number INTEGER,
                subjects TEXT,
                languages TEXT,
                source TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_isbn ON books(isbn)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_title ON books(title)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_hcf ON books(hcf_category)")
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT,
                category TEXT,
                count INTEGER,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

    def save_books(self, books: List[Dict]) -> int:
        """Sparar böcker i databasen. Returnerar antal nya."""
        new_count = 0
        for book in books:
            try:
                result = self.conn.execute("""
                    INSERT OR IGNORE INTO books (isbn, title, author, publisher, year,
                        cover_url, hcf_category, dewey_number, subjects, languages, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    book.get("isbn", ""),
                    book.get("title", ""),
                    book.get("author", ""),
                    book.get("publisher", ""),
                    book.get("year"),
                    book.get("cover_url", ""),
                    book.get("hcf_category", ""),
                    book.get("dewey_number"),
                    book.get("subjects", ""),
                    book.get("languages", ""),
                    book.get("source", ""),
                ))
                if result.rowcount > 0:
                    new_count += 1
            except Exception as e:
                logger.error(f"Error saving book '{book.get('title', '?')}': {e}")
        self.conn.commit()
        return new_count

    def save_stats(self, source: str, category: str, count: int):
        self.conn.execute(
            "INSERT INTO stats (source, category, count) VALUES (?, ?, ?)",
            (source, category, count)
        )
        self.conn.commit()

    def get_stats(self) -> Dict:
        total = self.conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        sources = {}
        for row in self.conn.execute("SELECT source, COUNT(*) FROM books GROUP BY source"):
            sources[row[0]] = {"count": row[1]}
        hcf = {}
        for row in self.conn.execute("SELECT hcf_category, COUNT(*) FROM books GROUP BY hcf_category"):
            hcf[row[0]] = row[1]
        return {
            "total": total,
            "sources": sources,
            "hcf_distribution": hcf,
        }

    def close(self):
        self.conn.close()


def run_scraping(db_path: str = "books.db", max_per_source: int = 50000):
    """
    Kör hela scraping-pipeline:
    1. OpenLibrary (flera kategorier, huvudkälla)
    2. Libris (~760 svenska böcker som komplement)
    """
    logger.info("=" * 60)
    logger.info("BOK-SCRAPING STARTAR")
    logger.info("=" * 60)

    db = DatabaseManager(db_path)
    total_saved = 0

    # 1. OpenLibrary - kategorier (huvudkälla)
    logger.info("\n--- OPENLIBRARY ---")
    ol = OpenLibraryScraper()

    categories = [
        ("fiction", 5000),
        ("biography", 5000),
        ("children", 5000),
        ("young_adult", 3000),
        ("fantasy", 3000),
        ("mystery", 3000),
        ("science", 3000),
        ("history", 3000),
        ("romance", 3000),
        ("thriller", 3000),
        ("horror", 2000),
        ("adventure", 2000),
        ("poetry", 2000),
        ("self_help", 2000),
        ("business", 2000),
        ("technology", 2000),
        ("art", 2000),
        ("juvenile", 2000),
    ]

    for category, max_count in categories:
        if total_saved >= max_per_source:
            logger.info(f"Totalt max ({max_per_source}) nått, avslutar")
            break

        effective_max = min(max_count, max_per_source - total_saved)
        if effective_max <= 0:
            break

        logger.info(f"\nKategorin '{category}'...")
        ol_books = ol.scrape_subject(category, max_books=effective_max)
        saved = db.save_books(ol_books)
        db.save_stats("openlibrary", category, len(ol_books))
        total_saved += saved
        logger.info(f"OpenLibrary '{category}': {len(ol_books)} hämtade, {saved} nya sparade")

    # 2. Libris - svenska böcker (komplement)
    logger.info("\n--- LIBRIS ---")
    libris = LibrisScraper()
    libris_books = libris.scrape(max_books=1000)
    saved = db.save_books(libris_books)
    db.save_stats("libris", "all", len(libris_books))
    total_saved += saved
    logger.info(f"Libris: {len(libris_books)} hämtade, {saved} nya sparade")

    # Sammanfattning
    stats = db.get_stats()
    logger.info("\n" + "=" * 60)
    logger.info("SCRAPING SLUTFÖRD")
    logger.info("=" * 60)
    logger.info(f"Totalt unika böcker: {stats['total']}")
    logger.info(f"Fördelning per källa:")
    for source, s in stats["sources"].items():
        logger.info(f"  {source}: {s['count']} böcker")
    logger.info(f"Fördelning per åldersgrupp (HCF):")
    for cat, count in stats["hcf_distribution"].items():
        logger.info(f"  {cat}: {count} böcker")

    db.close()
    return stats


if __name__ == "__main__":
    print("Startar scraping...")
    stats = run_scraping()
    print(f"Totalt unika böcker: {stats['total']}")
    print(f"Källor: {stats['sources']}")
