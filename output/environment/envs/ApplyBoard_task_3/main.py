import os
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class RecommendationResponse(BaseModel):
    id: int
    application_id: int
    reviewer_id: int
    decision: str
    comment: str
    created_at: str
    updated_at: str


class ApplicationResponse(BaseModel):
    id: int
    applicant_name: str
    grant_name: str
    status: str
    created_at: str
    updated_at: str


class SubmitRecommendationRequest(BaseModel):
    decision: str
    comment: str


def get_db():
    db = Session(engine)
    try:
        yield db
    finally:
        db.close()


@app.post(
    "/applications/{application_id}/recommendations",
    response_model=RecommendationResponse,
    operation_id="submit_rejection_recommendation",
)
def submit_rejection_recommendation(application_id: int, request: SubmitRecommendationRequest):
    if request.decision != "reject":
        raise HTTPException(status_code=400, detail="Decision must be 'reject'")

    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Missing user ID")
    
    try:
        reviewer_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")

    with engine.connect() as conn:
        # Verify application exists
        app_result = conn.execute(
            text("SELECT id FROM applications WHERE id = :app_id"),
            {"app_id": application_id}
        ).fetchone()
        
        if not app_result:
            raise HTTPException(status_code=404, detail="Application not found")

        # Verify reviewer exists
        reviewer_result = conn.execute(
            text("SELECT id FROM reviewers WHERE id = :reviewer_id"),
            {"reviewer_id": reviewer_id}
        ).fetchone()
        
        if not reviewer_result:
            raise HTTPException(status_code=403, detail="Reviewer not found or no permission")

        now = datetime.now(timezone.utc).isoformat()
        
        # Insert recommendation
        result = conn.execute(
            text("""
                INSERT INTO recommendations (application_id, reviewer_id, decision, comment, created_at, updated_at)
                VALUES (:application_id, :reviewer_id, :decision, :comment, :created_at, :updated_at)
            """),
            {
                "application_id": application_id,
                "reviewer_id": reviewer_id,
                "decision": request.decision,
                "comment": request.comment,
                "created_at": now,
                "updated_at": now,
            }
        )
        conn.commit()
        
        rec_id = result.lastrowid
        
        # Fetch the created recommendation to return it
        rec_row = conn.execute(
            text("""
                SELECT id, application_id, reviewer_id, decision, comment, created_at, updated_at
                FROM recommendations WHERE id = :rec_id
            """),
            {"rec_id": rec_id}
        ).fetchone()
        
        if not rec_row:
            raise HTTPException(status_code=500, detail="Failed to retrieve created recommendation")
        
        return RecommendationResponse(
            id=rec_row[0],
            application_id=rec_row[1],
            reviewer_id=rec_row[2],
            decision=rec_row[3],
            comment=rec_row[4],
            created_at=str(rec_row[5]),
            updated_at=str(rec_row[6]),
        )


@app.get(
    "/applications",
    response_model=List[ApplicationResponse],
    operation_id="find_application_by_applicant_and_grant",
)
def find_application_by_applicant_and_grant(
    applicant_name: str = Query(...),
    grant_name: str = Query(...)
):
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT id, applicant_name, grant_name, status, created_at, updated_at
                FROM applications
                WHERE applicant_name = :applicant_name AND grant_name = :grant_name
            """),
            {"applicant_name": applicant_name, "grant_name": grant_name}
        ).fetchall()
        
        results = []
        for row in rows:
            results.append(ApplicationResponse(
                id=row[0],
                applicant_name=row[1],
                grant_name=row[2],
                status=row[3],
                created_at=str(row[4]),
                updated_at=str(row[5]),
            ))
        
        return results
