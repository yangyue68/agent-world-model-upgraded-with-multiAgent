import os
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

# Read DATABASE_PATH at module import; strip sqlite:/// prefix for path if needed
DATABASE_URL = os.environ.get("DATABASE_PATH")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_PATH environment variable is required")

# Determine engine based on URL format
if DATABASE_URL.startswith("sqlite:///"):
    # For SQLite, we need to ensure foreign keys are enabled. The prompt says the shared runtime already does this,
    # but standard SQLAlchemy engines might need explicit configuration if not handled by a wrapper.
    # However, the prompt says "the shared runtime already enables PRAGMA foreign_keys=ON".
    # We will just create the engine with the provided URL.
    engine = create_engine(DATABASE_URL)
else:
    engine = create_engine(DATABASE_URL)

app = FastAPI()

# Pydantic Models for Request/Response

class FieldCreate(BaseModel):
    label: str
    field_type: str = Field(..., pattern="^(text|email|date|number|textarea|file|recommendation_request)$")
    is_required: bool = False
    position: int = 0
    placeholder: Optional[str] = None
    help_text: Optional[str] = None

class SectionCreate(BaseModel):
    title: str
    position: int = 0
    fields: List[FieldCreate] = []

class RequirementCreate(BaseModel):
    requirement_type: str = Field(..., pattern="^(document_upload|recommendation_request)$")
    title: str
    description: Optional[str] = None
    is_required: bool = True
    allowed_file_types: Optional[str] = None
    max_file_size_bytes: Optional[int] = None
    recommendation_count: Optional[int] = 1

class ApplicationFormCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    application_fee_cents: int
    deadline: Optional[str] = None
    sections: List[SectionCreate]
    requirements: Optional[List[RequirementCreate]] = None

class ApplicationFormCreateResponse(BaseModel):
    id: int
    name: str
    status: str
    created_at: str


@app.post("/application-forms", response_model=ApplicationFormCreateResponse, operation_id="create_application_form")
def create_application_form(request: ApplicationFormCreateRequest):
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id:
        # Fallback or error? The prompt says runtime_context defines it. 
        # If missing, we can't satisfy the FK constraint for created_by.
        raise HTTPException(status_code=500, detail="Current user ID not available in context")
    
    try:
        user_id = int(current_user_id)
    except ValueError:
        raise HTTPException(status_code=500, detail="Invalid Current User ID")

    # Verify user exists to avoid FK violation later (though DB will catch it, better to give clear error)
    with engine.connect() as conn:
        result = conn.execute(text("SELECT id FROM users WHERE id = :id"), {"id": user_id})
        if result.fetchone() is None:
            raise HTTPException(status_code=409, detail=f"User with ID {user_id} not found")

    # Prepare data for insertion
    deadline_dt = None
    if request.deadline:
        try:
            # Expecting ISO format YYYY-MM-DD HH:MM:SS
            deadline_dt = datetime.strptime(request.deadline, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid deadline format. Expected YYYY-MM-DD HH:MM:SS")

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    with engine.begin() as conn:
        # 1. Insert into application_forms
        stmt = text("""
            INSERT INTO application_forms (name, description, created_by, application_fee_cents, deadline, status, created_at, updated_at)
            VALUES (:name, :description, :created_by, :fee, :deadline, 'draft', :now, :now)
        """)
        res = conn.execute(stmt, {
            "name": request.name,
            "description": request.description,
            "created_by": user_id,
            "fee": request.application_fee_cents,
            "deadline": deadline_dt,
            "now": now_str
        })
        form_id = res.lastrowid
        
        # Fetch the created row to get exact timestamps/status from DB if needed, though we know them
        # Let's fetch to be safe and return correct data
        form_res = conn.execute(text("SELECT id, name, status, created_at FROM application_forms WHERE id = :id"), {"id": form_id})
        form_data = form_res.fetchone()
        
        if not form_data:
             raise HTTPException(status_code=500, detail="Failed to retrieve created form")

        # 2. Insert Sections and Fields
        for section in request.sections:
            sec_stmt = text("""
                INSERT INTO form_sections (form_id, title, position, created_at, updated_at)
                VALUES (:form_id, :title, :position, :now, :now)
            """)
            sec_res = conn.execute(sec_stmt, {
                "form_id": form_id,
                "title": section.title,
                "position": section.position,
                "now": now_str
            })
            section_id = sec_res.lastrowid

            for field in section.fields:
                fld_stmt = text("""
                    INSERT INTO form_fields (section_id, label, field_type, is_required, position, placeholder, help_text, created_at, updated_at)
                    VALUES (:section_id, :label, :field_type, :is_required, :position, :placeholder, :help_text, :now, :now)
                """)
                conn.execute(fld_stmt, {
                    "section_id": section_id,
                    "label": field.label,
                    "field_type": field.field_type,
                    "is_required": 1 if field.is_required else 0,
                    "position": field.position,
                    "placeholder": field.placeholder,
                    "help_text": field.help_text,
                    "now": now_str
                })

        # 3. Insert Requirements
        if request.requirements:
            for req in request.requirements:
                req_stmt = text("""
                    INSERT INTO form_requirements (form_id, requirement_type, title, description, is_required, allowed_file_types, max_file_size_bytes, recommendation_count, created_at, updated_at)
                    VALUES (:form_id, :req_type, :title, :desc, :is_req, :allowed_types, :max_size, :rec_count, :now, :now)
                """)
                conn.execute(req_stmt, {
                    "form_id": form_id,
                    "req_type": req.requirement_type,
                    "title": req.title,
                    "desc": req.description,
                    "is_req": 1 if req.is_required else 0,
                    "allowed_types": req.allowed_file_types,
                    "max_size": req.max_file_size_bytes,
                    "rec_count": req.recommendation_count if req.recommendation_count is not None else 1,
                    "now": now_str
                })

    return ApplicationFormCreateResponse(
        id=form_data.id,
        name=form_data.name,
        status=form_data.status,
        created_at=str(form_data.created_at)
    )
