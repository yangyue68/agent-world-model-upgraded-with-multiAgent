import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    ForeignKey,
    Boolean,
    create_engine,
    select,
    text,
)
from sqlalchemy.orm import Session, declarative_base, sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- Models ---

class Candidate(Base):
    __tablename__ = "candidates"
    id = Column(Integer, primary_key=True)
    candidate_code = Column(String, unique=True, nullable=False)
    full_name = Column(String, nullable=False)
    email = Column(String, unique=True)

class Certification(Base):
    __tablename__ = "certifications"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(String)

class Exam(Base):
    __tablename__ = "exams"
    id = Column(Integer, primary_key=True)
    certification_id = Column(Integer, ForeignKey("certifications.id"), nullable=False)
    scheduled_at = Column(DateTime, nullable=False)
    proctored = Column(Boolean, default=True, nullable=False)
    status = Column(String, default="scheduled", nullable=False)

class ExamRegistration(Base):
    __tablename__ = "exam_registrations"
    id = Column(Integer, primary_key=True)
    exam_id = Column(Integer, ForeignKey("exams.id"), nullable=False)
    candidate_id = Column(Integer, ForeignKey("candidates.id"), nullable=False)
    registered_at = Column(DateTime, default=datetime.utcnow)

class ExamResult(Base):
    __tablename__ = "exam_results"
    id = Column(Integer, primary_key=True)
    exam_registration_id = Column(Integer, ForeignKey("exam_registrations.id"), unique=True, nullable=False)
    result = Column(String, nullable=False)
    score = Column(Float)

# --- Pydantic Schemas ---

class CandidateResponse(BaseModel):
    id: int
    candidate_code: str
    full_name: str
    email: str

class CertificationResponse(BaseModel):
    id: int
    name: str
    description: str

class ScheduleExamRequest(BaseModel):
    candidate_id: str  # The candidate code (e.g., C4089)
    certification_name: str
    scheduled_datetime: str

class ScheduleExamResponse(BaseModel):
    exam_id: int
    registration_id: int

class RecordResultRequest(BaseModel):
    exam_registration_id: int
    result: str
    score: float

class RecordResultResponse(BaseModel):
    result_id: int

# --- App Setup ---

app = FastAPI(title="CertChain API", version="1.0.0")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def parse_iso_datetime(dt_str: str) -> datetime:
    """Parse ISO-8601 datetime string, handling both 'T' and space separators."""
    dt_str = dt_str.replace('Z', '+00:00')
    if 'T' in dt_str:
        parts = dt_str.split('T')
        date_part = parts[0]
        time_part = parts[1]
    else:
        parts = dt_str.split(' ')
        date_part = parts[0]
        time_part = parts[1] if len(parts) > 1 else "00:00:00"
    
    try:
        return datetime.strptime(f"{date_part} {time_part}", "%Y-%m-%d %H:%M:%S%z")
    except ValueError:
        try:
            return datetime.strptime(f"{date_part} {time_part}", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid datetime format")

# --- Endpoints ---

@app.get("/candidates/{candidate_code}", operation_id="get_candidate_by_code", tags=["PrimaryResource"])
def get_candidate_by_code(candidate_code: str = Path(..., example="C4089")):
    db = SessionLocal()
    try:
        stmt = select(Candidate).where(Candidate.candidate_code == candidate_code)
        candidate = db.execute(stmt).scalar_one_or_none()
        if not candidate:
            raise HTTPException(status_code=404, detail="Candidate not found")
        
        return CandidateResponse(
            id=candidate.id,
            candidate_code=candidate.candidate_code,
            full_name=candidate.full_name,
            email=candidate.email or ""
        )
    finally:
        db.close()

@app.get("/certifications/{certification_name}", operation_id="get_certification_by_name", tags=["PrimaryResource"])
def get_certification_by_name(certification_name: str = Path(..., example="Certified Project Manager")):
    db = SessionLocal()
    try:
        stmt = select(Certification).where(Certification.name == certification_name)
        cert = db.execute(stmt).scalar_one_or_none()
        if not cert:
            raise HTTPException(status_code=404, detail="Certification not found")
        
        return CertificationResponse(
            id=cert.id,
            name=cert.name,
            description=cert.description or ""
        )
    finally:
        db.close()

@app.post("/exam-schedule", operation_id="schedule_exam_for_candidate", tags=["PrimaryResource"], response_model=ScheduleExamResponse)
def schedule_exam_for_candidate(req: ScheduleExamRequest):
    db = SessionLocal()
    try:
        # 1. Get Candidate ID from Code
        stmt_cand = select(Candidate).where(Candidate.candidate_code == req.candidate_id)
        candidate = db.execute(stmt_cand).scalar_one_or_none()
        if not candidate:
            raise HTTPException(status_code=404, detail="Candidate not found")
        
        # 2. Get Certification ID from Name
        stmt_cert = select(Certification).where(Certification.name == req.certification_name)
        cert = db.execute(stmt_cert).scalar_one_or_none()
        if not cert:
            raise HTTPException(status_code=404, detail="Certification not found")
        
        # 3. Parse Scheduled Datetime
        try:
            scheduled_dt = parse_iso_datetime(req.scheduled_datetime)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
            
        # Format for SQLite storage (remove tzinfo if present to keep it naive for storage, or keep UTC)
        # The schema uses DATETIME. We'll store as ISO string or datetime object. 
        # SQLAlchemy handles datetime objects well.
        
        # 4. Create Exam
        new_exam = Exam(
            certification_id=cert.id,
            scheduled_at=scheduled_dt,
            proctored=1,
            status="scheduled"
        )
        db.add(new_exam)
        db.flush() # Get exam.id
        
        # 5. Create Registration
        new_reg = ExamRegistration(
            exam_id=new_exam.id,
            candidate_id=candidate.id
        )
        db.add(new_reg)
        db.flush() # Get reg.id
        
        db.commit()
        
        return ScheduleExamResponse(
            exam_id=new_exam.id,
            registration_id=new_reg.id
        )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/exam-results", operation_id="record_exam_result", tags=["PrimaryResource"], response_model=RecordResultResponse)
def record_exam_result(req: RecordResultRequest):
    db = SessionLocal()
    try:
        # Validate result enum
        if req.result not in ('pass', 'fail'):
            raise HTTPException(status_code=400, detail="Result must be 'pass' or 'fail'")
        
        # Validate score range
        if req.score < 0 or req.score > 100:
            raise HTTPException(status_code=400, detail="Score must be between 0 and 100")
            
        # Check if registration exists
        stmt_reg = select(ExamRegistration).where(ExamRegistration.id == req.exam_registration_id)
        reg = db.execute(stmt_reg).scalar_one_or_none()
        if not reg:
            raise HTTPException(status_code=404, detail="Exam registration not found")
            
        # Check if result already exists (unique constraint on exam_registration_id)
        stmt_res = select(ExamResult).where(ExamResult.exam_registration_id == req.exam_registration_id)
        existing_result = db.execute(stmt_res).scalar_one_or_none()
        if existing_result:
            raise HTTPException(status_code=409, detail="Result already recorded for this exam registration")
            
        # Create Result
        new_result = ExamResult(
            exam_registration_id=req.exam_registration_id,
            result=req.result,
            score=req.score
        )
        db.add(new_result)
        db.commit()
        
        return RecordResultResponse(result_id=new_result.id)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
