CREATE TABLE IF NOT EXISTS http_cache(key TEXT PRIMARY KEY, url TEXT, etag TEXT, last_modified TEXT, fetched_at TEXT NOT NULL, status INTEGER, body BLOB);
