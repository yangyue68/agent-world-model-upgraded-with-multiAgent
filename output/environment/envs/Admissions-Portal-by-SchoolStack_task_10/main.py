import os
import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Date, Boolean, ForeignKey, Text, func, case, and_
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

DATABASE_PATH = os.getenv("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    role = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

class Program(Base):
    __tablename__ = "programs"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    department = Column(String)
    degree_type = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

class Intake(Base):
    __tablename__ = "intakes"
    id = Column(Integer, primary_key=True, index=True)
    term = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    level = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

class Application(Base):
    __tablename__ = "applications"
    id = Column(Integer, primary_key=True, index=True)
    applicant_name = Column(String, nullable=False)
    applicant_email = Column(String, nullable=False)
    program_id = Column(Integer, ForeignKey("programs.id"))
    intake_id = Column(Integer, ForeignKey("intakes.id"))
    status = Column(String, default="submitted")
    assigned_reviewer_id = Column(Integer, ForeignKey("users.id"))
    review_deadline = Column(Date)
    financial_aid_documents_complete = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

class Rubric(Base):
    __tablename__ = "rubrics"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    version = Column(String, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

class ApplicationRubric(Base):
    __tablename__ = "application_rubrics"
    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("applications.id"))
    rubric_id = Column(Integer, ForeignKey("rubrics.id"))
    attached_by = Column(Integer, ForeignKey("users.id"))
    attached_at = Column(DateTime, default=datetime.datetime.utcnow)

class CommentThread(Base):
    __tablename__ = "comment_threads"
    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("applications.id"))
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

class Comment(Base):
    __tablename__ = "comments"
    id = Column(Integer, primary_key=True, index=True)
    thread_id = Column(Integer, ForeignKey("comment_threads.id"))
    author_id = Column(Integer, ForeignKey("users.id"))
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    action = Column(String, nullable=False)
    entity_type = Column(String, nullable=False)
    entity_id = Column(Integer)
    details = Column(Text)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

# Pydantic Models
class ApplicationResponse(BaseModel):
    id: int
    applicant_name: str
    applicant_email: str
    status: str
    financial_aid_documents_complete: bool

class BulkAssignRequest(BaseModel):
    application_ids: List[int]
    reviewer_id: int
    rubric_id: int
    review_deadline: str
    comment_body: str

class BulkAssignResponse(BaseModel):
    updated_count: int
    failed_ids: List[int]

class UserLookupResponse(BaseModel):
    id: int
    full_name: str
    email: str
    role: str

class ProgramLookupResponse(BaseModel):
    id: int
    name: str
    department: str
    degree_type: str

class IntakeLookupResponse(BaseModel):
    id: int
    term: str
    year: int
    level: str

class RubricLookupResponse(BaseModel):
    id: int
    name: str
    version: str
    description: str

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/applications/unassigned", response_model=List[ApplicationResponse], operation_id="get_unassigned_applications")
def get_unassigned_applications(intake_id: int, program_id: int, db=None):
    if db is None:
        db = next(get_db())
    
    applications = db.query(Application).filter(
        and_(
            Application.intake_id == intake_id,
            Application.program_id == program_id,
            Application.assigned_reviewer_id.is_(None)
        )
    ).all()
    
    return [
        ApplicationResponse(
            id=app.id,
            applicant_name=app.applicant_name,
            applicant_email=app.applicant_email,
            status=app.status,
            financial_aid_documents_complete=bool(app.financial_aid_documents_complete)
        ) for app in applications
    ]

