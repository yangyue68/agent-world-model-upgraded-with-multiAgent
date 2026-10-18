import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class RubricCriterionCreate(BaseModel):
    name: str = Field(..., description="Name of the rubric criterion")
    weight: float = Field(..., ge=0, le=100, description="Weight of the criterion as a percentage (0-100)")
    max_score: float = Field(..., gt=0, description="Maximum score achievable for this criterion")
    description: str = Field(..., description="Description of what the criterion evaluates")


class RubricCriterionResponse(BaseModel):
    id: int
    assignment_id: int
    name: str
    weight: float
    max_score: float
    description: str
    created_at: str
    updated_at: str


@app.post(
    "/courses/{course_id}/assignments/{assignment_id}/rubric_criteria",
    response_model=RubricCriterionResponse,
    operation_id="add_rubric_criterion",
    summary="Add a new rubric criterion to an assignment",
    tags=["PrimaryResource"],
)
def add_rubric_criterion(
    course_id: int = Path(..., description="Internal ID of the course"),
    assignment_id: int = Path(..., description="Internal ID of the assignment"),
    body: RubricCriterionCreate = ...,
):
    """
    Adds a new rubric criterion to the specified assignment in a course.
    Requires the course ID, assignment ID, criterion name, weight, max score, and description.
    """
    
    # Verify that the assignment exists and belongs to the specified course
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT id 
                FROM assignments 
                WHERE id = :assignment_id AND course_id = :course_id
            """),
            {"assignment_id": assignment_id, "course_id": course_id}
        )
        if result.fetchone() is None:
            raise HTTPException(status_code=404, detail="Assignment not found or does not belong to the specified course")

        # Check for uniqueness constraint violation before inserting
        existing = conn.execute(
            text("""
                SELECT id 
                FROM rubric_criteria 
                WHERE assignment_id = :assignment_id AND name = :name
            """),
            {"assignment_id": assignment_id, "name": body.name}
        ).fetchone()
        
        if existing:
            raise HTTPException(status_code=409, detail="A rubric criterion with this name already exists for the assignment")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        try:
            insert_result = conn.execute(
                text("""
                    INSERT INTO rubric_criteria (assignment_id, name, weight, max_score, description, created_at, updated_at)
                    VALUES (:assignment_id, :name, :weight, :max_score, :description, :created_at, :updated_at)
                """),
                {
                    "assignment_id": assignment_id,
                    "name": body.name,
                    "weight": body.weight,
                    "max_score": body.max_score,
                    "description": body.description,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            conn.commit()
            
            new_id = insert_result.lastrowid
            
            # Fetch the inserted record to return full details including timestamps from DB if needed, 
            # though we just used 'now'. Using lastrowid is safe here.
            final_record = conn.execute(
                text("""
                    SELECT id, assignment_id, name, weight, max_score, description, created_at, updated_at
                    FROM rubric_criteria
                    WHERE id = :id
                """),
                {"id": new_id}
            ).fetchone()
            
            if not final_record:
                 raise HTTPException(status_code=500, detail="Failed to retrieve created rubric criterion")

            return RubricCriterionResponse(
                id=final_record.id,
                assignment_id=final_record.assignment_id,
                name=final_record.name,
                weight=final_record.weight,
                max_score=final_record.max_score,
                description=final_record.description,
                created_at=str(final_record.created_at),
                updated_at=str(final_record.updated_at),
            )
            
        except Exception as e:
            conn.rollback()
            # Handle specific integrity errors if they slip through pre-checks (race conditions etc)
            if "UNIQUE constraint failed" in str(e):
                raise HTTPException(status_code=409, detail="A rubric criterion with this name already exists for the assignment")
            raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
