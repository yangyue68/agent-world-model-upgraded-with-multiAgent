import os
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, Depends
from pydantic import BaseModel, Field
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Date,
    DateTime,
    ForeignKey,
    Text,
    create_engine,
    select,
    insert,
    update,
    and_,
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# --- SQLAlchemy Models ---

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True)
    full_name = Column(String)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=datetime.now(timezone.utc))


class Certification(Base):
    __tablename__ = "certifications"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=datetime.now(timezone.utc))


class Provider(Base):
    __tablename__ = "providers"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=datetime.now(timezone.utc))


class Course(Base):
    __tablename__ = "courses"
    id = Column(Integer, primary_key=True, autoincrement=True)
    provider_id = Column(Integer, ForeignKey("providers.id"), nullable=False)
    name = Column(String, nullable=False)
    credits = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=datetime.now(timezone.utc))


class Candidate(Base):
    __tablename__ = "candidates"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=datetime.now(timezone.utc))


class Application(Base):
    __tablename__ = "applications"
    id = Column(Integer, primary_key=True, autoincrement=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"), nullable=False)
    certification_id = Column(Integer, ForeignKey("certifications.id"), nullable=False)
    status = Column(String, nullable=False)
    submitted_at = Column(DateTime, default=datetime.now(timezone.utc))
    decided_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=datetime.now(timezone.utc))


class CeuRecord(Base):
    __tablename__ = "ceu_records"
    id = Column(Integer, primary_key=True, autoincrement=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"), nullable=False)
    course_id = Column(Integer, ForeignKey("courses.id"), nullable=False)
    credits = Column(Float, nullable=False)
    completion_date = Column(Date, nullable=False)
    category = Column(String)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=datetime.now(timezone.utc))


class Appeal(Base):
    __tablename__ = "appeals"
    id = Column(Integer, primary_key=True, autoincrement=True)
    application_id = Column(Integer, ForeignKey("applications.id"), nullable=False)
    submitted_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    reason = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="submitted")
    submitted_at = Column(DateTime, default=datetime.now(timezone.utc))
    resolved_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=datetime.now(timezone.utc))


class AppealCeuRecord(Base):
    __tablename__ = "appeal_ceu_records"
    appeal_id = Column(Integer, ForeignKey("appeals.id"), primary_key=True)
    ceu_record_id = Column(Integer, ForeignKey("ceu_records.id"), primary_key=True)
    created_at = Column(DateTime, default=datetime.now(timezone.utc))


# --- Pydantic Schemas ---

class AppealCreate(BaseModel):
    application_id: int
    reason: str

class AppealResponse(BaseModel):
    id: int
    application_id: int
    submitted_by: int
    reason: str
    status: str
    submitted_at: str

    class Config:
        from_attributes = True

class LinkCeuRecordsRequest(BaseModel):
    ceu_record_ids: List[int]

class LinkCeuRecordsResponse(BaseModel):
    appeal_id: int
    ceu_record_ids: List[int]

class CandidateApplicationDetailsResponse(BaseModel):
    id: int
    candidate_id: int
    certification_id: int
    status: str
    submitted_at: str
    decided_at: Optional[str] = None

    class Config:
        from_attributes = True

class CeuRecordResponse(BaseModel):
    id: int
    candidate_id: int
    course_id: int
    credits: float
    completion_date: str
    category: Optional[str] = None

    class Config:
        from_attributes = True


# --- App Setup ---

app = FastAPI(title="CertChain API", version="1.0.0")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.post("/appeals", operation_id="submit_appeal", response_model=AppealResponse, tags=["PrimaryResource"])
def submit_appeal(appeal_data: AppealCreate, db: Session = Depends(get_db)):
    """Submit an appeal for a denied certification application"""
    
    # Verify the application exists and is in a state that allows appeal (e.g., 'denied')
    stmt = select(Application).where(Application.id == appeal_data.application_id)
    application = db.execute(stmt).scalar_one_or_none()
    
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    
    if application.status != "denied":
        raise HTTPException(status_code=400, detail="Can only appeal denied applications")

    # Get current user ID from environment
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=500, detail="Current user ID not set")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=500, detail="Invalid current user ID format")

    # Create the appeal
    new_appeal = Appeal(
        application_id=appeal_data.application_id,
        submitted_by=current_user_id,
        reason=appeal_data.reason,
        status="submitted"
    )
    db.add(new_appeal)
    db.commit()
    db.refresh(new_appeal)

    return AppealResponse(
        id=new_appeal.id,
        application_id=new_appeal.application_id,
        submitted_by=new_appeal.submitted_by,
        reason=new_appeal.reason,
        status=new_appeal.status,
        submitted_at=new_appeal.submitted_at.isoformat() if new_appeal.submitted_at else None
    )


