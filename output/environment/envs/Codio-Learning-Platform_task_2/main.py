import os
from datetime import datetime, timezone
from typing import List

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class AssignmentItem(BaseModel):
    id: int
    title: str
    assignment_type: str


class ModuleResponse(BaseModel):
    module_id: int
    module_title: str
    release_at: str
    assignments: List[AssignmentItem]


class CreateModuleRequest(BaseModel):
    title: str = Field(..., description="Title of the new module")
    release_at: str = Field(..., description="ISO 8601 formatted datetime when the module should be released (UTC)")
    assignments: List[str] = Field(..., description="List of auto-graded assignment titles to be attached to the module")


def parse_iso_datetime(dt_string: str) -> datetime:
    """Parse ISO 8601 string handling both 'T...Z' and space-separated formats."""
    # Replace Z with +00:00 for compatibility if needed, though fromisoformat handles it in newer Python versions
    # However, standard practice for robust parsing often involves replacing 'Z' explicitly or using dateutil.
    # Since we are limited to stdlib/FastAPI/Pydantic/SQLAlchemy, we rely on fromisoformat.
    # Note: Python < 3.11 might struggle with 'Z'. We'll normalize it.
    dt_string = dt_string.replace('Z', '+00:00')
    
    try:
        return datetime.fromisoformat(dt_string)
    except ValueError:
        # Try common alternative format: YYYY-MM-DD HH:MM:SS
        try:
            return datetime.strptime(dt_string, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid datetime format: {dt_string}")


@app.post("/courses/{course_id}/modules", response_model=ModuleResponse, operation_id="create_module_with_assignments")
def create_module_with_assignments(
    course_id: str = Path(..., description="Unique identifier of the course to which the module will be added"),
    body: CreateModuleRequest = ...
):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    parsed_release_at = parse_iso_datetime(body.release_at)
    # Ensure UTC awareness for storage consistency if required, but SQLite stores naive datetimes usually.
    # The schema uses DATETIME. We will store as string matching the input format normalized or just pass through.
    # Let's convert back to a standardized string for DB insertion to avoid ambiguity.
    # Using isoformat without microseconds if they weren't provided? 
    # Better to keep the original string representation if possible, but ensuring validity.
    # For simplicity and safety with SQLAlchemy/SQLite, we can insert the datetime object directly 
    # if the column type supports it, or format it. The DDL says DATETIME.
    # Let's format it as "YYYY-MM-DD HH:MM:SS" which is standard for SQLite DATETIME columns.
    db_release_at_str = parsed_release_at.strftime("%Y-%m-%d %H:%M:%S")

    with engine.connect() as conn:
        # Check if course exists
        result = conn.execute(text("SELECT id FROM courses WHERE id = :cid"), {"cid": course_id})
        if result.fetchone() is None:
            raise HTTPException(status_code=404, detail=f"Course '{course_id}' not found")

        # Determine next position for the module in this course
        result = conn.execute(
            text("SELECT COALESCE(MAX(position), 0) + 1 AS next_pos FROM modules WHERE course_id = :cid"),
            {"cid": course_id}
        )
        row = result.fetchone()
        next_position = row.next_pos if row else 1

        # Insert new module
        now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        insert_module_stmt = text("""
            INSERT INTO modules (course_id, title, release_at, position, created_at, updated_at)
            VALUES (:course_id, :title, :release_at, :position, :created_at, :updated_at)
        """)
        
        try:
            res = conn.execute(insert_module_stmt, {
                "course_id": course_id,
                "title": body.title,
                "release_at": db_release_at_str,
                "position": next_position,
                "created_at": now_ts,
                "updated_at": now_ts
            })
            module_id = res.lastrowid
        except Exception as e:
            # Handle unique constraint violation on (course_id, title)
            if "UNIQUE constraint failed" in str(e):
                raise HTTPException(status_code=409, detail=f"Module with title '{body.title}' already exists in course '{course_id}'")
            raise HTTPException(status_code=500, detail=str(e))

        inserted_assignments = []
        
        # Insert assignments
        if body.assignments:
            insert_assignment_stmt = text("""
                INSERT INTO assignments (module_id, title, assignment_type, position, created_at, updated_at)
                VALUES (:module_id, :title, :assignment_type, :position, :created_at, :updated_at)
            """)
            
            for idx, assign_title in enumerate(body.assignments, start=1):
                try:
                    res = conn.execute(insert_assignment_stmt, {
                        "module_id": module_id,
                        "title": assign_title,
                        "assignment_type": "auto_graded",
                        "position": idx,
                        "created_at": now_ts,
                        "updated_at": now_ts
                    })
                    assignment_id = res.lastrowid
                    inserted_assignments.append({
                        "id": assignment_id,
                        "title": assign_title,
                        "assignment_type": "auto_graded"
                    })
                except Exception as e:
                     # Rollback logic would ideally happen here if strict atomicity was required across multiple inserts
                     # But since we are in a single connection context manager, exceptions bubble up.
                     # If one fails, the whole transaction rolls back upon exit if not committed manually.
                     # Wait, `conn` does not auto-commit unless configured. In SQLAlchemy Core, execute() doesn't commit.
                     # We need to manage transactions properly.
                     raise HTTPException(status_code=500, detail=f"Failed to insert assignment: {str(e)}")

        # Commit the transaction
        conn.commit()

        # Construct response
        # Format release_at back to ISO 8601 for the API response
        # The input was ISO 8601. We stored as "YYYY-MM-DD HH:MM:SS".
        # To return consistent ISO 8601, we re-parse what we stored or use the original parsed object.
        final_release_at_iso = parsed_release_at.isoformat().replace('+00:00', 'Z')

        return ModuleResponse(
            module_id=module_id,
            module_title=body.title,
            release_at=final_release_at_iso,
            assignments=[AssignmentItem(**a) for a in inserted_assignments]
        )
