import os
from typing import Optional, List

from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)


class Author(BaseModel):
    id: int
    username: str
    display_name: Optional[str] = None


class ClauseResponse(BaseModel):
    id: int
    title: str
    version_number: int
    body: str
    author: Author
    last_reviewed_at: Optional[str] = None


app = FastAPI()


@app.get("/clauses/latest", operation_id="get_latest_clause_version")
def get_latest_clause_version(title: str = Query(..., description="The exact title of the clause to retrieve")) -> ClauseResponse:
    with engine.connect() as conn:
        # Find the clause by title
        result = conn.execute(
            text("SELECT id FROM clauses WHERE title = :title"),
            {"title": title}
        )
        clause_row = result.fetchone()
        if not clause_row:
            raise HTTPException(status_code=404, detail="Clause not found")
        
        clause_id = clause_row[0]
        
        # Get the latest version (highest version_number) for this clause
        result = conn.execute(
            text("""
                SELECT cv.id, cv.version_number, cv.body, cv.author_id, cv.last_reviewed_at,
                       u.id as user_id, u.username, u.display_name
                FROM clause_versions cv
                JOIN users u ON cv.author_id = u.id
                WHERE cv.clause_id = :clause_id
                ORDER BY cv.version_number DESC
                LIMIT 1
            """),
            {"clause_id": clause_id}
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="No versions found for this clause")
        
        version_id = row[0]
        version_number = row[1]
        body = row[2]
        author_id = row[3]
        last_reviewed_at = row[4]
        user_id = row[5]
        username = row[6]
        display_name = row[7]
        
        # Format last_reviewed_at as ISO string if present
        last_reviewed_str = None
        if last_reviewed_at is not None:
            if hasattr(last_reviewed_at, 'isoformat'):
                last_reviewed_str = last_reviewed_at.isoformat()
            else:
                last_reviewed_str = str(last_reviewed_at)
        
        author = Author(id=user_id, username=username, display_name=display_name)
        
        return ClauseResponse(
            id=version_id,
            title=title,
            version_number=version_number,
            body=body,
            author=author,
            last_reviewed_at=last_reviewed_str
        )
