import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class DiffLine(BaseModel):
    line_number: int
    type: str  # "added", "removed", "unchanged"
    content: str


class VersionInfo(BaseModel):
    version_number: int
    body: str


class ClauseDiffResponse(BaseModel):
    clause_id: int
    title: str
    version_1: VersionInfo
    version_2: VersionInfo
    diff: List[DiffLine]


class ClauseSummary(BaseModel):
    id: int
    title: str


def compute_diff(old_text: str, new_text: str) -> List[DiffLine]:
    """
    Compute a simple line-by-line diff between two texts.
    Returns a list of DiffLine objects with line numbers relative to the unified view.
    We use difflib.SequenceMatcher for standard diff logic.
    """
    import difflib

    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()

    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
    diff_result = []
    
    current_line_num = 0
    
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            for k in range(i1, i2):
                current_line_num += 1
                diff_result.append(DiffLine(
                    line_number=current_line_num,
                    type="unchanged",
                    content=old_lines[k]
                ))
        elif tag == 'delete':
            for k in range(i1, i2):
                current_line_num += 1
                diff_result.append(DiffLine(
                    line_number=current_line_num,
                    type="removed",
                    content=old_lines[k]
                ))
        elif tag == 'insert':
            for k in range(j1, j2):
                current_line_num += 1
                diff_result.append(DiffLine(
                    line_number=current_line_num,
                    type="added",
                    content=new_lines[k]
                ))
        elif tag == 'replace':
            # Handle replaced lines: usually show removals then additions
            for k in range(i1, i2):
                current_line_num += 1
                diff_result.append(DiffLine(
                    line_number=current_line_num,
                    type="removed",
                    content=old_lines[k]
                ))
            for k in range(j1, j2):
                current_line_num += 1
                diff_result.append(DiffLine(
                    line_number=current_line_num,
                    type="added",
                    content=new_lines[k]
                ))
                
    return diff_result


@app.get("/clauses/{clause_id}/versions/latest-diff", response_model=ClauseDiffResponse, operation_id="get_latest_clause_diff")
def get_latest_clause_diff(clause_id: int):
    with engine.connect() as conn:
        # Get clause info
        stmt_clause = text("SELECT id, title FROM clauses WHERE id = :id")
        result_clause = conn.execute(stmt_clause, {"id": clause_id}).fetchone()
        
        if not result_clause:
            raise HTTPException(status_code=404, detail="Clause not found")
        
        clause_title = result_clause.title
        
        # Get the two latest versions
        stmt_versions = text("""
            SELECT version_number, body 
            FROM clause_versions 
            WHERE clause_id = :cid 
            ORDER BY version_number DESC 
            LIMIT 2
        """)
        results = conn.execute(stmt_versions, {"cid": clause_id}).fetchall()
        
        if len(results) < 2:
            raise HTTPException(status_code=404, detail="Not enough versions to compare")
        
        # results[0] is the latest (newest). results[1] is second highest (older).
        newest_ver = results[0].version_number
        newest_body = results[0].body
        
        older_ver = results[1].version_number
        older_body = results[1].body
        
        # Map to response model
        # version_1: older, version_2: newer
        v1_info = VersionInfo(version_number=older_ver, body=older_body)
        v2_info = VersionInfo(version_number=newest_ver, body=newest_body)
        
        diff_lines = compute_diff(older_body, newest_body)
        
        return ClauseDiffResponse(
            clause_id=clause_id,
            title=clause_title,
            version_1=v1_info,
            version_2=v2_info,
            diff=diff_lines
        )


@app.get("/clauses", response_model=List[ClauseSummary], operation_id="find_clauses")
def find_clauses(title: str = Query(..., description="Clause title")):
    with engine.connect() as conn:
        stmt = text("SELECT id, title FROM clauses WHERE title LIKE :pattern")
        pattern = f"%{title}%"
        results = conn.execute(stmt, {"pattern": pattern}).fetchall()
        
        return [ClauseSummary(id=r.id, title=r.title) for r in results]
