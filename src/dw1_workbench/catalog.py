from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS project_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS resources (
 id TEXT PRIMARY KEY, original_path TEXT NOT NULL, parent_path TEXT, offset INTEGER,
 size INTEGER NOT NULL CHECK(size >= 0), extent INTEGER, detected_format TEXT NOT NULL,
 content_hash TEXT, decoder TEXT, decoder_version TEXT, status TEXT NOT NULL,
 output_path TEXT, category TEXT NOT NULL DEFAULT 'disc', error TEXT,
 mapping_evidence TEXT, confidence TEXT
);
CREATE TABLE IF NOT EXISTS associations (
 source_id TEXT NOT NULL REFERENCES resources(id), target_id TEXT NOT NULL REFERENCES resources(id),
 kind TEXT NOT NULL, evidence TEXT, confidence TEXT, PRIMARY KEY(source_id,target_id,kind)
);
CREATE TABLE IF NOT EXISTS resource_labels (
 resource_id TEXT NOT NULL REFERENCES resources(id), kind TEXT NOT NULL, value TEXT NOT NULL,
 evidence TEXT, confidence TEXT, PRIMARY KEY(resource_id,kind)
);
CREATE TABLE IF NOT EXISTS logs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_resources_path ON resources(original_path);
CREATE INDEX IF NOT EXISTS idx_resources_category ON resources(category);
"""


class Catalogue:
    def __init__(self, project_dir: Path):
        self.project_dir = project_dir.resolve()
        self.db_path = self.project_dir / "catalogue.sqlite3"

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self):
        self.project_dir.mkdir(parents=True, exist_ok=True)
        (self.project_dir / "exports").mkdir(exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)
            db.execute("INSERT OR REPLACE INTO project_meta VALUES (?,?)", ("schema_version", str(SCHEMA_VERSION)))

    def set_meta(self, **values):
        with self.connect() as db:
            db.executemany("INSERT OR REPLACE INTO project_meta VALUES (?,?)",
                           [(key, json.dumps(value)) for key, value in values.items()])

    def meta(self) -> dict:
        with self.connect() as db:
            return {row["key"]: json.loads(row["value"]) for row in db.execute("SELECT * FROM project_meta")}

    def replace_inventory(self, rows):
        with self.connect() as db:
            db.execute("DELETE FROM resource_labels")
            db.execute("DELETE FROM associations")
            db.execute("DELETE FROM resources")
            db.executemany("""INSERT INTO resources
              (id,original_path,parent_path,offset,size,extent,detected_format,content_hash,status,category)
              VALUES (?,?,?,?,?,?,?,?,?,?)""", rows)

    def add_resources(self, rows, associations=()):
        with self.connect() as db:
            db.executemany("""INSERT INTO resources
              (id,original_path,parent_path,offset,size,extent,detected_format,content_hash,
               decoder,decoder_version,status,category,mapping_evidence,confidence)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
            db.executemany("""INSERT INTO associations
              (source_id,target_id,kind,evidence,confidence) VALUES (?,?,?,?,?)""", associations)

    def update_resource_analysis(self, rows):
        with self.connect() as db:
            db.executemany("""UPDATE resources SET detected_format=?,decoder=?,decoder_version=?,
              status=?,mapping_evidence=?,confidence=? WHERE id=?""", rows)

    def resources(self, category: str | None = None):
        with self.connect() as db:
            sql, args = "SELECT * FROM resources", ()
            if category:
                sql, args = sql + " WHERE category=?", (category,)
            return list(db.execute(sql + " ORDER BY original_path", args))

    def associations(self):
        with self.connect() as db:
            return list(db.execute("SELECT * FROM associations ORDER BY source_id,target_id,kind"))

    def add_labels(self, rows):
        with self.connect() as db:
            db.executemany("""INSERT OR REPLACE INTO resource_labels
              (resource_id,kind,value,evidence,confidence) VALUES (?,?,?,?,?)""", rows)

    def labels(self):
        with self.connect() as db:
            try:
                return list(db.execute("SELECT * FROM resource_labels ORDER BY resource_id,kind"))
            except sqlite3.OperationalError as exc:
                if "no such table" not in str(exc):
                    raise
                return []

    def log(self, level: str, message: str):
        with self.connect() as db:
            db.execute("INSERT INTO logs(created_at,level,message) VALUES (?,?,?)",
                       (datetime.now(timezone.utc).isoformat(), level, message))

    def logs(self):
        with self.connect() as db:
            return list(db.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 500"))


def open_catalogue(path: Path) -> Catalogue:
    catalogue = Catalogue(path)
    if not catalogue.db_path.is_file():
        raise FileNotFoundError(f"Not a DW1 analysis project: {path}")
    meta = catalogue.meta()
    if int(meta.get("schema_version", 0)) != SCHEMA_VERSION:
        raise ValueError("Unsupported project catalogue schema.")
    return catalogue
