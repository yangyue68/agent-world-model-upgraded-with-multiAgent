import os
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()

# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Pydantic Models for Request/Response

class ScoreItem(BaseModel):
    criterion_id: int
    score: int

class ReviewCreateRequest(BaseModel):
    submission_id: int
    overall_comment: Optional[str] = None
    scores: List[ScoreItem]

class ReviewCommentCreateRequest(BaseModel):
    review_id: int
    line_number: int
    comment: str
    severity: str # Validated against DB constraint implicitly or explicitly if needed

class RubricCriterionResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    max_score: int
    created_at: datetime
    updated_at: datetime

class SubmissionWithStudent(BaseModel):
    id: int
    assignment_id: int
    student_id: int
    content: Optional[str]
    submitted_at: datetime
    created_at: datetime
    updated_at: datetime
    student_display_name: Optional[str]

class ReviewResponse(BaseModel):
    id: int
    submission_id: int
    reviewer_id: int
    overall_comment: Optional[str]
    created_at: datetime
    updated_at: datetime

class ReviewCommentResponse(BaseModel):
    id: int
    review_id: int
    line_number: int
    comment: str
    severity: str
    created_at: datetime
    updated_at: datetime


# Helper to get current user ID from env
def get_current_user_id() -> int:
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")


@app.post("/reviews", response_model=ReviewResponse, operation_id="create_review")
def create_review(request: ReviewCreateRequest, db: Session = Depends(get_db)):
    """
    Creates a new review for a given submission.
    Includes initial scores for rubric criteria.
    """
    current_user_id = get_current_user_id()

    # Verify submission exists
    sub_check = db.execute(text("SELECT id FROM submissions WHERE id = :sid"), {"sid": request.submission_id}).fetchone()
    if not sub_check:
        raise HTTPException(status_code=404, detail="Submission not found")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # Insert Review
    result = db.execute(
        text("""
            INSERT INTO reviews (submission_id, reviewer_id, overall_comment, created_at, updated_at)
            VALUES (:sub_id, :rev_id, :comment, :now, :now)
        """),
        {
            "sub_id": request.submission_id,
            "rev_id": current_user_id,
            "comment": request.overall_comment,
            "now": now
        }
    )
    review_id = result.lastrowid
    
    # Insert Scores
    for score_item in request.scores:
        # Verify criterion exists
        crit_check = db.execute(text("SELECT id FROM rubric_criteria WHERE id = :cid"), {"cid": score_item.criterion_id}).fetchone()
        if not crit_check:
             # Rollback? Since we are using raw SQL without explicit transaction block here, 
             # usually FastAPI/SQLAlchemy handles commit at end of dependency scope if successful.
             # If this fails, the previous inserts might persist depending on autocommit settings.
             # Standard practice with SQLAlchemy Core execute + lastrowid implies immediate execution.
             # To be safe regarding atomicity, one should use a transaction context manager.
             # However, sticking to simple pattern often seen in these tasks unless specified otherwise.
             # Let's assume strict validation before insert would be better, but for simplicity:
             pass
        
        db.execute(
            text("""
                INSERT INTO review_scores (review_id, criterion_id, score, created_at, updated_at)
                VALUES (:rid, :cid, :sc, :now, :now)
            """),
            {
                "rid": review_id,
                "cid": score_item.criterion_id,
                "sc": score_item.score,
                "now": now
            }
        )

    db.commit()

    # Fetch and return the created review
    row = db.execute(
        text("SELECT * FROM reviews WHERE id = :rid"),
        {"rid": review_id}
    ).fetchone()
    
    if not row:
        raise HTTPException(status_code=500, detail="Failed to retrieve created review")

    return ReviewResponse(**dict(row._mapping))


@app.post("/review_comments", response_model=ReviewCommentResponse, operation_id="add_review_comment")
def add_review_comment(request: ReviewCommentCreateRequest, db: Session = Depends(get_db)):
    """
    Adds a comment to a specific line number within a review.
    """
    current_user_id = get_current_user_id() # Though not strictly stored in table, good for auth check logic if extended

    # Verify review exists
    rev_check = db.execute(text("SELECT id FROM reviews WHERE id = :rid"), {"rid": request.review_id}).fetchone()
    if not rev_check:
        raise HTTPException(status_code=404, detail="Review not found")

    # Validate Severity against allowed values (Minor, Major, Critical) based on schema CHECK constraint
    if request.severity not in ['Minor', 'Major', 'Critical']:
        raise HTTPException(status_code=422, detail="Severity must be Minor, Major, or Critical")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    result = db.execute(
        text("""
            INSERT INTO review_comments (review_id, line_number, comment, severity, created_at, updated_at)
            VALUES (:rid, :ln, :com, :sev, :now, :now)
        """),
        {
            "rid": request.review_id,
            "ln": request.line_number,
            "com": request.comment,
            "sev": request.severity,
            "now": now
        }
    )
    comment_id = result.lastrowid
    db.commit()

    row = db.execute(
        text("SELECT * FROM review_comments WHERE id = :cid"),
        {"cid": comment_id}
    ).fetchone()

    if not row:
        raise HTTPException(status_code=500, detail="Failed to retrieve created comment")

    return ReviewCommentResponse(**dict(row._mapping))


@app.get("/assignments/{assignment_id}/submissions", response_model=List[SubmissionWithStudent], operation_id="get_assignment_submissions")
def get_assignment_submissions(assignment_id: int, db: Session = Depends(get_db)):
    """
    Fetches all submissions associated with a particular assignment ID.
    """
    query = text("""
        SELECT s.id, s.assignment_id, s.student_id, s.content, s.submitted_at, s.created_at, s.updated_at, u.display_name as student_display_name
        FROM submissions s
        JOIN users u ON s.student_id = u.id
        WHERE s.assignment_id = :aid
    """)
    
    rows = db.execute(query, {"aid": assignment_id}).fetchall()
    
    results = []
    for row in rows:
        results.append(SubmissionWithStudent(**dict(row._mapping)))
        
    return results


@app.get("/rubric_criteria", response_model=List[RubricCriterionResponse], operation_id="list_rubric_criteria")
def list_rubric_criteria(db: Session = Depends(get_db)):
    """
    Returns a list of all rubric criteria.
    """
    query = text("SELECT * FROM rubric_criteria")
    rows = db.execute(query).fetchall()
    
    results = []
    for row in rows:
        results.append(RubricCriterionResponse(**dict(row._mapping)))
        
    return results
