import os
from typing import List

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)


class ReviewCoverageItem(BaseModel):
    assignment_id: int
    assignment_title: str
    total_submissions: int
    reviewed_submissions: int
    review_percentage: float


app = FastAPI()


@app.get("/dashboard/review-coverage", response_model=List[ReviewCoverageItem], operation_id="get_review_coverage_dashboard")
def get_review_coverage_dashboard(course_code: str = Query(..., description="Course code to filter assignments (e.g., 'CS101')")) -> List[ReviewCoverageItem]:
    query = text("""
        SELECT 
            a.id AS assignment_id,
            a.title AS assignment_title,
            COUNT(s.id) AS total_submissions,
            SUM(CASE WHEN r.status = 'completed' THEN 1 ELSE 0 END) AS reviewed_submissions
        FROM assignments a
        JOIN course_offerings co ON a.course_offering_id = co.id
        JOIN courses c ON co.course_id = c.id
        JOIN semesters s_sem ON co.semester_id = s_sem.id
        LEFT JOIN submissions s ON a.id = s.assignment_id
        LEFT JOIN reviews r ON s.id = r.submission_id AND r.status = 'completed'
        WHERE c.code = :course_code
          AND s_sem.is_current = 1
        GROUP BY a.id, a.title
        HAVING CAST(SUM(CASE WHEN r.status = 'completed' THEN 1 ELSE 0 END) AS REAL) / NULLIF(COUNT(s.id), 0) < 0.8
           OR COUNT(s.id) = 0
    """)

    with engine.connect() as conn:
        result = conn.execute(query, {"course_code": course_code})
        rows = result.fetchall()

    items = []
    for row in rows:
        total_subs = row.total_submissions or 0
        reviewed_subs = row.reviewed_submissions or 0
        
        if total_subs == 0:
            percentage = 0.0
        else:
            percentage = round((reviewed_subs / total_subs) * 100, 2)

        items.append(ReviewCoverageItem(
            assignment_id=row.assignment_id,
            assignment_title=row.assignment_title,
            total_submissions=total_subs,
            reviewed_submissions=reviewed_subs,
            review_percentage=percentage
        ))

    return items
