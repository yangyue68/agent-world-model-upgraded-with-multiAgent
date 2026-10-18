import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from typing import Optional

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class SubmissionResponse(BaseModel):
    submission_id: int
    conflict_of_interest_flagged: bool
    conflict_details: Optional[str] = None


class SubmissionDetails(BaseModel):
    id: int
    assignment_id: int
    user_id: int
    repository_url: str
    branch: str
    status: str
    conflict_of_interest: bool
    conflict_details: Optional[str] = None
    created_at: str
    updated_at: str


def get_current_user_id() -> int:
    """Retrieve the current user ID from the environment."""
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")


@app.post("/submissions", response_model=SubmissionResponse, operation_id="submit_code_review")
def submit_code_review(
    assignment_title: str = Query(..., description="Title of the assignment to submit code review for"),
    course_code: str = Query(..., description="Course code where the assignment belongs"),
    repository_url: str = Query(..., description="URL of the Git repository containing the code"),
    branch: str = Query(default="main", description="Branch name in the repository (defaults to 'main')")
):
    # Get current user ID
    user_id = get_current_user_id()

    with engine.connect() as conn:
        # Find the course ID based on course_code
        result = conn.execute(
            text("SELECT id FROM courses WHERE code = :code"),
            {"code": course_code}
        )
        course_row = result.fetchone()
        if not course_row:
            raise HTTPException(status_code=404, detail=f"Course '{course_code}' not found")
        course_id = course_row[0]

        # Find the assignment ID based on course_id and title
        result = conn.execute(
            text("SELECT id FROM assignments WHERE course_id = :course_id AND title = :title"),
            {"course_id": course_id, "title": assignment_title}
        )
        assignment_row = result.fetchone()
        if not assignment_row:
            raise HTTPException(
                status_code=404,
                detail=f"Assignment '{assignment_title}' not found in course '{course_code}'"
            )
        assignment_id = assignment_row[0]

        # Check for conflicts of interest
        # A conflict exists if there are other submissions for the same assignment 
        # by users who might be reviewers. For simplicity, we check if any existing 
        # submission for this assignment has a conflict flag or if the current user 
        # has previously submitted for this assignment (self-review conflict).
        
        # Check if the user already has a submission for this assignment
        result = conn.execute(
            text("""
                SELECT COUNT(*) FROM submissions 
                WHERE assignment_id = :assignment_id AND user_id = :user_id
            """),
            {"assignment_id": assignment_id, "user_id": user_id}
        )
        count_row = result.fetchone()
        existing_submissions_count = count_row[0] if count_row else 0
        
        # Determine conflict of interest
        # In this scenario, we consider a conflict if:
        # 1. The user has already submitted for this assignment (potential duplicate/self-conflict)
        # 2. Or based on some predefined rule. Since the spec says "checks for conflicts of interest 
        #    based on reviewer-user relationships", and we don't have a separate reviewers table,
        #    we'll simulate a basic check: if there are other submissions for this assignment,
        #    we might flag a potential conflict depending on business logic.
        #    
        # Looking at initial data: submission 2 has conflict_of_interest=1 with details 
        # "Reviewer is a co-author on the repository." This suggests external checks.
        # Without more complex relationship tables, we'll assume no automatic conflict 
        # unless specified otherwise. However, to make it meaningful, let's say if the 
        # repository URL matches an existing submission's URL for the same assignment, 
        # that could indicate a shared repo/conflict.
        
        conflict_of_interest = False
        conflict_details = None

        # Simple heuristic: Check if another submission for this assignment uses the same repo URL
        result = conn.execute(
            text("""
                SELECT conflict_details FROM submissions 
                WHERE assignment_id = :assignment_id 
                  AND repository_url = :repo_url 
                  AND user_id != :user_id
                LIMIT 1
            """),
            {
                "assignment_id": assignment_id,
                "repo_url": repository_url,
                "user_id": user_id
            }
        )
        conflicting_submission = result.fetchone()
        if conflicting_submission:
            conflict_of_interest = True
            conflict_details = f"Repository URL overlaps with another submission for this assignment."

        # Insert the new submission
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        result = conn.execute(
            text("""
                INSERT INTO submissions (
                    assignment_id, user_id, repository_url, branch, 
                    status, conflict_of_interest, conflict_details, 
                    created_at, updated_at
                ) VALUES (
                    :assignment_id, :user_id, :repository_url, :branch,
                    'pending', :conflict_of_interest, :conflict_details,
                    :now, :now
                )
            """),
            {
                "assignment_id": assignment_id,
                "user_id": user_id,
                "repository_url": repository_url,
                "branch": branch,
                "conflict_of_interest": 1 if conflict_of_interest else 0,
                "conflict_details": conflict_details,
                "now": now
            }
        )
        submission_id = result.lastrowid
        conn.commit()

    return SubmissionResponse(
        submission_id=submission_id,
        conflict_of_interest_flagged=conflict_of_interest,
        conflict_details=conflict_details
    )


@app.get("/submissions/{submission_id}", response_model=SubmissionDetails, operation_id="get_submission_details")
def get_submission_details(submission_id: int):
    # Verify user is authenticated
    _ = get_current_user_id()

    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT 
                    id, assignment_id, user_id, repository_url, branch,
                    status, conflict_of_interest, conflict_details,
                    created_at, updated_at
                FROM submissions
                WHERE id = :submission_id
            """),
            {"submission_id": submission_id}
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Submission not found")

        # Convert boolean-like integer to actual boolean
        conflict_bool = bool(row[6])
        
        # Format datetimes as strings
        created_at_str = row[8].strftime("%Y-%m-%dT%H:%M:%SZ") if hasattr(row[8], 'strftime') else str(row[8])
        updated_at_str = row[9].strftime("%Y-%m-%dT%H:%M:%SZ") if hasattr(row[9], 'strftime') else str(row[9])

        return SubmissionDetails(
            id=row[0],
            assignment_id=row[1],
            user_id=row[2],
            repository_url=row[3],
            branch=row[4],
            status=row[5],
            conflict_of_interest=conflict_bool,
            conflict_details=row[7],
            created_at=created_at_str,
            updated_at=updated_at_str
        )
