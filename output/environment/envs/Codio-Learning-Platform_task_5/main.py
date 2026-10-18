import os
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

app = FastAPI(title="Codio Learning Platform API")


# --- Pydantic Models ---

class AssignmentResponse(BaseModel):
    id: int
    course_id: int
    title: str
    description: Optional[str]
    deadline: str
    allow_retries: bool
    max_retries: Optional[int]
    created_by: int
    created_at: str
    updated_at: str


class UpdateAssignmentRequest(BaseModel):
    deadline: str
    allow_retries: bool
    max_retries: Optional[int] = None


class TestCaseResponse(BaseModel):
    id: int
    assignment_id: int
    name: str
    description: Optional[str]
    created_at: str
    updated_at: str


class CreateTestCaseFeedbackRequest(BaseModel):
    test_case_id: int
    feedback_message: str


class TestCaseFeedbackResponse(BaseModel):
    id: int
    test_case_id: int
    feedback_message: str
    created_by: int
    created_at: str
    updated_at: str


# --- Helper Functions ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def parse_datetime(dt_str: str) -> datetime:
    """Parse ISO-8601 datetime strings, handling 'Z' suffix and space separators."""
    if dt_str.endswith('Z'):
        dt_str = dt_str[:-1] + '+00:00'
    
    # Try parsing with fromisoformat (Python 3.7+)
    try:
        return datetime.fromisoformat(dt_str)
    except ValueError:
        pass
    
    # Fallback for formats like "YYYY-MM-DD HH:MM:SS"
    try:
        return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        pass
        
    # If still fails, try common variations or raise error
    raise ValueError(f"Could not parse datetime string: {dt_str}")


