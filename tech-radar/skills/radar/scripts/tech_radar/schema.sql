CREATE TABLE runs (
  run_id TEXT PRIMARY KEY,                 -- e.g. 20261029T070000Z-daily
  mode TEXT NOT NULL CHECK (mode IN ('hourly','daily','weekly','monthly','bootstrap','adhoc')),
  requested_mode TEXT NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT,
  status TEXT NOT NULL CHECK (status IN ('running','ok','degraded','failed')),
  t3_thread_id TEXT, model TEXT,
  usage_json TEXT, counts_json TEXT, error TEXT
);
CREATE TABLE entities (
  entity_id TEXT PRIMARY KEY,              -- pkg:pypi/litellm, gh:owner/repo, brew:formula/x, brew:cask/x, gha:owner/action, mcp:name, skill:owner/repo/name, model:provider/id, technique:slug, app:bundle-id, svc:slug
  kind TEXT NOT NULL CHECK (kind IN ('package','repo','cli','app','action','mcp','skill','plugin','model','technique','service','runtime')),
  ecosystem TEXT, name TEXT NOT NULL, repo TEXT, homepage TEXT, quadrant TEXT,
  visibility TEXT NOT NULL DEFAULT 'unknown' CHECK (visibility IN ('public','private','unknown')),
  created_at TEXT NOT NULL
);
CREATE TABLE entity_aliases (alias TEXT PRIMARY KEY, entity_id TEXT NOT NULL REFERENCES entities ON DELETE CASCADE);
CREATE TABLE projects (
  project_key TEXT PRIMARY KEY,            -- normalized origin URL (scheme/host/owner/repo, lowercase, no .git) else 'local:'||sha256(realpath)
  display_path TEXT NOT NULL, main_worktree TEXT NOT NULL,
  last_commit_at TEXT, active INTEGER NOT NULL, renovate_json TEXT, excluded INTEGER NOT NULL DEFAULT 0,
  root_source TEXT NOT NULL CHECK (root_source IN ('active_projects','launchd','dotfiles','manual')),
  visibility TEXT NOT NULL DEFAULT 'unknown' CHECK (visibility IN ('public','private','unknown')),
  alias TEXT UNIQUE,                       -- 'private-project-N' for non-public projects, public slug otherwise (spec 12 A8)
  manifest_files_json TEXT                 -- absolute manifest/lockfile paths + mtime/size, for hourly refresh (spec 12 A27)
);
CREATE TABLE usages (
  entity_id TEXT NOT NULL REFERENCES entities ON DELETE CASCADE,
  context TEXT NOT NULL,                   -- system:brew, system:mise, project:<key>, launchd:<label>, harness:claude, harness:t3
  version TEXT, direct INTEGER NOT NULL,
  first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, attrs_json TEXT,   -- attrs_json.source_file = absolute manifest path (A27)
  PRIMARY KEY (entity_id, context)
);
CREATE TABLE fingerprints (
  context TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('cmd','flag','subcommand','config_key','import','api','model_id','tool_name')),
  token TEXT NOT NULL, count INTEGER NOT NULL, last_seen TEXT NOT NULL,
  PRIMARY KEY (context, kind, token)
);
CREATE TABLE metrics (
  entity_id TEXT NOT NULL REFERENCES entities ON DELETE CASCADE,
  metric TEXT NOT NULL, value REAL, observed_at TEXT NOT NULL,
  PRIMARY KEY (entity_id, metric, observed_at)
);
CREATE TABLE signals (
  signal_id INTEGER PRIMARY KEY,
  entity_id TEXT REFERENCES entities ON DELETE SET NULL,
  source_id TEXT NOT NULL,
  type TEXT NOT NULL CHECK (type IN ('feature','compromise','kev','lifecycle','contender','horizon','technique','pricing','inventory_change')),
  confidence TEXT NOT NULL DEFAULT 'confirmed' CHECK (confidence IN ('confirmed','unconfirmed')),
  external_id TEXT, title TEXT NOT NULL, url TEXT, published_at TEXT, fetched_at TEXT NOT NULL,
  untrusted_excerpt TEXT, payload_json TEXT,
  dedupe_key TEXT NOT NULL UNIQUE, last_surfaced_at TEXT, withdrawn INTEGER NOT NULL DEFAULT 0
);
CREATE VIRTUAL TABLE signals_fts USING fts5(title, untrusted_excerpt, content='signals', content_rowid='signal_id');
CREATE TRIGGER signals_ai AFTER INSERT ON signals BEGIN
  INSERT INTO signals_fts(rowid, title, untrusted_excerpt) VALUES (new.signal_id, new.title, new.untrusted_excerpt); END;
CREATE TRIGGER signals_ad AFTER DELETE ON signals BEGIN
  INSERT INTO signals_fts(signals_fts, rowid, title, untrusted_excerpt) VALUES ('delete', old.signal_id, old.title, old.untrusted_excerpt); END;
CREATE TRIGGER signals_au AFTER UPDATE ON signals BEGIN
  INSERT INTO signals_fts(signals_fts, rowid, title, untrusted_excerpt) VALUES ('delete', old.signal_id, old.title, old.untrusted_excerpt);
  INSERT INTO signals_fts(rowid, title, untrusted_excerpt) VALUES (new.signal_id, new.title, new.untrusted_excerpt); END;
