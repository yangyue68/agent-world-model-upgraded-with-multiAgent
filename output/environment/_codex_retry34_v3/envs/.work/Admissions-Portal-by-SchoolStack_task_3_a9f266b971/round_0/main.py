import os
from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text, func, and_
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from typing import List

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class Program(Base):
    __tablename__ = "programs"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    degree_type = Column(String, nullable=False)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class Term(Base):
    __tablename__ = "terms"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    start_date = Column(String)
    end_date = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class ProgramTerm(Base):
    __tablename__ = "program_terms"
    id = Column(Integer, primary_key=True, index=True)
    program_id = Column(Integer, ForeignKey("programs.id"))
    term_id = Column(Integer, ForeignKey("terms.id"))
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class ReviewCommittee(Base):
    __tablename__ = "review_committees"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class ReviewStage(Base):
    __tablename__ = "review_stages"
    id = Column(Integer, primary_key=True, index=True)
    committee_id = Column(Integer, ForeignKey("review_committees.id"))
    name = Column(String, nullable=False)
    stage_order = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class Applicant(Base):
    __tablename__ = "applicants"
    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class Application(Base):
    __tablename__ = "applications"
    id = Column(Integer, primary_key=True, index=True)
    applicant_id = Column(Integer, ForeignKey("applicants.id"))
    program_term_id = Column(Integer, ForeignKey("program_terms.id"))
    submitted_at = Column(DateTime)
    final_decision = Column(Text, check_constraint="final_decision IN ('admit', 'waitlist', 'deny')")
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class ReviewAssignment(Base):
    __tablename__ = "review_assignments"
    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("applications.id"))
    reviewer_id = Column(Integer, ForeignKey("users.id"))
    stage_id = Column(Integer, ForeignKey("review_stages.id"))
    assigned_at = Column(DateTime, default=func.current_timestamp())
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

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

# FastAPI app
app = FastAPI()

@app.get("/reviewer-workload", response_model=List[ReviewerWorkloadStats], operation_id="get_reviewer_workload_stats")
def get_reviewer_workload_stats(
    program_name: str = Query(..., description="The name of the program (e.g., 'Master of Business Administration')."),
    term_name: str = Query(..., description="The name of the term (e.g., 'Fall 2025').")
):
    db = SessionLocal()
    try:
        # Subquery to get all applications assigned to reviewers for the specified program and term
        subquery = (
            db.query(
                ReviewAssignment.application_id,
                ReviewAssignment.reviewer_id,
                ReviewAssignment.stage_id,
                func.count(Application.id).label('total_count'),
                func.sum(func.case((Application.final_decision.isnot(None), 1), else_=0)).label('decided_count')
            )
            .join(Application, ReviewAssignment.application_id == Application.id)
            .join(ProgramTerm, Application.program_term_id == ProgramTerm.id)
            .join(Program, ProgramTerm.program_id == Program.id)
            .join(Term, ProgramTerm.term_id == Term.id)
            .filter(
                and_(
                    Program.name == program_name,
                    Term.name == term_name
                )
            )
            .group_by(ReviewAssignment.application_id, ReviewAssignment.reviewer_id, ReviewAssignment.stage_id)
            .subquery()
        )

        # Main query to aggregate results by reviewer and stage
        result = (
            db.query(
                User.id.label('reviewer_id'),
                User.username.label('reviewer_username'),
                ReviewStage.id.label('stage_id'),
                ReviewStage.name.label('stage_name'),
                ReviewCommittee.name.label('committee_name'),
                func.sum(subquery.c.total_count).label('total_assigned_applications'),
                func.sum(subquery.c.decided_count).label('decided_applications_count'),
                func.coalesce(
                    func.round(
                        func.avg(func.cast(subquery.c.decided_count, func.FLOAT) / func.cast(subquery.c.total_count, func.FLOAT)) * 100,
                        2
                    ),
                    0.0
                ).label('decision_percentage')
            )
            .select_from(subquery)
            .join(User, subquery.c.reviewer_id == User.id)
            .join(ReviewStage, subquery.c.stage_id == ReviewStage.id)
            .join(ReviewCommittee, ReviewStage.committee_id == ReviewCommittee.id)
            .group_by(User.id, ReviewStage.id, ReviewCommittee.name)
            .order_by(User.id, ReviewStage.stage_order)
        ).all()

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
            ) for row in result
        ]
    finally:
        db.close()
