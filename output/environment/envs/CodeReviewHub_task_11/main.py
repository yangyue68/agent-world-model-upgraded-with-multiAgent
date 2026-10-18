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


class RemovePlagiarismFlagRequest(BaseModel):
    resolution_notes: str


@app.put("/submissions/{submission_id}/plagiarism-flag", operation_id="remove_plagiarism_flag")
def remove_plagiarism_flag(submission_id: int, body: RemovePlagiarismFlagRequest):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Current user not authenticated")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid current user ID")

    with engine.begin() as conn:
        # 1. Verify submission exists and get student_id and assignment_id
        sub_row = conn.execute(
            text("SELECT id, student_id, assignment_id FROM submissions WHERE id = :sid"),
            {"sid": submission_id}
        ).fetchone()

        if not sub_row:
            raise HTTPException(status_code=404, detail="Submission not found")

        student_id = sub_row.student_id
        assignment_id = sub_row.assignment_id

        # 2. Get course_id from assignment
        assign_row = conn.execute(
            text("SELECT course_id FROM assignments WHERE id = :aid"),
            {"aid": assignment_id}
        ).fetchone()

        if not assign_row:
            raise HTTPException(status_code=404, detail="Assignment not found")

        course_id = assign_row.course_id

        # 3. Update plagiarism_flags table for this submission
        # Find active flags for this submission to resolve them as false_positive
        flag_rows = conn.execute(
            text("""
                SELECT id FROM plagiarism_flags 
                WHERE submission_id = :sid AND flag_status = 'active'
            """),
            {"sid": submission_id}
        ).fetchall()

        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        for flag in flag_rows:
            conn.execute(
                text("""
                    UPDATE plagiarism_flags 
                    SET flag_status = 'false_positive',
                        resolved_by = :uid,
                        resolved_at = :now,
                        resolution_notes = :notes,
                        updated_at = :now
                    WHERE id = :fid
                """),
                {
                    "uid": current_user_id,
                    "now": now_utc,
                    "notes": body.resolution_notes,
                    "fid": flag.id
                }
            )

        # 4. Update submissions table to clear the plagiarism flag indicator
        conn.execute(
            text("""
                UPDATE submissions 
                SET plagiarism_flag = 0,
                    plagiarism_notes = :notes,
                    updated_at = :now
                WHERE id = :sid
            """),
            {
                "notes": f"False positive confirmed by manual review. Notes: {body.resolution_notes}",
                "now": now_utc,
                "sid": submission_id
            }
        )

        # 5. Restore student access in submission_access table
        # Check if there is an existing restriction record for this student/course
        access_row = conn.execute(
            text("""
                SELECT id, can_submit FROM submission_access 
                WHERE student_id = :stid AND course_id = :cid
            """),
            {"stid": student_id, "cid": course_id}
        ).fetchone()

        if access_row:
            if access_row.can_submit == 0:
                conn.execute(
                    text("""
                        UPDATE submission_access 
                        SET can_submit = 1,
                            reason = NULL,
                            restored_at = :now,
                            updated_at = :now
                        WHERE id = :aid
                    """),
                    {
                        "now": now_utc,
                        "aid": access_row.id
                    }
                )
        else:
            # If no record exists, ensure they have permission (insert default allowed state)
            # Although typically restrictions are added when flagged, restoring might imply ensuring a row exists or just updating.
            # Given the schema has UNIQUE(student_id, course_id), we should probably only update if it exists.
            # However, if the system relies on presence of row for restriction, absence means allowed.
            # The task says "restore the student's access". If there was no row, they had access.
            # We will assume if there was a restriction, there is a row. If no row, nothing to do for access restoration specifically via this table update logic above.
            pass

        # 6. Insert audit log entry
        conn.execute(
            text("""
                INSERT INTO audit_log (user_id, action, entity_type, entity_id, details, created_at)
                VALUES (:uid, :action, :etype, :eid, :details, :now)
            """),
            {
                "uid": current_user_id,
                "action": "resolve_plagiarism_false_positive",
                "etype": "submission",
                "eid": submission_id,
                "details": f"Removed plagiarism flag for submission {submission_id}. Student {student_id} access restored.",
                "now": now_utc
            }
        )

    return {
        "message": "Plagiarism flag removed and student access restored.",
        "submission_id": submission_id,
        "student_id": student_id,
        "course_id": course_id
    }
