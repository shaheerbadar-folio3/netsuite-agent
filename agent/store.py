import json
import os
import sqlite3
import time
from pathlib import Path


class Store:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root = root
        self.db = sqlite3.connect(root / "agent.sqlite3", check_same_thread=False)
        os.chmod(root / "agent.sqlite3", 0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS completions
            (job TEXT PRIMARY KEY, result TEXT NOT NULL, created REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS audit
            (id INTEGER PRIMARY KEY, job TEXT, event TEXT, created REAL);
        """)
        self.db.commit()

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (key, json.dumps(value)))
        self.db.commit()

    def cached_result(self, job):
        row = self.db.execute("SELECT result FROM completions WHERE job=?", (str(job),)).fetchone()
        return json.loads(row[0]) if row else None

    def complete(self, job, result):
        self.db.execute("INSERT OR REPLACE INTO completions VALUES (?, ?, ?)",
                        (str(job), json.dumps(result), time.time()))
        self.db.commit()

    def audit(self, job, event):
        # Metadata only: prompts, returned rows, and credentials never enter audit logs.
        self.db.execute("INSERT INTO audit(job,event,created) VALUES (?,?,?)", (str(job), event, time.time()))
        self.db.commit()

    def purge(self, days):
        before = time.time() - days * 86400
        self.db.execute("DELETE FROM completions WHERE created < ?", (before,))
        self.db.execute("DELETE FROM audit WHERE created < ?", (before,))
        self.db.commit()

    def close(self):
        self.db.close()
