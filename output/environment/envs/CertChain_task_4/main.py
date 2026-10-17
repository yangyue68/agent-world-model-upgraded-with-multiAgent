import os
from datetime import date, datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class BadgeIssueRequest(BaseModel):
    candidate_id: int
    certification_id: int
    awarded_date: str  # ISO-8601 date string YYYY-MM-DD
    expiration_date: Optional[str] = None  # ISO-8601 date string YYYY-MM-DD


class BadgeResponse(BaseModel):
    badge_id: int
    candidate_email: str
    status: str


class CandidateSearchResponse(BaseModel):
    candidate_id: int


class CertificationSearchResponse(BaseModel):
    certification_id: int


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.post("/badges", response_model=BadgeResponse, operation_id="issue_badge")
def issue_badge(request: BadgeIssueRequest):
    """
    Issue a digital badge for a certification to a candidate.
    Creates a new badge record and sends an email log entry.
    """
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized: No user context found.")
    
    try:
        issued_by = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID format.")

    # Validate dates
    try:
        awarded_dt = datetime.strptime(request.awarded_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid awarded_date format. Expected YYYY-MM-DD.")

    exp_dt = None
    if request.expiration_date:
        try:
            exp_dt = datetime.strptime(request.expiration_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid expiration_date format. Expected YYYY-MM-DD.")

    with engine.connect() as conn:
        # Check if candidate exists and get their email
        res_candidate = conn.execute(
            text("SELECT id, email FROM candidates WHERE id = :cid"),
            {"cid": request.candidate_id}
        ).fetchone()
        
        if not res_candidate:
            raise HTTPException(status_code=404, detail=f"Candidate {request.candidate_id} not found.")
        
        candidate_email = res_candidate.email

        # Check if certification exists
        res_cert = conn.execute(
            text("SELECT id FROM certifications WHERE id = :certid"),
            {"certid": request.certification_id}
        ).fetchone()
        
        if not res_cert:
            raise HTTPException(status_code=404, detail=f"Certification {request.certification_id} not found.")

        # Insert badge
        insert_badge_sql = text("""
            INSERT INTO badges (candidate_id, certification_id, issued_by, awarded_date, expiration_date, status)
            VALUES (:candidate_id, :certification_id, :issued_by, :awarded_date, :expiration_date, 'issued')
        """)
        
        result = conn.execute(
            insert_badge_sql,
            {
                "candidate_id": request.candidate_id,
                "certification_id": request.certification_id,
                "issued_by": issued_by,
                "awarded_date": awarded_dt.isoformat(),
                "expiration_date": exp_dt.isoformat() if exp_dt else None
            }
        )
        
        badge_id = result.lastrowid
        
        # Get certification name for email subject
        cert_name_res = conn.execute(
            text("SELECT name FROM certifications WHERE id = :certid"),
            {"certid": request.certification_id}
        ).fetchone()
        cert_name = cert_name_res.name if cert_name_res else "Unknown Certification"

        # Insert email log
        subject = f"Your {cert_name} Badge"
        body = "Congratulations! Your digital badge has been issued."
        
        insert_email_log_sql = text("""
            INSERT INTO email_logs (badge_id, recipient_email, subject, body, status)
            VALUES (:badge_id, :recipient_email, :subject, :body, 'sent')
        """)
        
        conn.execute(
            insert_email_log_sql,
            {
                "badge_id": badge_id,
                "recipient_email": candidate_email,
                "subject": subject,
                "body": body
            }
        )
        
        conn.commit()

    return BadgeResponse(
        badge_id=badge_id,
        candidate_email=candidate_email,
        status="issued"
    )


@app.get("/candidates/search", response_model=CandidateSearchResponse, operation_id="search_candidates")
def search_candidates(external_id: str = Query(..., description="External identifier for the candidate")):
    """
    Lookup a candidate using an external identifier such as candidate ID 
    and return the internal candidate ID.
    In this schema, the primary key `id` in `candidates` serves as the internal ID.
    The task implies mapping an external ID (like 78910) to the internal DB ID.
    Looking at the initial snapshot, candidate 78910 IS the internal ID in the table.
    However, usually "external_id" might refer to something else or just be the PK.
    Given the data: candidates.id contains 78910. So we treat external_id as the candidate's ID directly.
    """
    try:
        cid_int = int(external_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid external_id format.")

    with engine.connect() as conn:
        res = conn.execute(
            text("SELECT id FROM candidates WHERE id = :cid"),
            {"cid": cid_int}
        ).fetchone()
        
        if not res:
            raise HTTPException(status_code=404, detail="Candidate not found.")
            
        return CandidateSearchResponse(candidate_id=res.id)


@app.get("/certifications/search", response_model=CertificationSearchResponse, operation_id="search_certifications")
def search_certifications(name: str = Query(..., description="Name of the certification to search for")):
    """
    Lookup a certification using its name and return the internal certification ID.
    """
    with engine.connect() as conn:
        res = conn.execute(
            text("SELECT id FROM certifications WHERE name = :name"),
            {"name": name}
        ).fetchone()
        
        if not res:
            raise HTTPException(status_code=404, detail="Certification not found.")
            
        return CertificationSearchResponse(certification_id=res.id)
