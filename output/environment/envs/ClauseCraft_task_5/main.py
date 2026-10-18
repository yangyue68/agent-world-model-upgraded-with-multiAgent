import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Path, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class ClauseSearchResult(BaseModel):
    id: int
    title: str


class UpdateNoticePeriodRequest(BaseModel):
    new_notice_period: str


class ClauseVersionResponse(BaseModel):
    id: int
    clause_id: int
    version: str
    content: str
    notice_period: str | None
    last_reviewed_at: str
    created_at: str
    updated_at: str


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        # Handle ISO format with Z suffix
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        pass
    # Try common formats
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _format_datetime(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _increment_version(version_str: str) -> str:
    parts = version_str.split(".")
    if len(parts) == 2:
        major, minor = parts
        minor_int = int(minor)
        return f"{major}.{minor_int + 1}"
    elif len(parts) == 3:
        major, minor, patch = parts
        patch_int = int(patch)
        return f"{major}.{minor}.{patch_int + 1}"
    else:
        raise ValueError(f"Invalid version format: {version_str}")


@app.get("/clauses", response_model=list[ClauseSearchResult], operation_id="find_clauses_by_title")
def find_clauses_by_title(
    title: str = Query(..., description="Clause title to search for"),
):
    sql = text("""
        SELECT id, title FROM clauses WHERE title LIKE :pattern ORDER BY id ASC
    """)
    pattern = f"%{title}%"
    with engine.connect() as conn:
        rows = conn.execute(sql, {"pattern": pattern}).fetchall()
    return [{"id": row.id, "title": row.title} for row in rows]


@app.put(
    "/clauses/{clause_id}/versions/{version}",
    response_model=ClauseVersionResponse,
    operation_id="update_clause_version",
)
def update_clause_version(
    clause_id: int = Path(..., description="Internal ID of the clause to update"),
    version: str = Path(..., description="Current version string of the clause to update"),
    body: UpdateNoticePeriodRequest = ...,
):
    # Fetch the current version record
    select_sql = text("""
        SELECT cv.id, cv.clause_id, cv.version, cv.content, cv.notice_period,
               cv.last_reviewed_at, cv.created_at, cv.updated_at
        FROM clause_versions cv
        JOIN clauses c ON cv.clause_id = c.id
        WHERE cv.clause_id = :clause_id AND cv.version = :version
    """)
    params = {"clause_id": clause_id, "version": version}
    
    with engine.begin() as conn:
        row = conn.execute(select_sql, params).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Clause version not found")
        
        # Verify this is the latest version by checking if there's a higher version
        check_latest_sql = text("""
            SELECT COUNT(*) as cnt FROM clause_versions 
            WHERE clause_id = :clause_id AND version > :version
        """)
        count_row = conn.execute(check_latest_sql, {"clause_id": clause_id, "version": version}).fetchone()
        if count_row.cnt > 0:
            raise HTTPException(status_code=400, detail="Cannot update non-latest version via PUT. Use POST to create a new version.")
        
        # Increment version
        new_version = _increment_version(version)
        
        now = datetime.now(timezone.utc)
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        
        # Update the existing record: change version, notice_period, last_reviewed_at, updated_at
        update_sql = text("""
            UPDATE clause_versions
            SET version = :new_version,
                notice_period = :notice_period,
                last_reviewed_at = :last_reviewed_at,
                updated_at = :updated_at
            WHERE id = :id
        """)
        conn.execute(update_sql, {
            "new_version": new_version,
            "notice_period": body.new_notice_period,
            "last_reviewed_at": now_str,
            "updated_at": now_str,
            "id": row.id,
        })
        
        # Also update the clauses table updated_at
        update_clause_sql = text("""
            UPDATE clauses SET updated_at = :updated_at WHERE id = :clause_id
        """)
        conn.execute(update_clause_sql, {"updated_at": now_str, "clause_id": clause_id})
        
        # Fetch the updated record
        result_row = conn.execute(select_sql, {"clause_id": clause_id, "version": new_version}).fetchone()
        if result_row is None:
            raise HTTPException(status_code=500, detail="Failed to retrieve updated clause version")
        
        return ClauseVersionResponse(
            id=result_row.id,
            clause_id=result_row.clause_id,
            version=result_row.version,
            content=result_row.content,
            notice_period=result_row.notice_period,
            last_reviewed_at=_format_datetime(_parse_datetime(result_row.last_reviewed_at)) or "",
            created_at=_format_datetime(_parse_datetime(result_row.created_at)) or "",
            updated_at=_format_datetime(_parse_datetime(result_row.updated_at)) or "",
        )


@app.post(
    "/clauses/{clause_id}/versions",
    response_model=ClauseVersionResponse,
    operation_id="create_new_clause_version",
)
def create_new_clause_version(
    clause_id: int = Path(..., description="Internal ID of the clause to create a new version for"),
    body: UpdateNoticePeriodRequest = ...,
):
    # Find the latest version of the clause
    
    # Simpler approach: fetch all versions and sort in Python
    all_versions_sql = text("""
        SELECT id, clause_id, version, content, notice_period, last_reviewed_at, created_at, updated_at
        FROM clause_versions
        WHERE clause_id = :clause_id
    """)
    
    with engine.begin() as conn:
        rows = conn.execute(all_versions_sql, {"clause_id": clause_id}).fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail="No versions found for this clause")
        
        # Sort by version numerically
        def version_key(v: str):
            parts = v.split(".")
            return tuple(int(p) for p in parts)
        
        sorted_rows = sorted(rows, key=lambda r: version_key(r.version))
        latest = sorted_rows[-1]
        
        new_version = _increment_version(latest.version)
        now = datetime.now(timezone.utc)
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        
        insert_sql = text("""
            INSERT INTO clause_versions (clause_id, version, content, notice_period, last_reviewed_at, created_at, updated_at)
            VALUES (:clause_id, :version, :content, :notice_period, :last_reviewed_at, :created_at, :updated_at)
        """)
        conn.execute(insert_sql, {
            "clause_id": clause_id,
            "version": new_version,
            "content": latest.content,
            "notice_period": body.new_notice_period,
            "last_reviewed_at": now_str,
            "created_at": now_str,
            "updated_at": now_str,
        })
        
        # Get the inserted ID
        get_id_sql = text("""
            SELECT id FROM clause_versions WHERE clause_id = :clause_id AND version = :version
        """)
        new_id_row = conn.execute(get_id_sql, {"clause_id": clause_id, "version": new_version}).fetchone()
        
        # Update clauses table
        update_clause_sql = text("""
            UPDATE clauses SET updated_at = :updated_at WHERE id = :clause_id
        """)
        conn.execute(update_clause_sql, {"updated_at": now_str, "clause_id": clause_id})
        
        return ClauseVersionResponse(
            id=new_id_row.id,
            clause_id=clause_id,
            version=new_version,
            content=latest.content,
            notice_period=body.new_notice_period,
            last_reviewed_at=now_str,
            created_at=now_str,
            updated_at=now_str,
        )
