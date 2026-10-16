import os
import datetime
from typing import List, Optional
from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text, Date, func, case, and_
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

class Program(Base):
    __tablename__ = 'programs'
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    degree_type = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

class Term(Base):
    __tablename__ = 'terms'
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    start_date = Column(Date)
    end_date = Column(Date)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

class ProgramTerm(Base):
    __tablename__ = 'program_terms'
    id = Column(Integer, primary_key=True)
    program_id = Column(Integer, ForeignKey('programs.id'))
    term_id = Column(Integer, ForeignKey('terms.id'))
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

class ReviewCommittee(Base):
    __tablename__ = 'review_committees'
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

class ReviewStage(Base):
    __tablename__ = 'review_stages'
    id = Column(Integer, primary_key=True)
    committee_id = Column(Integer, ForeignKey('review_committees.id'))
    name = Column(String, nullable=False)
    stage_order = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

class Applicant(Base):
    __tablename__ = 'applicants'
    id = Column(Integer, primary_key=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

class Application(Base):
    __tablename__ = 'applications'
    id = Column(Integer, primary_key=True)
    applicant_id = Column(Integer, ForeignKey('applicants.id'))
    program_term_id = Column(Integer, ForeignKey('program_terms.id'))
    submitted_at = Column(DateTime)
    final_decision = Column(Text, check_constraint="final_decision IN ('admit', 'waitlist', 'deny')")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

class ReviewAssignment(Base):
    __tablename__ = 'review_assignments'
    id = Column(Integer, primary_key=True)
    application_id = Column(Integer, ForeignKey('applications.id'))
    reviewer_id = Column(Integer, ForeignKey('users.id'))
    stage_id = Column(Integer, ForeignKey('review_stages.id'))
    assigned_at = Column(DateTime, default=datetime.datetime.utcnow)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

# Response Model
class ReviewerWorkloadStats(BaseModel):
    reviewer_id: int
    reviewer_username: str
    stage_id: int
    stage_name: str
    committee_name: str
    total_assigned_applications: int
    decided_applications_count: int
    decision_percentage: float

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/reviewer-workload", response_model=List[ReviewerWorkloadStats], operation_id="get_reviewer_workload_stats")
def get_reviewer_workload_stats(
    program_name: str = Query(..., description="The name of the program (e.g., 'Master of Business Administration')."),
    term_name: str = Query(..., description="The name of the term (e.g., 'Fall 2025')."),
):
    db = next(get_db())
    
    # Subquery to get the program_term_id
    program_term_subq = db.query(ProgramTerm.id).join(Program, Program.id == ProgramTerm.program_id).join(Term, Term.id == ProgramTerm.term_id).filter(
        Program.name == program_name,
        Term.name == term_name
    ).subquery()
    
    # Main query
    stmt = db.query(
        User.id.label("reviewer_id"),
        User.username.label("reviewer_username"),
        ReviewStage.id.label("stage_id"),
        ReviewStage.name.label("stage_name"),
        ReviewCommittee.name.label("committee_name"),
        func.count(distinct(ReviewAssignment.application_id)).label("total_assigned_applications"),
        func.sum(case(
            (Application.final_decision.isnot(None), 1),
            else_=0
        )).label("decided_applications_count")
    ).select_from(ReviewAssignment)\
    .join(Application, Application.id == ReviewAssignment.application_id)\
    .join(User, User.id == ReviewAssignment.reviewer_id)\
    .join(ReviewStage, ReviewStage.id == ReviewAssignment.stage_id)\
    .join(ReviewCommittee, ReviewCommittee.id == ReviewStage.committee_id)\
    .filter(
        Application.program_term_id.in_(program_term_subq.c.id)
    ).group_by(
        User.id,
        ReviewStage.id,
        ReviewCommittee.name
    )
    
    results = stmt.all()
    
    # Calculate decision percentages
    workload_stats = []
    for row in results:
        total = row.total_assigned_applications
        decided = row.decided_applications_count
        percentage = (decided / total * 100) if total > 0 else 0.0
        
        workload_stats.append(ReviewerWorkloadStats(
            reviewer_id=row.reviewer_id,
            reviewer_username=row.reviewer_username,
            stage_id=row.stage_id,
            stage_name=row.stage_name,
            committee_name=row.committee_name,
            total_assigned_applications=total,
            decided_applications_count=decided,
            decision_percentage=percentage
        ))
    
    return workload_stats
