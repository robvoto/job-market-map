PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    source_job_id TEXT,
    canonical_url TEXT NOT NULL,
    title TEXT,
    employer TEXT,
    location TEXT,
    salary_text TEXT,
    employment_type TEXT,
    workplace_type TEXT,
    posted_text TEXT,
    posted_at TEXT,
    reposted INTEGER NOT NULL DEFAULT 0,
    applicant_count INTEGER,
    easy_apply INTEGER,
    teaser_text TEXT,
    raw_card_text TEXT,
    classification_text TEXT,
    subclassification_text TEXT,
    card_tags_json TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    capture_count INTEGER NOT NULL DEFAULT 1,
    possible_same_job_group TEXT,
    core_fingerprint TEXT,
    exact_card_fingerprint TEXT,
    shown_to_rob INTEGER NOT NULL DEFAULT 0,
    reviewed INTEGER NOT NULL DEFAULT 0,
    applied INTEGER NOT NULL DEFAULT 0,
    rejected INTEGER NOT NULL DEFAULT 0,
    dismissed INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    compacted_at TEXT,
    UNIQUE(source, source_job_id),
    UNIQUE(source, canonical_url)
);

CREATE TABLE IF NOT EXISTS card_captures (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    captured_at TEXT NOT NULL,
    query_id INTEGER,
    rank INTEGER,
    page_number INTEGER,
    raw_card_text TEXT,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS queries (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    query_text TEXT NOT NULL,
    location TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    registry_key TEXT,
    origins_json TEXT,
    last_run_at TEXT,
    cards_observed INTEGER NOT NULL DEFAULT 0,
    unique_new_jobs INTEGER NOT NULL DEFAULT 0,
    duplicate_hits INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    UNIQUE(source, query_text, location)
);

CREATE TABLE IF NOT EXISTS job_query_hits (
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    query_id INTEGER NOT NULL REFERENCES queries(id) ON DELETE CASCADE,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    hit_count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(job_id, query_id)
);

CREATE TABLE IF NOT EXISTS job_status_events (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    event_value INTEGER NOT NULL,
    occurred_at TEXT NOT NULL,
    actor TEXT,
    note TEXT,
    idempotency_key TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_title ON jobs(title);
CREATE INDEX IF NOT EXISTS idx_jobs_employer ON jobs(employer);
CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_jobs_last_seen ON jobs(last_seen_at);
CREATE INDEX IF NOT EXISTS idx_jobs_flags ON jobs(shown_to_rob, reviewed, applied, rejected, dismissed);

CREATE TABLE IF NOT EXISTS collection_runs (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    query_id INTEGER REFERENCES queries(id) ON DELETE SET NULL,
    query_text TEXT NOT NULL,
    location TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    pages_requested INTEGER NOT NULL DEFAULT 0,
    pages_parsed INTEGER NOT NULL DEFAULT 0,
    cards_observed INTEGER NOT NULL DEFAULT 0,
    unique_new_jobs INTEGER NOT NULL DEFAULT 0,
    duplicate_observations INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_collection_runs_source_started ON collection_runs(source, started_at);

CREATE TABLE IF NOT EXISTS collection_cursors (
    source TEXT NOT NULL,
    query_text TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    cursor_value INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'PENDING',
    updated_at TEXT NOT NULL,
    last_job_id TEXT,
    total_results_hint INTEGER,
    PRIMARY KEY(source, query_text, location)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    value_type TEXT NOT NULL,
    value_json TEXT NOT NULL,
    default_json TEXT NOT NULL,
    min_value REAL,
    max_value REAL,
    help_text TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT
);

CREATE TABLE IF NOT EXISTS duplicate_links (
    job_id_a INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    job_id_b INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    confidence REAL NOT NULL,
    match_type TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    PRIMARY KEY(job_id_a, job_id_b),
    CHECK(job_id_a < job_id_b)
);

CREATE INDEX IF NOT EXISTS idx_duplicate_links_confidence ON duplicate_links(confidence DESC);

CREATE TABLE IF NOT EXISTS job_tombstones (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    source_job_id TEXT,
    canonical_url TEXT NOT NULL,
    title TEXT,
    employer TEXT,
    location TEXT,
    core_fingerprint TEXT,
    exact_card_fingerprint TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    capture_count INTEGER NOT NULL DEFAULT 0,
    shown_to_rob INTEGER NOT NULL DEFAULT 0,
    reviewed INTEGER NOT NULL DEFAULT 0,
    applied INTEGER NOT NULL DEFAULT 0,
    rejected INTEGER NOT NULL DEFAULT 0,
    dismissed INTEGER NOT NULL DEFAULT 0,
    query_history_json TEXT,
    removed_at TEXT NOT NULL,
    UNIQUE(source, source_job_id),
    UNIQUE(source, canonical_url)
);

CREATE INDEX IF NOT EXISTS idx_job_tombstones_core ON job_tombstones(core_fingerprint);
CREATE INDEX IF NOT EXISTS idx_job_tombstones_last_seen ON job_tombstones(last_seen_at);
