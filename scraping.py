
import requests
import json
import time
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional
import random
import hashlib

class BookScraper:
    def __init__(self, db_path: str = "books.db"):
        self.db_path = db_path
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Bibliotek-Scraper/1.0",
            "Accept": "application/json"
        })
        self.setup_database()
        
    def setup_database(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                isbn TEXT UNIQUE,
                title TEXT NOT NULL,
                author TEXT,
                publisher TEXT,
                year INTEGER,
                cover_url TEXT,
                hcf_category TEXT,
                dewey_number INTEGER,
                subjects TEXT,
                languages TEXT,
                source TEXT,
                scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_isbn ON books(isbn)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_title ON books(title)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_author ON books(author)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_hcf_category ON books(hcf_category)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_dewey_number ON books(dewey_number)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_subjects ON books(subjects)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_languages ON books(languages)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_source ON books(source)
        """)
        
        conn.commit()
        conn.close()
        
    def fetch_libris_books(self, limit: int = 1000) -> List[Dict]:
        url = "https://libris.kb.se/api/xsearch"
        params = {
            "q": "format:book+language:swe",
            "limit": limit,
            "format": "json"
        }
        
        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            books = []
            for record in data.get("xsearch", {}).get("list", []):
                book = {
                    "isbn": record.get("isbn", ""),
                    "title": record.get("title", ""),
                    "author": record.get("creator", ""),
                    "publisher": record.get("publisher", ""),
                    "year": int(record.get("date", 0)) if record.get("date") and record.get("date").isdigit() else None,
                    "cover_url": "",
                    "hcf_category": self.classify_hcf(record.get("title", "")),
                    "dewey_number": None,
                    "subjects": "",
                    "languages": ",".join(record.get("language", [])),
                    "source": "libris"
                }
                books.append(book)
                
            return books
        except Exception as e:
            print(f"Error fetching Libris books: {e}")
            return []
            
    def fetch_openlibrary_books(self, subject: str, limit: int = 1000) -> List[Dict]:
        url = "https://openlibrary.org/search.json"
        params = {
            "subject": subject,
            "limit": limit,
            "fields": "title,author_name,first_publish_year,isbn,subjects,dewey_decimal_number,languages,cover_i"
        }
        
        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            books = []
            for record in data.get("docs", []):
                cover_id = record.get("cover_i")
                cover_url = f"https://covers.openlibrary.org/b/id/{cover_id}-M.jpg" if cover_id else ""
                
                book = {
                    "isbn": ",".join(record.get("isbn", []))[:200],
                    "title": record.get("title", ""),
                    "author": ", ".join(record.get("author_name", [])),
                    "publisher": "",
                    "year": record.get("first_publish_year"),
                    "cover_url": cover_url,
                    "hcf_category": self.classify_hcf(record.get("title", "")),
                    "dewey_number": int(record.get("dewey_decimal_number", [0])[0]) if record.get("dewey_decimal_number") else None,
                    "subjects": ",".join(record.get("subjects", [])[:10]),
                    "languages": ",".join(record.get("languages", [])),
                    "source": "openlibrary"
                }
                books.append(book)
                
            return books
        except Exception as e:
            print(f"Error fetching OpenLibrary books: {e}")
            return []
            
    def classify_hcf(self, title: str) -> str:
        title_lower = title.lower()
        
        if any(word in title_lower for word in ["barnbok", "barn", "ungdom", "tonåring", "barndom", "uppväxt", "skola", "lek", "lego", "disney", "pixar", "mickey", "donald", "kalle", "anka", "musa", "saga", "hemsöborna", "nalle", "puss", "tomte", "gubben", "kaffe", "lotta", "bröder", "syskon", "systerson", "brorson", "kusin", "farsan", "mamsell", "ung", "barnens", "barns", "ungdoms", "ungdomars", "tonåringens", "tonårs"]):
            return "barn"
        elif any(word in title_lower for word in ["unga", "tonår", "ungdom", "skola", "ungdomars", "tonårs", "unga", "tonåring", "ungdoms", "ungdomar", "tonåringars", "tonåringar", "unga", "tonåringar"]):
            return "unga"
        elif any(word in title_lower for word in ["vuxen", "vuxna", "vuxnas", "vuxna", "vuxen", "vuxna", "vuxnas", "vuxna", "vuxen", "vuxna"]):
            return "vuxen"
        else:
            return "vuxen"
            
    def save_books(self, books: List[Dict]):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        for book in books:
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO books (isbn, title, author, publisher, year, cover_url, hcf_category, dewey_number, subjects, languages, source)
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
                    book.get("source", "")
                ))
            except Exception as e:
                print(f"Error saving book: {e}")
                
        conn.commit()
        conn.close()
        
    def run_scraping(self):
        print("Starting book scraping...")
        start_time = time.time()
        
        # Scrape Libris books
        print("Scraping Libris books...")
        libris_books = self.fetch_libris_books(1000)
        print(f"Fetched {len(libris_books)} Libris books")
        self.save_books(libris_books)
        
        # Scrape OpenLibrary books
        subjects = ["fiction", "biography", "children", "young_adult", "fantasy", "mystery", "science", "history", "romance", "thriller"]
        for subject in subjects:
            print(f"Scraping OpenLibrary books for subject: {subject}...")
            ol_books = self.fetch_openlibrary_books(subject, 1000)
            print(f"Fetched {len(ol_books)} {subject} books")
            self.save_books(ol_books)
            time.sleep(1)  # Rate limiting
            
        end_time = time.time()
        print(f"Scraping completed in {end_time - start_time:.2f} seconds")
        print(f"Total books in database: {self.get_book_count()}")
        
    def get_book_count(self) -> int:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM books")
        count = cursor.fetchone()[0]
        conn.close()
        return count
        
    def search_books(self, query: str) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM books 
            WHERE title LIKE ? OR author LIKE ? OR subjects LIKE ?
            LIMIT 100
        """, (f"%{query}%", f"%{query}%", f"%{query}%"))
        
        books = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return books
        
    def get_books_by_category(self, category: str) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM books 
            WHERE hcf_category = ?
            LIMIT 100
        """, (category,))
        
        books = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return books
        
    def get_books_by_dewey(self, dewey_number: int) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM books 
            WHERE dewey_number = ?
            LIMIT 100
        """, (dewey_number,))
        
        books = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return books

# Usage
if __name__ == "__main__":
    scraper = BookScraper()
    scraper.run_scraping()
