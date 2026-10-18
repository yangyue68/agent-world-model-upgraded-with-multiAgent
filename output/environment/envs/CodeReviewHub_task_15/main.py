import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class CriterionPerformance(BaseModel):
    criterion_id: int
    criterion_name: str
    average_score: float


class DetailedBreakdownItem(BaseModel):
    submission_id: int
    user_id: int
    user_name: str
    score: float
    feedback: Optional[str] = None


@app.get(
    "/assignments/{assignment_id}/rubric_criteria_performance",
    response_model=List[CriterionPerformance],
    operation_id="get_assignment_rubric_criteria_performance",
)
def get_assignment_rubric_criteria_performance(assignment_id: int):
    """
    Retrieve the average scores for each rubric criterion across all submissions 
    for a specific assignment, ordered by average score ascending.
    """
    query = text("""
        SELECT 
            rc.id AS criterion_id,
            rc.name AS criterion_name,
            AVG(cs.score) AS average_score
        FROM rubric_criteria rc
        JOIN criterion_scores cs ON rc.id = cs.criterion_id
        JOIN submissions s ON cs.submission_id = s.id
        WHERE s.assignment_id = :assignment_id
        GROUP BY rc.id, rc.name
        ORDER BY average_score ASC
    """)

    with engine.connect() as conn:
        result = conn.execute(query, {"assignment_id": assignment_id})
        rows = result.fetchall()

    return [
        CriterionPerformance(
            criterion_id=row.criterion_id,
            criterion_name=row.criterion_name,
            average_score=float(row.average_score) if row.average_score is not None else 0.0
        )
        for row in rows
    ]


@app.get(
    "/assignments/{assignment_id}/lowest_performing_criteria",
    response_model=List[CriterionPerformance],
    operation_id="get_lowest_performing_criteria",
)
def get_lowest_performing_criteria(assignment_id: int):
    """
    Identify the three rubric criteria with the lowest average scores across all 
    submissions for a given assignment.
    """
    query = text("""
        SELECT 
            rc.id AS criterion_id,
            rc.name AS criterion_name,
            AVG(cs.score) AS average_score
        FROM rubric_criteria rc
        JOIN criterion_scores cs ON rc.id = cs.criterion_id
        JOIN submissions s ON cs.submission_id = s.id
        WHERE s.assignment_id = :assignment_id
        GROUP BY rc.id, rc.name
        ORDER BY average_score ASC
        LIMIT 3
    """)

    with engine.connect() as conn:
        result = conn.execute(query, {"assignment_id": assignment_id})
        rows = result.fetchall()

    return [
        CriterionPerformance(
            criterion_id=row.criterion_id,
            criterion_name=row.criterion_name,
            average_score=float(row.average_score) if row.average_score is not None else 0.0
        )
        for row in rows
    ]


@app.get(
    "/assignments/{assignment_id}/criteria/{criterion_id}/detailed_breakdown",
    response_model=List[DetailedBreakdownItem],
    operation_id="get_criterion_detailed_breakdown",
)
def get_criterion_detailed_breakdown(assignment_id: int, criterion_id: int):
    """
    View detailed score distribution and feedback for a specific rubric criterion 
    across all submissions for an assignment.
    """
    # Verify that the criterion belongs to the specified assignment to prevent cross-assignment data leakage
    check_query = text("""
        SELECT COUNT(*) 
        FROM rubric_criteria 
        WHERE id = :criterion_id AND assignment_id = :assignment_id
    """)
    
    with engine.connect() as conn:
        count_result = conn.execute(check_query, {
            "criterion_id": criterion_id,
            "assignment_id": assignment_id
        }).scalar()
        
        if count_result == 0:
            raise HTTPException(status_code=404, detail="Rubric criterion not found for this assignment")

    query = text("""
        SELECT 
            s.id AS submission_id,
            u.id AS user_id,
            COALESCE(u.full_name, u.username) AS user_name,
            cs.score,
            cs.feedback
        FROM criterion_scores cs
        JOIN submissions s ON cs.submission_id = s.id
        JOIN users u ON s.user_id = u.id
        WHERE s.assignment_id = :assignment_id
          AND cs.criterion_id = :criterion_id
        ORDER BY cs.score ASC
    """)

    with engine.connect() as conn:
        result = conn.execute(query, {
            "assignment_id": assignment_id,
            "criterion_id": criterion_id
        })
        rows = result.fetchall()

    return [
        DetailedBreakdownItem(
            submission_id=row.submission_id,
            user_id=row.user_id,
            user_name=row.user_name,
            score=float(row.score),
            feedback=row.feedback
        )
        for row in rows
    ]
