PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    source_job_id TEXT,
    identity_key TEXT,
    canonical_url TEXT NOT NULL,
    title TEXT,
    employer TEXT,
    location TEXT,
    geography_code TEXT,
    salary_text TEXT,
    employment_type TEXT,
    workplace_type TEXT,
    posted_at TEXT,
    expires_at TEXT,
    source_status TEXT,
    apply_method TEXT,
    reposted INTEGER NOT NULL DEFAULT 0,
    applicant_count INTEGER,
    easy_apply INTEGER,
    teaser_text TEXT,
    raw_card_text TEXT,
    full_description TEXT,
    jd_fetched_at TEXT,
    jd_source TEXT,
    classification_text TEXT,
    subclassification_text TEXT,
    card_tags_json TEXT,
    possible_same_job_group TEXT,
    core_fingerprint TEXT,
    exact_card_fingerprint TEXT,
    UNIQUE(identity_key),
    UNIQUE(source, source_job_id),
    UNIQUE(source, canonical_url)
);

CREATE TABLE IF NOT EXISTS job_observation_state (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    capture_count INTEGER NOT NULL DEFAULT 1,
    archived INTEGER NOT NULL DEFAULT 0,
    compacted_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_job_observation_first_seen
    ON job_observation_state(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_job_observation_last_seen
    ON job_observation_state(last_seen_at);
CREATE INDEX IF NOT EXISTS idx_job_observation_archived_last_seen
    ON job_observation_state(archived, last_seen_at);

CREATE TABLE IF NOT EXISTS jd_fetch_registry (
    identity_key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_job_id TEXT,
    canonical_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    jd_source TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jd_fetch_registry_source_id
    ON jd_fetch_registry(source, source_job_id);

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
    geography_code TEXT,
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

CREATE INDEX IF NOT EXISTS idx_jobs_title ON jobs(title);
CREATE INDEX IF NOT EXISTS idx_jobs_employer ON jobs(employer);


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
    cycle_key TEXT,
    PRIMARY KEY(source, query_text, location)
);

CREATE TABLE IF NOT EXISTS source_campaign_state (
    source TEXT PRIMARY KEY,
    cycle_key TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
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
    identity_key TEXT,
    canonical_url TEXT NOT NULL,
    title TEXT,
    employer TEXT,
    location TEXT,
    core_fingerprint TEXT,
    exact_card_fingerprint TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    capture_count INTEGER NOT NULL DEFAULT 0,
    query_history_json TEXT,
    removed_at TEXT NOT NULL,
    UNIQUE(identity_key),
    UNIQUE(source, source_job_id),
    UNIQUE(source, canonical_url)
);

CREATE INDEX IF NOT EXISTS idx_job_tombstones_core ON job_tombstones(core_fingerprint);
CREATE INDEX IF NOT EXISTS idx_job_tombstones_last_seen ON job_tombstones(last_seen_at);

CREATE TABLE IF NOT EXISTS geographies (
    code TEXT PRIMARY KEY,
    country TEXT NOT NULL,
    label TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    seek_location TEXT NOT NULL,
    seek_state_slug TEXT NOT NULL,
    linkedin_location TEXT NOT NULL,
    help_text TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT
);

CREATE TABLE IF NOT EXISTS seek_partitions (
    id INTEGER PRIMARY KEY,
    geography_code TEXT NOT NULL,
    parent_id INTEGER REFERENCES seek_partitions(id) ON DELETE CASCADE,
    level TEXT NOT NULL,
    label TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'PENDING',
    reported_results INTEGER,
    collected_unique_jobs INTEGER NOT NULL DEFAULT 0,
    child_count INTEGER NOT NULL DEFAULT 0,
    max_results_threshold INTEGER NOT NULL,
    first_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS seek_partition_jobs (
    partition_id INTEGER NOT NULL REFERENCES seek_partitions(id) ON DELETE CASCADE,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    first_seen_at TEXT NOT NULL,
    PRIMARY KEY(partition_id, job_id)
);

CREATE INDEX IF NOT EXISTS idx_seek_partitions_geo_status ON seek_partitions(geography_code, status);
CREATE INDEX IF NOT EXISTS idx_seek_partition_jobs_job ON seek_partition_jobs(job_id);

CREATE TABLE IF NOT EXISTS consumer_checkpoints (
    consumer_key TEXT PRIMARY KEY,
    last_job_id INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    note TEXT
);

-- Long-running collection/service control. This is neutral operational state,
-- not user/job activity.
CREATE TABLE IF NOT EXISTS market_collection_runs (
    id INTEGER PRIMARY KEY,
    trigger TEXT NOT NULL,
    source_scope TEXT NOT NULL,
    run_kind TEXT NOT NULL DEFAULT 'normal',
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    pid INTEGER,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    backup_path TEXT,
    states_json TEXT,
    stats_json TEXT,
    message TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_market_collection_runs_started
    ON market_collection_runs(started_at DESC);

CREATE TABLE IF NOT EXISTS scheduler_state (
    id INTEGER PRIMARY KEY CHECK(id=1),
    heartbeat_at TEXT,
    last_attempt_local_date TEXT,
    last_started_at TEXT,
    last_finished_at TEXT,
    last_status TEXT,
    last_message TEXT
);

INSERT OR IGNORE INTO scheduler_state(id) VALUES(1);

CREATE TABLE IF NOT EXISTS seek_coverage_history (
    id INTEGER PRIMARY KEY,
    captured_at TEXT NOT NULL,
    geography_code TEXT NOT NULL,
    root_status TEXT NOT NULL,
    reported_results INTEGER,
    covered_unique_jobs INTEGER NOT NULL DEFAULT 0,
    incomplete_partitions INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_seek_coverage_history_geo_time
    ON seek_coverage_history(geography_code, captured_at DESC);
