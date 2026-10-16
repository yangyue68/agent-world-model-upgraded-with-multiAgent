import os
from typing import Optional, List

from fastapi import FastAPI, Query, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()

class IntakeResponse(BaseModel):
    id: int
    name: str
    term: str
    year: int
    program: str

class ApplicantFilterResponse(BaseModel):
    applicant_id: int
    user_id: Optional[int] = None
    gpa: float
    decision_flag: str
    submission_date: str
    full_name: Optional[str] = None
    email: Optional[str] = None

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/intakes/lookup", response_model=IntakeResponse, operation_id="lookup_intake_by_term_year_program")
def lookup_intake_by_term_year_program(
    term: str = Query(..., description="The academic term (e.g., 'Fall', 'Spring')"),
    year: int = Query(..., description="The academic year (e.g., 2025)"),
    program: str = Query(..., description="The program name (e.g., 'Computer Science')"),
    db_session = Depends(get_db)
):
    query = text("""
        SELECT id, name, term, year, program 
        FROM intakes 
        WHERE term = :term AND year = :year AND program = :program
    """)
    result = db_session.execute(query, {"term": term, "year": year, "program": program}).fetchone()
    
    if not result:
        raise HTTPException(status_code=404, detail="Intake not found")
    
    return IntakeResponse(
        id=result.id,
        name=result.name,
        term=result.term,
        year=result.year,
        program=result.program
    )

@app.get("/applicants/filter", response_model=List[ApplicantFilterResponse], operation_id="filter_applicants")
def filter_applicants(
    intake_id: int = Query(..., description="The ID of the intake to filter applicants by"),
    min_gpa: Optional[float] = Query(None, description="Minimum GPA threshold (inclusive). If omitted, no lower bound is applied."),
    decision_flag: Optional[str] = Query(None, description="Exact match for the applicant's decision flag (e.g., 'Waitlist', 'Accepted'). If omitted, no filter is applied."),
    has_scheduled_interview: Optional[bool] = Query(None, description="If true, only return applicants who have at least one interview with status 'Scheduled'. If false or omitted, this constraint is ignored."),
    db_session = Depends(get_db)
):
    sql = """
        SELECT 
            a.id as applicant_id,
            a.user_id,
            a.gpa,
            a.decision_flag,
            a.submission_date,
            u.full_name,
            u.email
        FROM applicants a
        LEFT JOIN users u ON a.user_id = u.id
        WHERE a.intake_id = :intake_id
    """
    
    params = {"intake_id": intake_id}
    
    if min_gpa is not None:
        sql += " AND a.gpa >= :min_gpa"
        params["min_gpa"] = min_gpa
        
    if decision_flag is not None:
        sql += " AND a.decision_flag = :decision_flag"
        params["decision_flag"] = decision_flag
        
    if has_scheduled_interview is True:
        sql += """
            AND EXISTS (
                SELECT 1 FROM interviews i 
                WHERE i.applicant_id = a.id AND i.status = 'Scheduled'
            )
        """
        
    sql += " ORDER BY a.submission_date DESC"
    
    results = db_session.execute(text(sql), params).fetchall()
    
    output = []
    for row in results:
        sub_date = row.submission_date
        if hasattr(sub_date, 'isoformat'):
            sub_date_str = sub_date.isoformat()
        else:
            sub_date_str = str(sub_date)
            
        output.append(ApplicantFilterResponse(
            applicant_id=row.applicant_id,
            user_id=row.user_id,
            gpa=row.gpa,
            decision_flag=row.decision_flag,
            submission_date=sub_date_str,
            full_name=row.full_name,
            email=row.email
        ))
        
    return output
