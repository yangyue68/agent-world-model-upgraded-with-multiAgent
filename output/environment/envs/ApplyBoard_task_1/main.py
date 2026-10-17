import os
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class ApplicationResponse(BaseModel):
    id: int
    applicant_name: str
    program_name: str
    status: str
    created_at: str
    updated_at: str


class RubricItem(BaseModel):
    id: int
    criterion: str
    max_score: int


class ScoreInput(BaseModel):
    criterion_id: int
    score: int
    comment: Optional[str] = None


class RecommendationInput(BaseModel):
    recommendation: str
    overall_comment: Optional[str] = None
    reviewer_name: str


class MessageResponse(BaseModel):
    message: str


@app.get("/applications/review", response_model=ApplicationResponse, operation_id="find_application_for_review")
def find_application_for_review(
    applicant_name: str = Query(...),
    program_name: str = Query(...)
):
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT id, applicant_name, program_name, status, created_at, updated_at
                FROM applications
                WHERE applicant_name = :applicant_name AND program_name = :program_name
            """),
            {"applicant_name": applicant_name, "program_name": program_name}
        ).fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Application not found")
        return ApplicationResponse(
            id=result[0],
            applicant_name=result[1],
            program_name=result[2],
            status=result[3],
            created_at=str(result[4]),
            updated_at=str(result[5])
        )


@app.get("/applications/{application_id}/rubrics", response_model=List[RubricItem], operation_id="get_application_rubrics")
def get_application_rubrics(application_id: int):
    with engine.connect() as conn:
        # Verify application exists
        app_result = conn.execute(
            text("SELECT id FROM applications WHERE id = :id"),
            {"id": application_id}
        ).fetchone()
        if not app_result:
            raise HTTPException(status_code=404, detail="Application not found")

        results = conn.execute(
            text("""
                SELECT id, criterion, max_score
                FROM rubrics
                ORDER BY id
            """)
        ).fetchall()
        return [RubricItem(id=r[0], criterion=r[1], max_score=r[2]) for r in results]


@app.post("/applications/{application_id}/scores", response_model=MessageResponse, operation_id="submit_application_scores")
def submit_application_scores(application_id: int, scores: List[ScoreInput]):
    with engine.begin() as conn:
        # Verify application exists
        app_result = conn.execute(
            text("SELECT id FROM applications WHERE id = :id"),
            {"id": application_id}
        ).fetchone()
        if not app_result:
            raise HTTPException(status_code=404, detail="Application not found")

        for item in scores:
            # Validate criterion exists
            crit_result = conn.execute(
                text("SELECT id, max_score FROM rubrics WHERE id = :id"),
                {"id": item.criterion_id}
            ).fetchone()
            if not crit_result:
                raise HTTPException(status_code=404, detail=f"Criterion {item.criterion_id} not found")
            max_score = crit_result[1]
            if item.score < 0 or item.score > max_score:
                raise HTTPException(status_code=400, detail=f"Score must be between 0 and {max_score}")

            # Upsert into application_scores
            existing = conn.execute(
                text("SELECT 1 FROM application_scores WHERE application_id = :aid AND criterion_id = :cid"),
                {"aid": application_id, "cid": item.criterion_id}
            ).fetchone()
            if existing:
                conn.execute(
                    text("""
                        UPDATE application_scores
                        SET score = :score, comment = :comment, updated_at = CURRENT_TIMESTAMP
                        WHERE application_id = :aid AND criterion_id = :cid
                    """),
                    {"score": item.score, "comment": item.comment, "aid": application_id, "cid": item.criterion_id}
                )
            else:
                conn.execute(
                    text("""
                        INSERT INTO application_scores (application_id, criterion_id, score, comment, created_at, updated_at)
                        VALUES (:aid, :cid, :score, :comment, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """),
                    {"aid": application_id, "cid": item.criterion_id, "score": item.score, "comment": item.comment}
                )

    return MessageResponse(message="Scores submitted successfully")


@app.post("/applications/{application_id}/recommendation", response_model=MessageResponse, operation_id="submit_recommendation")
def submit_recommendation(application_id: int, rec_input: RecommendationInput):
    with engine.begin() as conn:
        # Verify application exists
        app_result = conn.execute(
            text("SELECT id FROM applications WHERE id = :id"),
            {"id": application_id}
        ).fetchone()
        if not app_result:
            raise HTTPException(status_code=404, detail="Application not found")

        # Check if recommendation already exists
        existing = conn.execute(
            text("SELECT 1 FROM recommendations WHERE application_id = :aid"),
            {"aid": application_id}
        ).fetchone()
        if existing:
            conn.execute(
                text("""
                    UPDATE recommendations
                    SET recommendation = :rec, overall_comment = :oc, reviewer_name = :rn, updated_at = CURRENT_TIMESTAMP
                    WHERE application_id = :aid
                """),
                {"rec": rec_input.recommendation, "oc": rec_input.overall_comment, "rn": rec_input.reviewer_name, "aid": application_id}
            )
        else:
            conn.execute(
                text("""
                    INSERT INTO recommendations (application_id, recommendation, overall_comment, reviewer_name, created_at, updated_at)
                    VALUES (:aid, :rec, :oc, :rn, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """),
                {"aid": application_id, "rec": rec_input.recommendation, "oc": rec_input.overall_comment, "rn": rec_input.reviewer_name}
            )

    return MessageResponse(message="Recommendation submitted successfully")