@app.post("/appeals/{appeal_id}/ceu_records", operation_id="link_ceu_records_to_appeal", response_model=LinkCeuRecordsResponse, tags=["PrimaryResource"])
def link_ceu_records_to_appeal(appeal_id: int, body: LinkCeuRecordsRequest, db: Session = Depends(get_db)):
    """Link CEU records to an appeal"""
    
    # Verify appeal exists
    stmt = select(Appeal).where(Appeal.id == appeal_id)
    appeal = db.execute(stmt).scalar_one_or_none()
    
    if not appeal:
        raise HTTPException(status_code=404, detail="Appeal not found")

    # Verify CEU records exist and belong to the candidate associated with the application
    # First, get the candidate_id from the application
    app_stmt = select(Application.candidate_id).where(Application.id == appeal.application_id)
    candidate_id_result = db.execute(app_stmt).scalar_one_or_none()
    
    if candidate_id_result is None:
        raise HTTPException(status_code=404, detail="Associated application not found")

    valid_ceu_ids = []
    for ceu_id in body.ceu_record_ids:
        ceu_stmt = select(CeuRecord).where(
            and_(
                CeuRecord.id == ceu_id,
                CeuRecord.candidate_id == candidate_id_result
            )
        )
        ceu_record = db.execute(ceu_stmt).scalar_one_or_none()
        
        if not ceu_record:
            raise HTTPException(status_code=404, detail=f"CEU record {ceu_id} not found or does not belong to the candidate")
        
        # Check if already linked
        existing_link = db.execute(
            select(AppealCeuRecord).where(
                and_(
                    AppealCeuRecord.appeal_id == appeal_id,
                    AppealCeuRecord.ceu_record_id == ceu_id
                )
            )
        ).scalar_one_or_none()
        
        if not existing_link:
            new_link = AppealCeuRecord(
                appeal_id=appeal_id,
                ceu_record_id=ceu_id
            )
            db.add(new_link)
            valid_ceu_ids.append(ceu_id)
        else:
            valid_ceu_ids.append(ceu_id)

    db.commit()

    return LinkCeuRecordsResponse(
        appeal_id=appeal_id,
        ceu_record_ids=valid_ceu_ids
    )


@app.get("/candidates/{candidate_id}/applications/{application_id}", operation_id="get_candidate_application_details", response_model=CandidateApplicationDetailsResponse, tags=["PrimaryResource"])
def get_candidate_application_details(candidate_id: int, application_id: int, db: Session = Depends(get_db)):
    """Retrieve details of a candidate's application"""
    
    stmt = select(Application).where(
        and_(
            Application.id == application_id,
            Application.candidate_id == candidate_id
        )
    )
    application = db.execute(stmt).scalar_one_or_none()
    
    if not application:
        raise HTTPException(status_code=404, detail="Application not found for this candidate")

    return CandidateApplicationDetailsResponse(
        id=application.id,
        candidate_id=application.candidate_id,
        certification_id=application.certification_id,
        status=application.status,
        submitted_at=application.submitted_at.isoformat() if application.submitted_at else None,
        decided_at=application.decided_at.isoformat() if application.decided_at else None
    )


@app.get("/ceu_records", operation_id="find_ceu_records", response_model=List[CeuRecordResponse], tags=["PrimaryResource"])
def find_ceu_records(
    candidate_id: int,
    course_name: Optional[str] = Query(None),
    provider_name: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """Find CEU records by candidate and course"""
    
    stmt = select(CeuRecord).where(CeuRecord.candidate_id == candidate_id)
    
    if course_name or provider_name:
        # Join with courses and providers to filter
        stmt = stmt.join(Course).join(Provider)
        
        conditions = [Course.provider_id == Provider.id]
        if course_name:
            conditions.append(Course.name == course_name)
        if provider_name:
            conditions.append(Provider.name == provider_name)
            
        stmt = stmt.where(and_(*conditions))

    results = db.execute(stmt).scalars().all()
    
    return [
        CeuRecordResponse(
            id=r.id,
            candidate_id=r.candidate_id,
            course_id=r.course_id,
            credits=r.credits,
            completion_date=r.completion_date.isoformat() if isinstance(r.completion_date, datetime) else str(r.completion_date),
            category=r.category
        )
        for r in results
    ]
