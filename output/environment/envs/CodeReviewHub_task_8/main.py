import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)

app = FastAPI()

class GrantInstructorRoleRequest(BaseModel):
    user_email: str
    course_code: str

class GrantInstructorRoleResponse(BaseModel):
    success: bool
    message: str

@app.post("/course-roles", response_model=GrantInstructorRoleResponse, operation_id="grant_instructor_role")
def grant_instructor_role(request: GrantInstructorRoleRequest):
    """
    Assign the instructor role to a user for the specified course.
    This allows the user to manage assignments and review student submissions.
    The granted_by field is set to the current user (id from env).
    """
    
    # Get current user ID from environment variable as per instructions
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Current user not authenticated")
    
    try:
        granted_by_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=500, detail="Invalid current user ID format")

    with engine.connect() as conn:
        # 1. Find user_id based on email
        stmt_user = text("SELECT id FROM users WHERE email = :email")
        res_user = conn.execute(stmt_user, {"email": request.user_email})
        user_row = res_user.fetchone()
        
        if not user_row:
            return {
                "success": False,
                "message": f"User with email '{request.user_email}' not found."
            }
        user_id = user_row[0]

        # 2. Find course_id based on code
        stmt_course = text("SELECT id FROM courses WHERE code = :code")
        res_course = conn.execute(stmt_course, {"code": request.course_code})
        course_row = res_course.fetchone()
        
        if not course_row:
            return {
                "success": False,
                "message": f"Course with code '{request.course_code}' not found."
            }
        course_id = course_row[0]

        # 3. Find role_id for 'instructor'
        stmt_role = text("SELECT id FROM roles WHERE name = :name")
        res_role = conn.execute(stmt_role, {"name": "instructor"})
        role_row = res_role.fetchone()
        
        if not role_row:
            return {
                "success": False,
                "message": "Role 'instructor' not found in system."
            }
        role_id = role_row[0]

        # 4. Check if the assignment already exists due to UNIQUE constraint (user_id, course_id, role_id)
        stmt_check_exists = text("""
            SELECT COUNT(*) FROM course_roles 
            WHERE user_id = :uid AND course_id = :cid AND role_id = :rid
        """)
        res_exists = conn.execute(stmt_check_exists, {"uid": user_id, "cid": course_id, "rid": role_id})
        count = res_exists.scalar()
        
        if count > 0:
            return {
                "success": True,
                "message": f"Instructor role for user '{request.user_email}' in course '{request.course_code}' already exists."
            }

        # 5. Insert new record into course_roles
        stmt_insert = text("""
            INSERT INTO course_roles (user_id, course_id, role_id, granted_by)
            VALUES (:uid, :cid, :rid, :gby)
        """)
        conn.execute(stmt_insert, {
            "uid": user_id,
            "cid": course_id,
            "rid": role_id,
            "gby": granted_by_id
        })
        conn.commit()

        return {
            "success": True,
            "message": f"Successfully granted instructor role to user '{request.user_email}' for course '{request.course_code}'."
        }
