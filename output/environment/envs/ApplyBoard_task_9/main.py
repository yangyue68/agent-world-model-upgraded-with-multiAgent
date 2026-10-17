import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class DecisionItem(BaseModel):
    applicant_id: int
    first_name: str
    last_name: str
    email: str
    decision: str
    decision_date: str


@app.get("/programs/{program_id}/decisions", response_model=List[DecisionItem], operation_id="get_program_decisions")
def get_program_decisions(program_id: int):
    """
    Retrieve decision list for a specific program where the decision is either 'accepted' or 'waitlisted',
    sorted by applicant last name.
    """
    with SessionLocal() as db:
        # Verify program exists
        program_query = text("SELECT id FROM programs WHERE id = :pid")
        program_result = db.execute(program_query, {"pid": program_id}).fetchone()
        if not program_result:
            raise HTTPException(status_code=404, detail="Program not found")

        # Fetch decisions with accepted or waitlisted status, joined with applicants
        query = text("""
            SELECT 
                a.id AS applicant_id,
                ap.first_name,
                ap.last_name,
                ap.email,
                a.decision,
                a.decision_date
            FROM applications a
            JOIN applicants ap ON a.applicant_id = ap.id
            WHERE a.program_id = :pid
              AND a.decision IN ('accepted', 'waitlisted')
            ORDER BY ap.last_name ASC
        """)
        
        results = db.execute(query, {"pid": program_id}).fetchall()
        
        items = []
        for row in results:
            items.append(DecisionItem(
                applicant_id=row.applicant_id,
                first_name=row.first_name,
                last_name=row.last_name,
                email=row.email,
                decision=row.decision,
                decision_date=str(row.decision_date)
            ))
        
        return items