def format_datetime(dt: datetime) -> str:
    """Format datetime to ISO-8601 string with Z suffix for UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# --- Endpoints ---

@app.get("/assignments", response_model=List[AssignmentResponse], operation_id="find_assignments")
def find_assignments(
    course_code: str = Query(..., description="Course code like 'CS101'"),
    title: str = Query(..., description="Assignment title like 'Debugging Loops'")
):
    db = next(get_db())
    try:
        query = text("""
            SELECT 
                a.id, 
                a.course_id, 
                a.title, 
                a.description, 
                a.deadline, 
                a.allow_retries, 
                a.max_retries, 
                a.created_by, 
                a.created_at, 
                a.updated_at
            FROM assignments a
            JOIN courses c ON a.course_id = c.id
            WHERE c.code = :course_code AND a.title = :title
        """)
        
        result = db.execute(query, {"course_code": course_code, "title": title})
        rows = result.fetchall()
        
        assignments = []
        for row in rows:
            assignments.append(AssignmentResponse(
                id=row.id,
                course_id=row.course_id,
                title=row.title,
                description=row.description,
                deadline=str(row.deadline), # Keep raw DB format or normalize? Spec says ISO. Let's assume DB stores standard string.
                allow_retries=bool(row.allow_retries),
                max_retries=row.max_retries,
                created_by=row.created_by,
                created_at=str(row.created_at),
                updated_at=str(row.updated_at)
            ))
            
        return assignments
    finally:
        db.close()


@app.put("/assignments/{assignment_id}", response_model=AssignmentResponse, operation_id="update_assignment")
def update_assignment(
    assignment_id: int,
    payload: UpdateAssignmentRequest
):
    db = next(get_db())
    try:
        # Get current user ID from env
        current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
        if not current_user_id_str:
             # In a real app this might be an auth error, but spec implies it's always present for writes
             raise HTTPException(status_code=401, detail="User context missing")
        current_user_id = int(current_user_id_str)

        # Fetch existing assignment to ensure it exists
        check_query = text("SELECT id FROM assignments WHERE id = :id")
        res = db.execute(check_query, {"id": assignment_id})
        if not res.fetchone():
            raise HTTPException(status_code=404, detail="Assignment not found")

        # Parse new deadline
        try:
            new_deadline_dt = parse_datetime(payload.deadline)
            new_deadline_str = format_datetime(new_deadline_dt)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid deadline format: {str(e)}")

        # Prepare update parameters
        params = {
            "id": assignment_id,
            "deadline": new_deadline_str,
            "allow_retries": 1 if payload.allow_retries else 0,
            "max_retries": payload.max_retries,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        }

        # Execute update
        update_query = text("""
            UPDATE assignments
            SET deadline = :deadline,
                allow_retries = :allow_retries,
                max_retries = :max_retries,
                updated_at = :updated_at
            WHERE id = :id
        """)
        
        db.execute(update_query, params)
        db.commit()

        # Fetch updated record
        fetch_query = text("""
            SELECT 
                id, 
                course_id, 
                title, 
                description, 
                deadline, 
                allow_retries, 
                max_retries, 
                created_by, 
                created_at, 
                updated_at
            FROM assignments
            WHERE id = :id
        """)
        
        result = db.execute(fetch_query, {"id": assignment_id})
        row = result.fetchone()
        
        if not row:
            raise HTTPException(status_code=500, detail="Failed to retrieve updated assignment")

        return AssignmentResponse(
            id=row.id,
            course_id=row.course_id,
            title=row.title,
            description=row.description,
            deadline=str(row.deadline),
            allow_retries=bool(row.allow_retries),
            max_retries=row.max_retries,
            created_by=row.created_by,
            created_at=str(row.created_at),
            updated_at=str(row.updated_at)
        )
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.get("/test_cases", response_model=List[TestCaseResponse], operation_id="find_test_cases")
def find_test_cases(
    assignment_id: int = Query(..., description="Internal ID of the assignment containing the test case"),
    name: str = Query(..., description="Name of the test case like 'test_off_by_one'")
):
    db = next(get_db())
    try:
        query = text("""
            SELECT 
                id, 
                assignment_id, 
                name, 
                description, 
                created_at, 
                updated_at
            FROM test_cases
            WHERE assignment_id = :assignment_id AND name = :name
        """)
        
        result = db.execute(query, {"assignment_id": assignment_id, "name": name})
        rows = result.fetchall()
        
        test_cases = []
        for row in rows:
            test_cases.append(TestCaseResponse(
                id=row.id,
                assignment_id=row.assignment_id,
                name=row.name,
                description=row.description,
                created_at=str(row.created_at),
                updated_at=str(row.updated_at)
            ))
            
        return test_cases
    finally:
        db.close()


@app.post("/test_case_feedback", response_model=TestCaseFeedbackResponse, operation_id="create_test_case_feedback")
def create_test_case_feedback(
    payload: CreateTestCaseFeedbackRequest
):
    db = next(get_db())
    try:
        current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
        if not current_user_id_str:
            raise HTTPException(status_code=401, detail="User context missing")
        current_user_id = int(current_user_id_str)

        # Verify test case exists
        tc_check = text("SELECT id FROM test_cases WHERE id = :id")
        res = db.execute(tc_check, {"id": payload.test_case_id})
        if not res.fetchone():
            raise HTTPException(status_code=404, detail="Test case not found")

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Check if feedback already exists for this test_case_id (UNIQUE constraint)
        existing_fb = text("SELECT id FROM test_case_feedback WHERE test_case_id = :tc_id")
        fb_res = db.execute(existing_fb, {"tc_id": payload.test_case_id})
        existing_row = fb_res.fetchone()

        if existing_row:
            # Update existing
            update_q = text("""
                UPDATE test_case_feedback
                SET feedback_message = :msg,
                    updated_at = :now
                WHERE test_case_id = :tc_id
            """)
            db.execute(update_q, {"msg": payload.feedback_message, "now": now_str, "tc_id": payload.test_case_id})
            db.commit()
            
            # Fetch updated
            fetch_q = text("""
                SELECT id, test_case_id, feedback_message, created_by, created_at, updated_at
                FROM test_case_feedback
                WHERE test_case_id = :tc_id
            """)
            final_res = db.execute(fetch_q, {"tc_id": payload.test_case_id}).fetchone()
        else:
            # Insert new
            insert_q = text("""
                INSERT INTO test_case_feedback (test_case_id, feedback_message, created_by, created_at, updated_at)
                VALUES (:tc_id, :msg, :creator, :now, :now)
            """)
            db.execute(insert_q, {
                "tc_id": payload.test_case_id,
                "msg": payload.feedback_message,
                "creator": current_user_id,
                "now": now_str
            })
            db.commit()
            
            # Fetch inserted
            fetch_q = text("""
                SELECT id, test_case_id, feedback_message, created_by, created_at, updated_at
                FROM test_case_feedback
                WHERE test_case_id = :tc_id
            """)
            final_res = db.execute(fetch_q, {"tc_id": payload.test_case_id}).fetchone()

        if not final_res:
            raise HTTPException(status_code=500, detail="Failed to retrieve feedback record")

        return TestCaseFeedbackResponse(
            id=final_res.id,
            test_case_id=final_res.test_case_id,
            feedback_message=final_res.feedback_message,
            created_by=final_res.created_by,
            created_at=str(final_res.created_at),
            updated_at=str(final_res.updated_at)
        )

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
