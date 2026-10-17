import os
from datetime import datetime, date
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class ApplicationRoundCreate(BaseModel):
    program_name: str = Field(..., description="Name of the program (e.g., 'Master of Data Science')")
    academic_year: int = Field(..., description="Academic year for the application round (e.g., 2026)")
    semester: str = Field(..., description="Semester of the application round (e.g., 'Fall')")
    start_date: str = Field(..., description="Start date of the application period in YYYY-MM-DD format")
    end_date: str = Field(..., description="End date of the application period in YYYY-MM-DD format")
    application_fee: float = Field(..., description="Application fee amount in USD")
    submission_limit: int = Field(..., description="Maximum number of applications allowed per applicant")


class ApplicationRoundResponse(BaseModel):
    id: int
    program_name: str
    academic_year: int
    semester: str
    start_date: str
    end_date: str
    application_fee: float
    submission_limit: int
    created_at: str
    updated_at: str


def parse_datetime_str(dt_str: Optional[str]) -> Optional[datetime]:
    if dt_str is None:
        return None
    # Handle both ISO-8601 with T and space-separated formats
    try:
        # Try parsing as datetime first
        if 'T' in dt_str or '-' in dt_str[:10]:
            # It's likely a full datetime or date string
            pass
        # Replace space with T for standard parsing if needed
        normalized = dt_str.replace(' ', 'T')
        if normalized.endswith('Z'):
            normalized = normalized[:-1] + '+00:00'
        return datetime.fromisoformat(normalized)
    except Exception:
        return None


@app.post("/application-rounds", response_model=ApplicationRoundResponse, operation_id="create_application_round")
def create_application_round(payload: ApplicationRoundCreate):
    """
    Create a new application round for a specified program with given dates, fee, and submission limit.
    The round is associated with a specific academic year and semester.
    """
    db = SessionLocal()
    try:
        # Check if an application round already exists for this program, academic year, and semester
        existing_query = text("""
            SELECT id FROM application_rounds 
            WHERE program_name = :program_name AND academic_year = :academic_year AND semester = :semester
        """)
        existing_result = db.execute(existing_query, {
            "program_name": payload.program_name,
            "academic_year": payload.academic_year,
            "semester": payload.semester
        }).fetchone()

        if existing_result:
            raise HTTPException(status_code=409, detail="An application round already exists for this program, academic year, and semester.")

        # Insert the new application round
        insert_query = text("""
            INSERT INTO application_rounds (
                program_name, academic_year, semester, start_date, end_date, 
                application_fee, submission_limit, created_at, updated_at
            ) VALUES (
                :program_name, :academic_year, :semester, :start_date, :end_date,
                :application_fee, :submission_limit, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
        """)
        
        db.execute(insert_query, {
            "program_name": payload.program_name,
            "academic_year": payload.academic_year,
            "semester": payload.semester,
            "start_date": payload.start_date,
            "end_date": payload.end_date,
            "application_fee": payload.application_fee,
            "submission_limit": payload.submission_limit
        })
        db.commit()

        # Fetch the newly created record to get the ID and timestamps
        fetch_query = text("""
            SELECT id, program_name, academic_year, semester, start_date, end_date, 
                   application_fee, submission_limit, created_at, updated_at 
            FROM application_rounds 
            WHERE program_name = :program_name AND academic_year = :academic_year AND semester = :semester
        """)
        result = db.execute(fetch_query, {
            "program_name": payload.program_name,
            "academic_year": payload.academic_year,
            "semester": payload.semester
        }).fetchone()

        if not result:
            raise HTTPException(status_code=500, detail="Failed to retrieve created application round.")

        row_dict = dict(result._mapping)
        
        # Format dates/datetime strings appropriately for response
        def format_dt(val):
            if val is None:
                return None
            if isinstance(val, datetime):
                return val.isoformat()
            if isinstance(val, date):
                return val.isoformat()
            return str(val)

        response_data = {
            "id": row_dict["id"],
            "program_name": row_dict["program_name"],
            "academic_year": row_dict["academic_year"],
            "semester": row_dict["semester"],
            "start_date": format_dt(row_dict["start_date"]),
            "end_date": format_dt(row_dict["end_date"]),
            "application_fee": row_dict["application_fee"],
            "submission_limit": row_dict["submission_limit"],
            "created_at": format_dt(row_dict["created_at"]),
            "updated_at": format_dt(row_dict["updated_at"])
        }

        return ApplicationRoundResponse(**response_data)

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
    finally:
        db.close()
