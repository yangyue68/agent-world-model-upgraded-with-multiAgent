import os
from typing import List, Optional

from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Read DATABASE_PATH at module import
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

# Create SQLAlchemy engine
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="Admissions Portal by SchoolStack")


class ReviewerWorkloadStats(BaseModel):
    reviewer_id: int
    reviewer_username: str
    stage_id: int
    stage_name: str
    committee_name: str
    total_assigned_applications: int
    decided_applications_count: int
    decision_percentage: float


@app.get("/reviewer-workload", response_model=List[ReviewerWorkloadStats], operation_id="get_reviewer_workload_stats", tags=["ReviewAssignments"])
def get_reviewer_workload_stats(
    program_name: str = Query(..., description="The name of the program (e.g., 'Master of Business Administration')."),
    term_name: str = Query(..., description="The name of the term (e.g., 'Fall 2025').")
):
    """
    Query reviewer workload statistics for a specific program and term.
    
    Calculates the number of applications assigned to each reviewer and the percentage 
    of those applications with a final decision flag of 'admit', 'waitlist', or 'deny'. 
    Results are grouped by review committee stage.
    """
    db = SessionLocal()
    try:
        # Join users, review_assignments, applications, program_terms, programs, terms, and review_stages
        query = text("""
            SELECT 
                u.id AS reviewer_id,
                u.username AS reviewer_username,
                rs.id AS stage_id,
                rs.name AS stage_name,
                rc.name AS committee_name,
                COUNT(DISTINCT ra.application_id) AS total_assigned_applications,
                SUM(CASE WHEN a.final_decision IN ('admit', 'waitlist', 'deny') THEN 1 ELSE 0 END) AS decided_applications_count
            FROM review_assignments ra
            JOIN users u ON ra.reviewer_id = u.id
            JOIN applications a ON ra.application_id = a.id
            JOIN program_terms pt ON a.program_term_id = pt.id
            JOIN programs p ON pt.program_id = p.id
            JOIN terms t ON pt.term_id = t.id
            JOIN review_stages rs ON ra.stage_id = rs.id
            JOIN review_committees rc ON rs.committee_id = rc.id
            WHERE p.name = :program_name AND t.name = :term_name
            GROUP BY u.id, u.username, rs.id, rs.name, rc.name
            ORDER BY u.id, rs.id
        """)
        
        result = db.execute(query, {"program_name": program_name, "term_name": term_name})
        rows = result.fetchall()
        
        stats_list = []
        for row in rows:
            total_assigned = row.total_assigned_applications
            decided_count = row.decided_applications_count
            
            if total_assigned > 0:
                decision_pct = (decided_count / total_assigned) * 100.0
            else:
                decision_pct = 0.0
            
            stats_list.append(ReviewerWorkloadStats(
                reviewer_id=row.reviewer_id,
                reviewer_username=row.reviewer_username,
                stage_id=row.stage_id,
                stage_name=row.stage_name,
                committee_name=row.committee_name,
                total_assigned_applications=total_assigned,
                decided_applications_count=decided_count,
                decision_percentage=round(decision_pct, 2)
            ))
        
        return stats_list
    
    finally:
        db.close()