CREATE TABLE items (
  item_id TEXT PRIMARY KEY,                -- R-0001 (stable, sequential)
  entity_id TEXT NOT NULL UNIQUE REFERENCES entities,
  ring TEXT CHECK (ring IN ('adopt','trial','assess','hold','watch')),
  quadrant TEXT,
  state TEXT NOT NULL CHECK (state IN ('candidate','active','rejected','snoozed')),
  in_use INTEGER NOT NULL DEFAULT 0,
  momentum REAL, quality REAL, quality_adj REAL, fit REAL, total REAL,
  confidence TEXT CHECK (confidence IN ('high','medium','low')),
  gates_json TEXT, red_flags_json TEXT, reported_json TEXT,   -- reported_json = computed-but-non-gating criteria (spec 05)
  rationale TEXT, last_deep_inspect_at TEXT, created_at TEXT NOT NULL
);
CREATE TABLE ring_history (
  item_id TEXT NOT NULL REFERENCES items, run_id TEXT NOT NULL REFERENCES runs,
  from_ring TEXT, to_ring TEXT, at TEXT NOT NULL, reason TEXT
);
CREATE TABLE decisions (
  decision_id INTEGER PRIMARY KEY,
  item_id TEXT NOT NULL REFERENCES items,
  verb TEXT NOT NULL CHECK (verb IN ('adopt','trial','assess','hold','reject','snooze')),
  reason TEXT,
  channel TEXT NOT NULL CHECK (channel IN ('thread','decisions_md','cli','bootstrap')),
  actor TEXT NOT NULL CHECK (actor IN ('user')),   -- agents cannot author decisions (spec 06 T12)
  created_at TEXT NOT NULL, expires_at TEXT, wake_triggers_json TEXT,
  superseded_by INTEGER REFERENCES decisions
);
CREATE TABLE alerts (
  alert_id INTEGER PRIMARY KEY,
  tier TEXT NOT NULL CHECK (tier IN ('P0','P1','P2','P3')),
  kind TEXT NOT NULL CHECK (kind IN ('compromise','incident','kev','integrity','app_integrity','lifecycle','chatter','feature')),
  entity_id TEXT REFERENCES entities, signal_id INTEGER REFERENCES signals, project_key TEXT,
  confidence TEXT NOT NULL CHECK (confidence IN ('confirmed','unconfirmed')),
  state TEXT NOT NULL CHECK (state IN ('open','acked','resolved','false_positive','withdrawn')),
  dedupe_key TEXT NOT NULL UNIQUE, raised_at TEXT NOT NULL, notified_at TEXT,
  notify_attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
  payload_json TEXT                        -- advisory id, published/modified dates, exposure report path, rotation checklist
);
CREATE TABLE pending (                     -- staged agent-reachable changes (spec 12 A41)
  pending_id INTEGER PRIMARY KEY,
  batch_id INTEGER REFERENCES confirm_batches,
  kind TEXT NOT NULL CHECK (kind IN ('decision','feedback','taste')),
  payload_json TEXT NOT NULL,             -- taste: {"content": <proposed file text>, "sha256": <hex>}; confirm applies only that content
  channel TEXT NOT NULL CHECK (channel IN ('thread','decisions_md')),
  created_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('staged','batched','applied','cancelled','expired'))
);
CREATE TABLE confirm_batches (
  batch_id INTEGER PRIMARY KEY,
  code_hash TEXT NOT NULL, salt TEXT NOT NULL,       -- sha256(salt+code); the code itself is never stored
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL CHECK (status IN ('open','confirmed','cancelled','expired'))
);
CREATE TABLE digests (run_id TEXT PRIMARY KEY REFERENCES runs, path TEXT NOT NULL, markdown TEXT NOT NULL);
CREATE VIRTUAL TABLE digests_fts USING fts5(markdown, content='digests', content_rowid='rowid');
CREATE TRIGGER digests_ai AFTER INSERT ON digests BEGIN
  INSERT INTO digests_fts(rowid, markdown) VALUES (new.rowid, new.markdown); END;
CREATE TRIGGER digests_ad AFTER DELETE ON digests BEGIN
  INSERT INTO digests_fts(digests_fts, rowid, markdown) VALUES ('delete', old.rowid, old.markdown); END;
CREATE TRIGGER digests_au AFTER UPDATE ON digests BEGIN
  INSERT INTO digests_fts(digests_fts, rowid, markdown) VALUES ('delete', old.rowid, old.markdown);
  INSERT INTO digests_fts(rowid, markdown) VALUES (new.rowid, new.markdown); END;
CREATE TABLE digest_items (
  run_id TEXT NOT NULL REFERENCES runs, ref TEXT NOT NULL, section TEXT NOT NULL, rank INTEGER NOT NULL,
  feedback TEXT CHECK (feedback IN ('useful','not_useful','acted')),
  PRIMARY KEY (run_id, ref)
);
CREATE TABLE source_health (
  source_id TEXT PRIMARY KEY, last_ok_at TEXT, last_error TEXT,
  consecutive_failures INTEGER NOT NULL DEFAULT 0, last_item_count INTEGER, skipped_reason TEXT
);
CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);  -- decisions_md_hash, last_hourly_at, app_version, baseline_done, last_seen cursors
CREATE INDEX usages_context ON usages(context);
CREATE INDEX signals_entity ON signals(entity_id, type);
CREATE INDEX items_rotation ON items(state, last_deep_inspect_at);
