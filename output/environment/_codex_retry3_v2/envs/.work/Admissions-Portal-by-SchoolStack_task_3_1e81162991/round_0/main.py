import os
from typing import List
from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, CheckConstraint, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise ValueError("DATABASE_PATH environment variable is not set")

engine = create_engine(f"sqlite:///{DATABASE_PATH}")
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Define models based on the schema
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))

class Program(Base):
    __tablename__ = "programs"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    degree_type = Column(String, nullable=False)
    created_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))

class Term(Base):
    __tablename__ = "terms"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    start_date = Column(String)
    end_date = Column(String)
    created_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))

class ProgramTerm(Base):
    __tablename__ = "program_terms"
    id = Column(Integer, primary_key=True)
    program_id = Column(Integer, ForeignKey("programs.id"))
    term_id = Column(Integer, ForeignKey("terms.id"))
    created_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))

class ReviewCommittee(Base):
    __tablename__ = "review_committees"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))

class ReviewStage(Base):
    __tablename__ = "review_stages"
    id = Column(Integer, primary_key=True)
    committee_id = Column(Integer, ForeignKey("review_committees.id"))
    name = Column(String, nullable=False)
    stage_order = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))
    committee = relationship("ReviewCommittee")

class Applicant(Base):
    __tablename__ = "applicants"
    id = Column(Integer, primary_key=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))

class Application(Base):
    __tablename__ = "applications"
    id = Column(Integer, primary_key=True)
    applicant_id = Column(Integer, ForeignKey("applicants.id"))
    program_term_id = Column(Integer, ForeignKey("program_terms.id"))
    submitted_at = Column(DateTime)
    final_decision = Column(String, CheckConstraint("final_decision IN ('admit', 'waitlist', 'deny')"))
    created_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))

class ReviewAssignment(Base):
    __tablename__ = "review_assignments"
    id = Column(Integer, primary_key=True)
    application_id = Column(Integer, ForeignKey("applications.id"))
    reviewer_id = Column(Integer, ForeignKey("users.id"))
    stage_id = Column(Integer, ForeignKey("review_stages.id"))
    assigned_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))
    created_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, default=text("CURRENT_TIMESTAMP"))

# Response model
class ReviewerWorkloadStats(BaseModel):
    reviewer_id: int
    reviewer_username: str
    stage_id: int
    stage_name: str
    committee_name: str
    total_assigned_applications: int
    decided_applications_count: int
    decision_percentage: float

# Create FastAPI app
app = FastAPI()

@app.get("/reviewer-workload", response_model=List[ReviewerWorkloadStats], operation_id="get_reviewer_workload_stats")
def get_reviewer_workload_stats(
    program_name: str = Query(..., description="The name of the program (e.g., 'Master of Business Administration')."),
    term_name: str = Query(..., description="The name of the term (e.g., 'Fall 2025').")
):
    db = SessionLocal()
    try:
        # SQL query to fetch reviewer workload stats
        sql_query = text("""
            SELECT 
                u.id AS reviewer_id,
                u.username AS reviewer_username,
                rs.id AS stage_id,
                rs.name AS stage_name,
                rc.name AS committee_name,
                COUNT(DISTINCT ra.application_id) AS total_assigned_applications,
                COUNT(DISTINCT CASE WHEN a.final_decision IS NOT NULL THEN ra.application_id END) AS decided_applications_count,
                CASE 
                    WHEN COUNT(DISTINCT ra.application_id) = 0 THEN 0.0
                    ELSE (COUNT(DISTINCT CASE WHEN a.final_decision IS NOT NULL THEN ra.application_id END) * 100.0 / COUNT(DISTINCT ra.application_id))
                END AS decision_percentage
            FROM users u
            JOIN review_assignments ra ON u.id = ra.reviewer_id
            JOIN review_stages rs ON ra.stage_id = rs.id
            JOIN review_committees rc ON rs.committee_id = rc.id
            JOIN applications a ON ra.application_id = a.id
            JOIN program_terms pt ON a.program_term_id = pt.id
            JOIN programs p ON pt.program_id = p.id
            JOIN terms t ON pt.term_id = t.id
            WHERE p.name = :program_name AND t.name = :term_name
            GROUP BY u.id, rs.id, rc.name
            ORDER BY u.id, rs.stage_order
        """)
        
        result = db.execute(sql_query, {"program_name": program_name, "term_name": term_name})
        return [
            ReviewerWorkloadStats(
                reviewer_id=row.reviewer_id,
                reviewer_username=row.reviewer_username,
                stage_id=row.stage_id,
                stage_name=row.stage_name,
                committee_name=row.committee_name,
                total_assigned_applications=row.total_assigned_applications,
                decided_applications_count=row.decided_applications_count,
                decision_percentage=row.decision_percentage
            ) for row in result.fetchall()
        ]
    finally:
        db.close()
