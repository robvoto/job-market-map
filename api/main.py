from fastapi import FastAPI, HTTPException, Query
from collector.db import connect, init_db

app = FastAPI(title="Job Market Map", version="0.1.0")


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/jobs/search")
def search_jobs(
    q: str | None = None,
    source: str | None = None,
    unseen_only: bool = False,
    limit: int = Query(100, ge=1, le=1000),
):
    clauses = []
    params: list[object] = []
    if q:
        clauses.append("(title LIKE ? OR employer LIKE ? OR raw_card_text LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])
    if source:
        clauses.append("source = ?")
        params.append(source)
    if unseen_only:
        clauses.append("shown_to_rob = 0")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT * FROM jobs {where} ORDER BY first_seen_at DESC LIMIT ?"
    params.append(limit)
    with connect() as conn:
        return [dict(row) for row in conn.execute(sql, params)]


@app.get("/jobs/{job_id}")
def get_job(job_id: int):
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        raise HTTPException(404, "job not found")
    return dict(row)
