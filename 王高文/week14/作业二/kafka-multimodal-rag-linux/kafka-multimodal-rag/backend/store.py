import re
import sqlite3
import unicodedata
from contextlib import closing, contextmanager
from pathlib import Path


def terms(text):
    normalized = unicodedata.normalize("NFKC", text).lower()
    words = []
    for token in re.findall(r"[\u3400-\u9fff]+|[a-z0-9]+", normalized):
        if "\u3400" <= token[0] <= "\u9fff" and len(token) > 1:
            words.extend(token[i:i + 2] for i in range(len(token) - 1))
        else:
            words.append(token)
    return words


class Store:
    def __init__(self, filename):
        self.filename = Path(filename)
        self.filename.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL,
                    error TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS images (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL, mime TEXT NOT NULL, data BLOB NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS evidence USING fts5(
                    tokens, document_id UNINDEXED, page UNINDEXED, text UNINDEXED, image_id UNINDEXED
                );
            """)

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.filename, timeout=15)) as db:
            db.row_factory = sqlite3.Row
            with db:
                yield db

    def queued(self, doc_id, name):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO documents(id,name,status) VALUES(?,?,'queued')", (doc_id, name))

    def begin(self, doc_id, name):
        with self.connect() as db:
            result = db.execute("""
                INSERT INTO documents(id,name,status) VALUES(?,?,'parsing')
                ON CONFLICT(id) DO UPDATE SET status='parsing', error=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE documents.status IN ('queued','parsing')
            """, (doc_id, name))
            return bool(result.rowcount)

    def fail(self, doc_id, message):
        with self.connect() as db:
            db.execute("""
                UPDATE documents SET status='failed', error=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status='parsing'
            """, (message, doc_id))

    def finish(self, doc_id, blocks):
        with self.connect() as db:
            result = db.execute("""
                UPDATE documents SET status='complete', error=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status='parsing'
            """, (doc_id,))
            if not result.rowcount:
                return
            for index, block in enumerate(blocks):
                image_id = None
                if block.image:
                    image_id = f"{doc_id}-{index}"
                    db.execute("INSERT INTO images VALUES(?,?,?,?)", (image_id, doc_id, *block.image))
                db.execute("INSERT INTO evidence VALUES(?,?,?,?,?)", (
                    " ".join(terms(block.text)), doc_id, block.page, block.text, image_id
                ))

    def get(self, doc_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
            return dict(row) if row else None

    def documents(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM documents ORDER BY rowid DESC")]

    def image(self, image_id):
        with self.connect() as db:
            row = db.execute("SELECT mime,data FROM images WHERE id=?", (image_id,)).fetchone()
            return tuple(row) if row else None

    def search(self, query, limit=8):
        tokens = list(dict.fromkeys(terms(query)))[:120]
        if not tokens:
            return []
        expression = " OR ".join(f'"{token}"' for token in tokens)
        with self.connect() as db:
            rows = db.execute("""
                SELECT documents.name, evidence.document_id, evidence.page, evidence.text, evidence.image_id
                FROM evidence JOIN documents ON documents.id=evidence.document_id
                WHERE evidence MATCH ? AND documents.status='complete'
                ORDER BY rank, evidence.rowid LIMIT ?
            """, (expression, limit))
            return [dict(row) for row in rows]
