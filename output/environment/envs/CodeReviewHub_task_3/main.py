import os
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class DeadlineExtensionResponse(BaseModel):
    assignment_id: int
    new_deadline: str
    student_count_notified: int


@app.put(
    "/assignments/{assignment_id}/deadline",
    response_model=DeadlineExtensionResponse,
    operation_id="extend_assignment_deadline",
    summary="Extend the deadline for an assignment by 48 hours",
    description=(
        "Extend the submission deadline for a specific assignment by 48 hours. "
        "This endpoint requires instructor privileges for the course containing the assignment. "
        "The operation updates the assignment's deadline and creates notifications for all enrolled students."
    ),
    tags=["PrimaryResource"],
)
def extend_assignment_deadline(
    assignment_id: int,
    hours: int = Query(default=48, ge=1),
):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Missing authenticated user context")

    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authenticated user context")

    with engine.begin() as conn:
        # Verify assignment exists and get course info + current deadline
        row = conn.execute(
            text("""
                SELECT a.id, a.course_id, a.title, a.deadline, c.instructor_id
                FROM assignments a
                JOIN courses c ON c.id = a.course_id
                WHERE a.id = :assignment_id
            """),
            {"assignment_id": assignment_id},
        ).fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="Assignment not found")

        _, course_id, title, current_deadline, instructor_id = row

        # Enforce instructor privilege
        if instructor_id != current_user_id:
            raise HTTPException(status_code=403, detail="Only the course instructor can extend deadlines")

        # Parse current deadline (supports both 'YYYY-MM-DD HH:MM:SS' and ISO-8601 variants)
        if isinstance(current_deadline, str):
            normalized = current_deadline.strip().replace("T", " ").replace("Z", "")
            # Handle fractional seconds if present
            if "." in normalized:
                base, frac = normalized.split(".", 1)
                # Keep only up to microseconds precision safely
                frac_clean = "".join(ch for ch in frac if ch.isdigit())[:6]
                normalized = f"{base}.{frac_clean}" if frac_clean else base
            try:
                parsed_deadline = datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S.%f")
            except ValueError:
                parsed_deadline = datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")
        elif isinstance(current_deadline, datetime):
            parsed_deadline = current_deadline
        else:
            raise HTTPException(status_code=500, detail="Unexpected deadline type stored in database")

        new_deadline_dt = parsed_deadline + timedelta(hours=hours)
        new_deadline_str = new_deadline_dt.strftime("%Y-%m-%d %H:%M:%S")

        # Update assignment deadline
        conn.execute(
            text("""
                UPDATE assignments
                SET deadline = :new_deadline, updated_at = CURRENT_TIMESTAMP
                WHERE id = :assignment_id
            """),
            {"new_deadline": new_deadline_str, "assignment_id": assignment_id},
        )

        # Fetch all enrolled students for this course
        students_rows = conn.execute(
            text("""
                SELECT e.student_id
                FROM enrollments e
                WHERE e.course_id = :course_id
            """),
            {"course_id": course_id},
        ).fetchall()

        student_ids = [r[0] for r in students_rows]

        notification_title = "Assignment Deadline Extended"
        notification_message = (
            f"The deadline for '{title}' has been extended by {hours} hours. "
            f"New deadline: {new_deadline_str}."
        )

        notified_count = 0
        for sid in student_ids:
            conn.execute(
                text("""
                    INSERT INTO notifications (user_id, title, message, is_read, created_at, updated_at)
                    VALUES (:user_id, :title, :message, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """),
                {
                    "user_id": sid,
                    "title": notification_title,
                    "message": notification_message,
                },
            )
            notified_count += 1

        return DeadlineExtensionResponse(
            assignment_id=assignment_id,
            new_deadline=new_deadline_str,
            student_count_notified=notified_count,
        )
