```python
import os
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Query, Body
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text, Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint, CheckConstraint, Index
from sqlalchemy.orm import declarative_base, Session

# Read DATABASE_PATH at module import
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

# Determine if it's SQLite and get the file path for sqlite3 usage if needed
is_sqlite = DATABASE_PATH.startswith("sqlite:///")
db_file_path = None
if is_sqlite:
    db_file_path = DATABASE_PATH[len("sqlite:///"):]

# Create SQLAlchemy engine
engine = create_engine(DATABASE_PATH)

Base = declarative_base()

# Define ORM models for tables we need to interact with
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

class Program(Base):
    __tablename__ = 'programs'
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    term = Column(String, nullable=False)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)
    __table_args__ = (UniqueConstraint('name', 'term'),)

class Applicant(Base):
    __tablename__ = 'applicants'
    id = Column(String, primary_key=True)
    program_id = Column(Integer, ForeignKey('programs.id'), nullable=False)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    date_of_birth = Column(String)
    email = Column(String)
    phone = Column(String)
    status = Column(String, nullable=False, default='active')
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

class Document(Base):
    __tablename__ = 'documents'
    id = Column(Integer, primary_key=True)
    applicant_id = Column(String, ForeignKey('applicants.id'), nullable=False)
    file_name = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    file_type = Column(String)
    file_size = Column(Integer)
    uploaded_at = Column(DateTime)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

class TestScore(Base):
    __tablename__ = 'test_scores'
    id = Column(Integer, primary_key=True)
    applicant_id = Column(String, ForeignKey('applicants.id'), nullable=False)
    test_name = Column(String, nullable=False)
    score = Column(Integer) # Using Integer as per DDL REAL but often stored as int in simple cases, or Float. Let's use Float/Real equivalent. SQLAlchemy maps REAL to Float usually.
    test_date = Column(String)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

class RecommendationRequest(Base):
    __tablename__ = 'recommendation_requests'
    id = Column(Integer, primary_key=True)
    applicant_id = Column(String, ForeignKey('applicants.id'), nullable=False)
    recommender_name = Column(String, nullable=False)
    recommender_email = Column(String, nullable=False)
    status = Column(String, nullable=False, default='pending')
    requested_at = Column(DateTime)
    submitted_at = Column(DateTime)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

class SubmissionResponse(Base):
    __tablename__ = 'submission_responses'
    id = Column(Integer, primary_key=True)
    applicant_id = Column(String, ForeignKey('applicants.id'), nullable=False)
    question_key = Column(String, nullable=False)
    response_text = Column(Text)
    submitted_at = Column(DateTime)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

class MergeLog(Base):
    __tablename__ = 'merge_logs'
    id = Column(Integer, primary_key=True)
    primary_applicant_id = Column(String, ForeignKey('applicants.id'), nullable=False)
    duplicate_applicant_id = Column(String, ForeignKey('applicants.id'), nullable=False)
    reviewer_user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    merged_at = Column(DateTime)
    details = Column(Text)
    created_at = Column(DateTime)

# Pydantic Models for API
class ApplicantLookupResponse(BaseModel):
    id: str
    first_name: str
    last_name: str
    date_of_birth: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    status: str
    program_id: int

class MergeRequest(BaseModel):
    reviewer_user_id: int = Field(..., description="The ID of the user performing the merge, used for audit logging")

class MergeResponse(BaseModel):
    success: bool
    message: str
    primary_applicant_id: str
    merged_from_applicant_id: str
    moved_documents_count: Optional[int] = 0
    moved_test_scores_count: Optional[int] = 0
    moved_recommendation_requests_count: Optional[int] = 0
    moved_submission_responses_count: Optional[int] = 0
    merge_log_id: Optional[int] = None

app = FastAPI(title="Admissions Portal by SchoolStack")

@app.get("/applicants/lookup", response_model=List[ApplicantLookupResponse], operation_id="lookup_applicants_by_name_and_program")
def lookup_applicants(
    first_name: str = Query(..., description="First name of the applicant"),
    last_name: str = Query(..., description="Last name of the applicant"),
    program_name: str = Query(..., description="Name of the program (e.g., 'Data Science')"),
    program_term: str = Query(..., description="Term of the program (e.g., 'Fall 2025')")
):
    """
    Find applicant records matching the provided first name, last name, and program name/term.
    Returns a list of matching applicants with their IDs, current status, date of birth, and other key fields.
    """
    with Session(engine) as session:
        # Join applicants with programs to filter by program name and term
        stmt = (
            select(Applicant)
            .join(Program, Applicant.program_id == Program.id)
            .where(
                Applicant.first_name == first_name,
                Applicant.last_name == last_name,
                Program.name == program_name,
                Program.term == program_term
            )
        )
        results = session.execute(stmt).scalars().all()
        
        return [
            ApplicantLookupResponse(
                id=r.id,
                first_name=r.first_name,
                last_name=r.last_name,
                date_of_birth=r.date_of_birth,
                email=r.email,
                phone=r.phone,
                status=r.status,
                program_id=r.program_id
            )
            for r in results
        ]

@app.post("/applicants/{primary_id}/merge/{duplicate_id}", response_model=MergeResponse, operation_id="merge_duplicate_applicants")
def merge_duplicate_applicants(
    primary_id: str,
    duplicate_id: str,
    body: MergeRequest
):
    """
    Merges all associated data from the duplicate applicant record into the primary applicant record.
    Resolves conflicting personal information by keeping values from the primary record.
    Deletes the duplicate applicant. Logs the merge action.
    """
    reviewer_user_id = body.reviewer_user_id
    
    with Session(engine) as session:
        # Verify Primary Applicant exists and is active
        primary_applicant = session.get(Applicant, primary_id)
        if not primary_applicant:
            raise HTTPException(status_code=404, detail=f"Primary applicant {primary_id} not found")
        if primary_applicant.status != 'active':
            raise HTTPException(status_code=400, detail=f"Primary applicant {primary_id} is not active")

        # Verify Duplicate Applicant exists and is active
        duplicate_applicant = session.get(Applicant, duplicate_id)
        if not duplicate_applicant:
            raise HTTPException(status_code=404, detail=f"Duplicate applicant {duplicate_id} not found")
        if duplicate_applicant.status != 'active':
            raise HTTPException(status_code=400, detail=f"Duplicate applicant {duplicate_id} is not active")
        
        # Verify Reviewer User exists
        reviewer_user = session.get(User, reviewer_user_id)
        if not reviewer_user:
            raise HTTPException(status_code=404, detail=f"Reviewer user {reviewer_user_id} not found")

        # Move Documents
        doc_stmt = update(Document).where(Document.applicant_id == duplicate_id).values(applicant_id=primary_id)
        result_doc = session.execute(doc_stmt)
        moved_documents_count = result_doc.rowcount

        # Move Test Scores
        ts_stmt = update(TestScore).where(TestScore.applicant_id == duplicate_id).values(applicant_id=primary_id)
        result_ts = session.execute(ts_stmt)
        moved_test_scores_count = result_ts.rowcount

        # Move Recommendation Requests
        rr_stmt = update(RecommendationRequest).where(RecommendationRequest.applicant_id == duplicate_id).values(applicant_id=primary_id)
        result_rr = session.execute(rr_stmt)
        moved_recommendation_requests_count = result_rr.rowcount

        # Move Submission Responses
        sr_stmt = update(SubmissionResponse).where(SubmissionResponse.applicant_id == duplicate_id).values(applicant_id=primary_id)
        result_sr = session.execute(sr_stmt)
        moved_submission_responses_count = result_sr.rowcount

        # Resolve Conflicting Personal Information (Keep Primary's DOB, Email, Phone)
        # The task says "resolve conflicting date-of-birth values by keeping the one from APP-4421".
        # Since we are merging INTO primary, primary's data stays. We don't need to update primary unless we wanted to overwrite with duplicate, which we don't.
        # However, we should ensure primary's data is intact. It already is.
        
        # Delete Duplicate Applicant
        # Note: Foreign keys on child tables have been updated to point to primary. 
        # So deleting duplicate should be safe now regarding children.
        # But wait, merge_logs has FKs to applicants. If we delete duplicate, any existing logs referencing it might break?
        # The schema says ON DELETE RESTRICT for merge_logs FKs. 
        # This means we cannot delete an applicant if there are merge_logs referencing them.
        # In this specific task flow, we are creating a NEW log AFTER moving data. 
        # Are there OLD logs referencing APP-7789? Initial snapshot shows empty merge_logs. 
        # If there were old logs, we couldn't delete APP-7789 due to RESTRICT.
        # Assuming no prior logs for simplicity as per initial snapshot, or handling error if present.
        
        try:
            session.delete(duplicate_applicant)
        except Exception as e:
            # Likely IntegrityError due to RESTRICT if logs exist
            session.rollback()
            raise HTTPException(status_code=409, detail=f"Conflict: Cannot delete duplicate applicant due to existing references: {str(e)}")

        # Log the Merge
        from datetime import datetime
        now = datetime.utcnow()
        
        new_log = MergeLog(
            primary_applicant_id=primary_id,
            duplicate_applicant_id=duplicate_id,
            reviewer_user_id=reviewer_user_id,
            merged_at=now,
            details=f"Merged {duplicate_id} into {primary_id}. Moved {moved_documents_count} docs, {moved_test_scores_count} scores, {moved_recommendation_requests_count} recs, {moved_submission_responses_count} responses."
        )
        session.add(new_log)
        
        session.commit()
        
        # Refresh to get generated ID
        session.refresh(new_log)

        return MergeResponse(
            success=True,
            message="Merge completed successfully",
            primary_applicant_id=primary_id,
            merged_from_applicant_id=duplicate_id,
            moved_documents_count=moved_documents_count,
            moved_test_scores_count=moved_test_scores_count,
            moved_recommendation_requests_count=moved_recommendation_requests_count,
            moved_submission_responses_count=moved_submission_responses_count,
            merge_log_id=new_log.id
        )

# Import necessary SQLAlchemy components
from sqlalchemy import select, update
