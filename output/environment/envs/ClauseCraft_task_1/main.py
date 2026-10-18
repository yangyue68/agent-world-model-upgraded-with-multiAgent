import os
from typing import List, Optional

from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class ClauseSearchResult(BaseModel):
    title: str
    last_reviewed_at: str


@app.get("/clauses/search", operation_id="search_gdpr_clauses", response_model=List[ClauseSearchResult])
def search_gdpr_clauses():
    """
    Search GDPR clauses with specific tags, risk level, and jurisdiction.
    
    Find clauses tagged 'GDPR' and 'data processing', with risk level 'high' 
    and jurisdiction 'EU', returning the two most recently reviewed clause 
    titles and last reviewed dates.
    """
    db = SessionLocal()
    try:
        # Query to find clauses that have both 'GDPR' and 'data processing' tags,
        # with risk_level='high' and jurisdiction='EU', ordered by last_reviewed_at DESC, limit 2
        
        query = text("""
            SELECT c.title, c.last_reviewed_at
            FROM clauses c
            INNER JOIN clause_tags ct1 ON c.id = ct1.clause_id
            INNER JOIN tags t1 ON ct1.tag_id = t1.id AND t1.name = 'GDPR'
            INNER JOIN clause_tags ct2 ON c.id = ct2.clause_id
            INNER JOIN tags t2 ON ct2.tag_id = t2.id AND t2.name = 'data processing'
            WHERE c.risk_level = 'high'
              AND c.jurisdiction = 'EU'
              AND c.last_reviewed_at IS NOT NULL
            ORDER BY c.last_reviewed_at DESC
            LIMIT 2
        """)
        
        result = db.execute(query).fetchall()
        
        items = []
        for row in result:
            items.append(ClauseSearchResult(
                title=row[0],
                last_reviewed_at=row[1] if isinstance(row[1], str) else str(row[1])
            ))
        
        return items
    finally:
        db.close()
