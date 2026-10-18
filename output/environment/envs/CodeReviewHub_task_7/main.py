import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")  # Placeholder, will be replaced by runtime injection if needed, but spec says DATABASE_PATH already contains URL. 
# Actually, the prompt says: "`DATABASE_PATH` already contains the complete SQLAlchemy URL."
# So I should just use it directly.

engine = create_engine(DATABASE_PATH)

class CohortCreate(BaseModel):
    name: str
    enrollment_cap: int
    start_date: str

class CohortResponse(BaseModel):
    id: int
    name: str
    enrollment_cap: int
    start_date: str
    created_by: int
    created_at: str
    updated_at: str

app = FastAPI()

@app.post("/cohorts", response_model=CohortResponse, operation_id="create_cohort")
def create_cohort(cohort_data: CohortCreate):
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    # Validate start_date format YYYY-MM-DD
    try:
        parsed_date = datetime.strptime(cohort_data.start_date, "%Y-%m-%d").date()
        formatted_start_date = parsed_date.isoformat()
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Expected YYYY-MM-DD.")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    with engine.begin() as conn:
        # Insert new cohort
        result = conn.execute(
            text("""
                INSERT INTO cohorts (name, enrollment_cap, start_date, created_by, created_at, updated_at)
                VALUES (:name, :enrollment_cap, :start_date, :created_by, :now, :now)
            """),
            {
                "name": cohort_data.name,
                "enrollment_cap": cohort_data.enrollment_cap,
                "start_date": formatted_start_date,
                "created_by": user_id,
                "now": now
            }
        )
        
        cohort_id = result.lastrowid
        
        # Fetch the created record to ensure consistency and get default values if any were applied differently
        row = conn.execute(
            text("""
                SELECT id, name, enrollment_cap, start_date, created_by, created_at, updated_at
                FROM cohorts
                WHERE id = :id
            """),
            {"id": cohort_id}
        ).fetchone()

    if not row:
        raise HTTPException(status_code=500, detail="Failed to retrieve created cohort")

    return CohortResponse(
        id=row.id,
        name=row.name,
        enrollment_cap=row.enrollment_cap,
        start_date=str(row.start_date),
        created_by=row.created_by,
        created_at=str(row.created_at),
        updated_at=str(row.updated_at)
    )
