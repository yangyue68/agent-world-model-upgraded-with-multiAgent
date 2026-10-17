import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

# Assume DATABASE_PATH is provided in the environment or context as per instructions
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class ApplicationResponse(BaseModel):
    id: int
    applicant_id: int
    scholarship_id: int
    status: str


class ReviewResponse(BaseModel):
    id: int
    application_id: int
    reviewer_id: int
    academic_merit_score: int


@app.get(
    "/applications/assigned-to-reviewer",
    response_model=List[ApplicationResponse],
    operation_id="find_assigned_applications",
    tags=["PrimaryResource"],
)
def find_assigned_applications():
    """
    Retrieve all applications assigned to the current reviewer that are currently in 'under review' status.
    """
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Current user ID not found in environment.")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid current user ID format.")

    query = text("""
        SELECT DISTINCT a.id, a.applicant_id, a.scholarship_id, a.status
        FROM applications a
        JOIN reviews r ON a.id = r.application_id
        WHERE r.reviewer_id = :reviewer_id
          AND a.status = 'under review'
    """)

    with engine.connect() as conn:
        result = conn.execute(query, {"reviewer_id": current_user_id})
        rows = result.fetchall()

    return [
        ApplicationResponse(
            id=row[0],
            applicant_id=row[1],
            scholarship_id=row[2],
            status=row[3],
        )
        for row in rows
    ]


@app.post(
    "/applications/{application_id}/review",
    response_model=ReviewResponse,
    operation_id="submit_academic_merit_review",
    tags=["PrimaryResource"],
)
def submit_academic_merit_review(application_id: int = Path(...)):
    """
    Provide a score of 4 on the 'Academic Merit' criterion for the specified application.
    """
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Current user ID not found in environment.")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid current user ID format.")

    # Check if application exists and is under review
    check_app_query = text("""
        SELECT id, status FROM applications WHERE id = :app_id
    """)
    
    with engine.begin() as conn:
        app_row = conn.execute(check_app_query, {"app_id": application_id}).fetchone()
        if not app_row:
            raise HTTPException(status_code=404, detail="Application not found.")
        
        if app_row[1] != 'under review':
            raise HTTPException(status_code=400, detail="Application is not under review.")

        # Insert or update review? The spec says "Provide a score...". 
        # Looking at schema, reviews table has no unique constraint on (application_id, reviewer_id).
        # However, typically one might expect an upsert or just insert. 
        # Given the initial snapshot has a review with null score, it implies we might be updating existing ones or creating new.
        # But the task says "retrieve all applications assigned to you... then provide a score".
        # If there's already a review record for this reviewer/app, should we update it?
        # The API returns a ReviewResponse object.
        # Let's assume standard behavior: if a review exists for this reviewer and app, update it. Otherwise insert.
        # Or simply insert a new review entry if the system allows multiple reviews per reviewer per app.
        # Without explicit "update" semantics in path, POST usually creates. But "submit ... score" often implies setting the value.
        # Let's look at the initial data: Review id 1, app 1, reviewer 2, score null.
        # If I am reviewer 2, and I submit score 4 for app 1, do I create a new row or update row 1?
        # Usually, in such systems, a reviewer submits their evaluation once. So updating makes sense if a placeholder exists.
        # However, strict REST POST /resource usually creates.
        # Let's try to find if a review exists for this reviewer and application.
        
        find_review_query = text("""
            SELECT id FROM reviews WHERE application_id = :app_id AND reviewer_id = :rev_id
        """)
        existing_review = conn.execute(find_review_query, {"app_id": application_id, "rev_id": current_user_id}).fetchone()
        
        score_to_set = 4
        
        if existing_review:
            update_query = text("""
                UPDATE reviews 
                SET academic_merit_score = :score, updated_at = CURRENT_TIMESTAMP
                WHERE id = :id
            """)
            conn.execute(update_query, {"score": score_to_set, "id": existing_review[0]})
            
            get_updated_query = text("""
                SELECT id, application_id, reviewer_id, academic_merit_score 
                FROM reviews WHERE id = :id
            """)
            res = conn.execute(get_updated_query, {"id": existing_review[0]}).fetchone()
        else:
            insert_query = text("""
                INSERT INTO reviews (application_id, reviewer_id, academic_merit_score, created_at, updated_at)
                VALUES (:app_id, :rev_id, :score, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """)
            conn.execute(insert_query, {"app_id": application_id, "rev_id": current_user_id, "score": score_to_set})
            
            # Get the last inserted ID
            last_id_res = conn.execute(text("SELECT last_insert_rowid()")).fetchone()
            new_id = last_id_res[0]
            
            get_new_query = text("""
                SELECT id, application_id, reviewer_id, academic_merit_score 
                FROM reviews WHERE id = :id
            """)
            res = conn.execute(get_new_query, {"id": new_id}).fetchone()

    return ReviewResponse(
        id=res[0],
        application_id=res[1],
        reviewer_id=res[2],
        academic_merit_score=res[3],
    )
