import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class EligibilityUpdateRequest(BaseModel):
    min_degree: str
    min_years_experience: int
    exam_passing_score: float


class EligibilityResponse(BaseModel):
    id: int
    certification_id: int
    min_degree: str
    min_years_experience: int
    exam_passing_score: float
    created_at: str
    updated_at: str


@app.put(
    "/certifications/{certification_id}/eligibility",
    response_model=EligibilityResponse,
    operation_id="update_certification_eligibility"
)
def update_certification_eligibility(
    certification_id: int,
    payload: EligibilityUpdateRequest,
):
    # Verify the certification exists
    with engine.connect() as conn:
        cert_row = conn.execute(
            text("SELECT id FROM certifications WHERE id = :cid"),
            {"cid": certification_id},
        ).fetchone()

        if cert_row is None:
            raise HTTPException(status_code=404, detail="Certification not found")

        # Check if eligibility criteria row exists for this certification
        existing = conn.execute(
            text("SELECT id FROM eligibility_criteria WHERE certification_id = :cid"),
            {"cid": certification_id},
        ).fetchone()

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        if existing:
            # Update existing record
            ec_id = existing[0]
            conn.execute(
                text("""
                    UPDATE eligibility_criteria
                    SET min_degree = :min_degree,
                        min_years_experience = :min_years_exp,
                        exam_passing_score = :exam_score,
                        updated_at = :updated_at
                    WHERE id = :ec_id
                """),
                {
                    "min_degree": payload.min_degree,
                    "min_years_exp": payload.min_years_experience,
                    "exam_score": payload.exam_passing_score,
                    "updated_at": now_str,
                    "ec_id": ec_id,
                },
            )
            conn.commit()

            # Fetch updated record
            result = conn.execute(
                text("""
                    SELECT id, certification_id, min_degree, min_years_experience,
                           exam_passing_score, created_at, updated_at
                    FROM eligibility_criteria
                    WHERE id = :ec_id
                """),
                {"ec_id": ec_id},
            ).fetchone()
        else:
            # Insert new record
            conn.execute(
                text("""
                    INSERT INTO eligibility_criteria (
                        certification_id, min_degree, min_years_experience,
                        exam_passing_score, created_at, updated_at
                    ) VALUES (
                        :cid, :min_degree, :min_years_exp,
                        :exam_score, :created_at, :updated_at
                    )
                """),
                {
                    "cid": certification_id,
                    "min_degree": payload.min_degree,
                    "min_years_exp": payload.min_years_experience,
                    "exam_score": payload.exam_passing_score,
                    "created_at": now_str,
                    "updated_at": now_str,
                },
            )
            conn.commit()

            # Fetch inserted record
            result = conn.execute(
                text("""
                    SELECT id, certification_id, min_degree, min_years_experience,
                           exam_passing_score, created_at, updated_at
                    FROM eligibility_criteria
                    WHERE certification_id = :cid
                """),
                {"cid": certification_id},
            ).fetchone()

    if result is None:
        raise HTTPException(status_code=500, detail="Failed to retrieve updated eligibility criteria")

    return EligibilityResponse(
        id=result[0],
        certification_id=result[1],
        min_degree=result[2],
        min_years_experience=result[3],
        exam_passing_score=float(result[4]),
        created_at=str(result[5]),
        updated_at=str(result[6]),
    )
