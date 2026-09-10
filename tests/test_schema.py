from collector.db import init_db, connect


def test_schema_initialises():
    init_db()
    with connect() as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"jobs", "card_captures", "queries", "job_query_hits", "job_status_events"} <= tables
