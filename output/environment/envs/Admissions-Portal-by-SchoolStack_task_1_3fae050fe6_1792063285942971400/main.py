import os
from typing import List, Optional

from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

# Determine if it's a SQLite URL and strip prefix for sqlite3 compatibility if needed, 
# but SQLAlchemy handles 'sqlite:///' directly.
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()

class ApplicantResponse(BaseModel):
    full_name: str
    email: str

@app.get("/applicants/search", response_model=List[ApplicantResponse], operation_id="search_applicants_by_criteria")
def search_applicants_by_criteria(
    camp_name: str = Query(..., description="The name of the camp (e.g., 'Robotics Camp')."),
    term: str = Query(..., description="The term of the camp (e.g., 'Summer')."),
    year: int = Query(..., description="The year of the camp (e.g., 2025)."),
    application_status: Optional[str] = Query(None, description="Filter by application status (e.g., 'Incomplete'). If omitted, no status filter is applied."),
    missing_document_type: Optional[str] = Query(None, description="Filter for applicants who do NOT have this specific document type uploaded (e.g., 'Recommendation Letter'). If omitted, no missing document filter is applied.")
):
    db = SessionLocal()
    try:
        # Base query joining users, applicants, camps
        # We need to find applicants for a specific camp
        sql = """
            SELECT u.full_name, u.email
            FROM applicants a
            JOIN users u ON a.user_id = u.id
            JOIN camps c ON a.camp_id = c.id
            WHERE c.name = :camp_name AND c.term = :term AND c.year = :year
        """
        
        params = {
            "camp_name": camp_name,
            "term": term,
            "year": year
        }

        if application_status:
            sql += " AND a.application_status = :application_status"
            params["application_status"] = application_status

        if missing_document_type:
            # Filter for applicants who do NOT have this specific document type
            # Use NOT EXISTS subquery
            sql += """
                AND NOT EXISTS (
                    SELECT 1 
                    FROM documents d 
                    WHERE d.applicant_id = a.id 
                    AND d.document_type = :missing_document_type
                )
            """
            params["missing_document_type"] = missing_document_type
        
        result = db.execute(text(sql), params)
        rows = result.fetchall()
        
        return [ApplicantResponse(full_name=row[0], email=row[1]) for row in rows]
    finally:
        db.close()
