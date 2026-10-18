import os
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


@app.delete("/cohorts/{cohort_id}/members/{user_id}", operation_id="remove_student_from_cohort")
def remove_student_from_cohort(cohort_id: str, user_id: str):
    """
    Removes the specified student (by user ID) from the specified cohort without unenrolling them from the course.
    The cohort and user must exist, and the user must be a member of the cohort.
    """
    with engine.connect() as conn:
        # Check if the cohort exists
        result = conn.execute(
            text("SELECT id FROM cohorts WHERE id = :cohort_id"),
            {"cohort_id": cohort_id}
        )
        if result.fetchone() is None:
            raise HTTPException(status_code=404, detail=f"Cohort '{cohort_id}' not found.")

        # Check if the user exists
        result = conn.execute(
            text("SELECT id FROM users WHERE id = :user_id"),
            {"user_id": user_id}
        )
        if result.fetchone() is None:
            raise HTTPException(status_code=404, detail=f"User '{user_id}' not found.")

        # Check if the user is a member of the cohort
        result = conn.execute(
            text("SELECT id FROM cohort_memberships WHERE cohort_id = :cohort_id AND user_id = :user_id"),
            {"cohort_id": cohort_id, "user_id": user_id}
        )
        membership = result.fetchone()
        if membership is None:
            raise HTTPException(status_code=404, detail=f"User '{user_id}' is not a member of cohort '{cohort_id}'.")

        # Remove the membership
        conn.execute(
            text("DELETE FROM cohort_memberships WHERE cohort_id = :cohort_id AND user_id = :user_id"),
            {"cohort_id": cohort_id, "user_id": user_id}
        )
        conn.commit()

    return {"message": f"Student {user_id} removed from cohort {cohort_id}."}
