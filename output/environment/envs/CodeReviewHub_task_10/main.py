import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)


class RevokeResponse(BaseModel):
    message: str
    revoked_at: str


app = FastAPI()


@app.put("/courses/{course_id}/reviewers/{user_id}/revoke", response_model=RevokeResponse, operation_id="revoke_reviewer_permission")
def revoke_reviewer_permission(course_id: int, user_id: int):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user context")

    with engine.begin() as conn:
        # Verify the reviewer assignment exists and is active
        result = conn.execute(
            text("""
                SELECT id FROM course_reviewers 
                WHERE course_id = :course_id AND user_id = :user_id AND revoked_at IS NULL
            """),
            {"course_id": course_id, "user_id": user_id}
        ).fetchone()

        if not result:
            raise HTTPException(status_code=404, detail="Active reviewer permission not found")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Update the record to set revoked_at and revoked_by
        conn.execute(
            text("""
                UPDATE course_reviewers 
                SET revoked_at = :now, revoked_by = :current_user_id, updated_at = :now
                WHERE course_id = :course_id AND user_id = :user_id AND revoked_at IS NULL
            """),
            {
                "now": now,
                "current_user_id": current_user_id,
                "course_id": course_id,
                "user_id": user_id
            }
        )

    return RevokeResponse(
        message=f"Reviewer permission for user {user_id} in course {course_id} has been revoked.",
        revoked_at=now
    )
