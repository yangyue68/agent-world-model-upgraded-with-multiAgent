import os
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_URL = os.environ.get("DATABASE_PATH")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

app = FastAPI()


class FileUpdate(BaseModel):
    filename: str
    content: str


class ResubmitRequest(BaseModel):
    file_updates: List[FileUpdate]


class ResubmitResponse(BaseModel):
    new_submission_id: str
    status: str
    attempt_number: int


class TestResultItem(BaseModel):
    test_case_name: str
    status: str
    output: Optional[str] = None
    duration_ms: Optional[int] = None


@app.post(
    "/courses/{course_code}/assignments/{assignment_title}/submissions/{submission_id}/resubmit",
    response_model=ResubmitResponse,
    operation_id="resubmit_assignment"
)
def resubmit_assignment(
    course_code: str = Path(...),
    assignment_title: str = Path(...),
    submission_id: str = Path(...),
    payload: ResubmitRequest = Body(...)
):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")

    db = SessionLocal()
    try:
        # 1. Verify Course exists and get ID
        stmt_course = text("""
            SELECT id FROM courses WHERE code = :code
        """)
        res_course = db.execute(stmt_course, {"code": course_code}).fetchone()
        if not res_course:
            raise HTTPException(status_code=404, detail="Course not found")
        course_id = res_course[0]

        # 2. Verify Assignment exists in this course and get ID
        stmt_assign = text("""
            SELECT id FROM assignments 
            WHERE course_id = :course_id AND title = :title
        """)
        res_assign = db.execute(stmt_assign, {"course_id": course_id, "title": assignment_title}).fetchone()
        if not res_assign:
            raise HTTPException(status_code=404, detail="Assignment not found")
        assignment_id = res_assign[0]

        # 3. Verify Base Submission exists, belongs to this assignment, and is owned by current user
        stmt_base_sub = text("""
            SELECT id, user_id, attempt_number, base_submission_id 
            FROM submissions 
            WHERE id = :sub_id AND assignment_id = :assign_id
        """)
        res_base_sub = db.execute(stmt_base_sub, {"sub_id": submission_id, "assign_id": assignment_id}).fetchone()
        if not res_base_sub:
            raise HTTPException(status_code=404, detail="Base submission not found or does not belong to this assignment")
        
        base_user_id = res_base_sub[1]
        if base_user_id != current_user_id:
            raise HTTPException(status_code=403, detail="Cannot resubmit another user's submission")

        prev_attempt_number = res_base_sub[2]
        new_attempt_number = prev_attempt_number + 1
        
        # Check uniqueness constraint implicitly via logic or let DB fail? 
        # The schema has UNIQUE (assignment_id, user_id, attempt_number).
        # We assume sequential attempts. If a higher attempt exists, we might need to find max.
        # Let's find the max attempt number for this user/assignment to be safe.
        stmt_max_attempt = text("""
            SELECT MAX(attempt_number) FROM submissions 
            WHERE assignment_id = :assign_id AND user_id = :user_id
        """)
        res_max = db.execute(stmt_max_attempt, {"assign_id": assignment_id, "user_id": current_user_id}).scalar()
        if res_max is not None:
            new_attempt_number = res_max + 1

        # 4. Create New Submission
        new_submission_id = f"sub_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        stmt_insert_sub = text("""
            INSERT INTO submissions (id, assignment_id, user_id, base_submission_id, status, attempt_number, submitted_at, created_at, updated_at)
            VALUES (:id, :assign_id, :user_id, :base_id, 'submitted', :attempt, :now, :now, :now)
        """)
        db.execute(stmt_insert_sub, {
            "id": new_submission_id,
            "assign_id": assignment_id,
            "user_id": current_user_id,
            "base_id": submission_id,
            "attempt": new_attempt_number,
            "now": now
        })

        # 5. Handle Files
        # First, copy all files from base submission to new submission
        stmt_copy_files = text("""
            INSERT INTO submission_files (submission_id, filename, content, content_text, size_bytes, mime_type, created_at, updated_at)
            SELECT :new_sub_id, filename, content, content_text, size_bytes, mime_type, :now, :now
            FROM submission_files
            WHERE submission_id = :old_sub_id
        """)
        db.execute(stmt_copy_files, {
            "new_sub_id": new_submission_id,
            "old_sub_id": submission_id,
            "now": now
        })

        # Then, apply updates from payload
        for update in payload.file_updates:
            filename = update.filename
            content = update.content
            
            # Check if file exists in new submission (it should, since we copied)
            # Update it
            stmt_update_file = text("""
                UPDATE submission_files
                SET content_text = :content, size_bytes = :size, updated_at = :now
                WHERE submission_id = :sub_id AND filename = :filename
            """)
            
            # Calculate size in bytes
            size_bytes = len(content.encode('utf-8'))
            
            result = db.execute(stmt_update_file, {
                "content": content,
                "size": size_bytes,
                "now": now,
                "sub_id": new_submission_id,
                "filename": filename
            })
            
            if result.rowcount == 0:
                # If it didn't exist (maybe wasn't in base?), insert it
                # However, spec says "updating the specified file". Usually implies existing.
                # But to be robust, if missing, we could insert. 
                # Given the context of "diff based on previous", usually files match.
                # Let's assume if rowcount 0, it's an error or we insert. 
                # Standard behavior for such APIs often requires the file to be part of the assignment definition or previous sub.
                # For simplicity in this task, if it doesn't exist after copy, we insert.
                
                stmt_insert_file = text("""
                    INSERT INTO submission_files (submission_id, filename, content_text, size_bytes, mime_type, created_at, updated_at)
                    VALUES (:sub_id, :filename, :content, :size, :mime, :now, :now)
                """)
                # Guess mime type based on extension
                mime_type = "text/plain"
                if filename.endswith(".js"):
                    mime_type = "text/javascript"
                elif filename.endswith(".json"):
                    mime_type = "application/json"
                    
                db.execute(stmt_insert_file, {
                    "sub_id": new_submission_id,
                    "filename": filename,
                    "content": content,
                    "size": size_bytes,
                    "mime": mime_type,
                    "now": now
                })

        db.commit()

        return ResubmitResponse(
            new_submission_id=new_submission_id,
            status="submitted",
            attempt_number=new_attempt_number
        )

    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


@app.get(
    "/submissions/{submission_id}/test_results",
    response_model=List[TestResultItem],
    operation_id="get_submission_test_results"
)
def get_submission_test_results(
    submission_id: str = Path(...)
):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")

    db = SessionLocal()
    try:
        # 1. Verify submission exists and belongs to current user
        stmt_sub = text("""
            SELECT s.id, s.assignment_id 
            FROM submissions s
            WHERE s.id = :sub_id AND s.user_id = :user_id
        """)
        res_sub = db.execute(stmt_sub, {"sub_id": submission_id, "user_id": current_user_id}).fetchone()
        if not res_sub:
            raise HTTPException(status_code=404, detail="Submission not found or access denied")
        
        assignment_id = res_sub[1]

        # 2. Find the latest completed test run for this submission
        # Or just any test run associated with this submission? 
        # Spec says "fetch the updated test suite output". 
        # Usually, there's one active/recent run per submission version.
        stmt_run = text("""
            SELECT tr.id 
            FROM test_runs tr
            WHERE tr.submission_id = :sub_id
            ORDER BY tr.created_at DESC
            LIMIT 1
        """)
        res_run = db.execute(stmt_run, {"sub_id": submission_id}).fetchone()
        
        if not res_run:
            # No tests run yet? Return empty list or specific message?
            # The API returns array of items. Empty array is valid.
            return []

        test_run_id = res_run[0]

        # 3. Fetch results joined with test case names
        stmt_results = text("""
            SELECT tc.name, tr.status, tr.output, tr.duration_ms
            FROM test_results tr
            JOIN test_cases tc ON tr.test_case_id = tc.id
            WHERE tr.test_run_id = :run_id
            ORDER BY tc.id ASC
        """)
        res_results = db.execute(stmt_results, {"run_id": test_run_id}).fetchall()

        results_list = []
        for row in res_results:
            results_list.append(TestResultItem(
                test_case_name=row[0],
                status=row[1],
                output=row[2],
                duration_ms=row[3]
            ))
        
        return results_list

    except Exception as e:
        raise e
    finally:
        db.close()
