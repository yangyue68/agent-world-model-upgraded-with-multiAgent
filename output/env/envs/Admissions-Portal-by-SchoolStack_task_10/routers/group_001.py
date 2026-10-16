from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import date
from typing import List, Dict

from database import get_db
from models import Application, User, Program, Intake, Rubric, ApplicationRubric, CommentThread, Comment
from schemas import ApplicationBulkAssignRequest, ApplicationBulkAssignResponse
from datetime import datetime

router = APIRouter(
    prefix="/api/applications",
    tags=["Applications"]
)

@router.post("/bulk_assign", operation_id="bulk_assign_applications")
async def bulk_assign_applications(
    payload: ApplicationBulkAssignRequest,
    db: Session = Depends(get_db)
):
    # Find the reviewer by email
    reviewer = db.query(User).filter(User.email == payload.reviewer_email).first()
    if not reviewer:
        raise HTTPException(status_code=404, detail="Reviewer not found")

    # Find the program
    program = db.query(Program).filter(Program.name == payload.program_name).first()
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    # Find the intake
    intake = db.query(Intake).filter(
        Intake.term == payload.intake_term,
        Intake.year == payload.intake_year,
        Intake.level == payload.intake_level
    ).first()
    if not intake:
        raise HTTPException(status_code=404, detail="Intake not found")

    # Find the rubric
    rubric = db.query(Rubric).filter(
        Rubric.name == payload.rubric_name,
        Rubric.version == payload.rubric_version
    ).first()
    if not rubric:
        raise HTTPException(status_code=404, detail="Rubric not found")

    # Find all unassigned applications in the specified program and intake with status 'submitted'
    applications = db.query(Application).filter(
        Application.program_id == program.id,
        Application.intake_id == intake.id,
        Application.status == "submitted",
        Application.assigned_reviewer_id.is_(None)
    ).all()

    assigned_count = 0
    failed_assignments = []

    for app in applications:
        try:
            # Update application
            app.assigned_reviewer_id = reviewer.id
            app.review_deadline = payload.review_deadline
            app.updated_at = datetime.utcnow()

            # Create ApplicationRubric entry
            app_rubric = ApplicationRubric(
                application_id=app.id,
                rubric_id=rubric.id,
                attached_by=reviewer.id
            )
            db.add(app_rubric)

            # Create CommentThread
            comment_thread = CommentThread(
                application_id=app.id,
                created_by=reviewer.id
            )
            db.add(comment_thread)
            db.flush()  # Ensure thread has an ID

            # Create Comment
            comment = Comment(
                thread_id=comment_thread.id,
                author_id=reviewer.id,
                body=payload.comment_note
            )
            db.add(comment)

            assigned_count += 1

        except Exception as e:
            failed_assignments.append({
                "application_id": app.id,
                "reason": str(e)
            })

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to commit changes: {str(e)}")

    return ApplicationBulkAssignResponse(
        assigned_count=assigned_count,
        failed_assignments=failed_assignments
    )