@app.post("/applications/bulk-assign", response_model=BulkAssignResponse, operation_id="bulk_assign_applications")
def bulk_assign_applications(request: BulkAssignRequest, db=None):
    if db is None:
        db = next(get_db())
    
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    failed_ids = []
    updated_count = 0
    
    # Validate that all applications exist and are unassigned
    applications = db.query(Application).filter(Application.id.in_(request.application_ids)).all()
    existing_application_ids = {app.id for app in applications}
    
    # Check if any requested application doesn't exist or is already assigned
    for app_id in request.application_ids:
        if app_id not in existing_application_ids:
            failed_ids.append(app_id)
        else:
            app = next((a for a in applications if a.id == app_id), None)
            if app.assigned_reviewer_id is not None:
                failed_ids.append(app_id)
    
    # Filter out failed IDs
    successful_application_ids = [app_id for app_id in request.application_ids if app_id not in failed_ids]
    
    # Process successful assignments
    for app_id in successful_application_ids:
        try:
            # Update application
            app = db.query(Application).filter(Application.id == app_id).first()
            app.assigned_reviewer_id = request.reviewer_id
            app.review_deadline = datetime.datetime.strptime(request.review_deadline, "%Y-%m-%d").date()
            
            # Attach rubric if not already attached
            existing_attachment = db.query(ApplicationRubric).filter(
                and_(
                    ApplicationRubric.application_id == app_id,
                    ApplicationRubric.rubric_id == request.rubric_id
                )
            ).first()
            
            if not existing_attachment:
                attachment = ApplicationRubric(
                    application_id=app_id,
                    rubric_id=request.rubric_id,
                    attached_by=current_user_id
                )
                db.add(attachment)
            
            # Create comment thread and comment
            thread = CommentThread(
                application_id=app_id,
                created_by=current_user_id
            )
            db.add(thread)
            db.flush()  # Get thread ID
            
            comment = Comment(
                thread_id=thread.id,
                author_id=current_user_id,
                body=request.comment_body
            )
            db.add(comment)
            
            db.commit()
            updated_count += 1
        except Exception as e:
            db.rollback()
            failed_ids.append(app_id)
    
    return BulkAssignResponse(
        updated_count=updated_count,
        failed_ids=failed_ids
    )

@app.get("/users/lookup", response_model=List[UserLookupResponse], operation_id="lookup_users_by_name")
def lookup_users_by_name(full_name: str, db=None):
    if db is None:
        db = next(get_db())
    
    users = db.query(User).filter(User.full_name.ilike(f"%{full_name}%")).all()
    
    return [
        UserLookupResponse(
            id=user.id,
            full_name=user.full_name,
            email=user.email,
            role=user.role
        ) for user in users
    ]

@app.get("/programs/lookup", response_model=List[ProgramLookupResponse], operation_id="lookup_programs_by_name")
def lookup_programs_by_name(name: str, db=None):
    if db is None:
        db = next(get_db())
    
    programs = db.query(Program).filter(Program.name.ilike(f"%{name}%")).all()
    
    return [
        ProgramLookupResponse(
            id=program.id,
            name=program.name,
            department=program.department,
            degree_type=program.degree_type
        ) for program in programs
    ]

@app.get("/intakes/lookup", response_model=List[IntakeLookupResponse], operation_id="lookup_intakes_by_term_year_level")
def lookup_intakes_by_term_year_level(term: str, year: int, level: str, db=None):
    if db is None:
        db = next(get_db())
    
    intakes = db.query(Intake).filter(
        and_(
            Intake.term == term,
            Intake.year == year,
            Intake.level == level
        )
    ).all()
    
    return [
        IntakeLookupResponse(
            id=intake.id,
            term=intake.term,
            year=intake.year,
            level=intake.level
        ) for intake in intakes
    ]

@app.get("/rubrics/lookup", response_model=List[RubricLookupResponse], operation_id="lookup_rubrics_by_name_version")
def lookup_rubrics_by_name_version(name: str, version: str, db=None):
    if db is None:
        db = next(get_db())
    
    rubrics = db.query(Rubric).filter(
        and_(
            Rubric.name.ilike(f"%{name}%"),
            Rubric.version == version
        )
    ).all()
    
    return [
        RubricLookupResponse(
            id=rubric.id,
            name=rubric.name,
            version=rubric.version,
            description=rubric.description
        ) for rubric in rubrics
    ]
