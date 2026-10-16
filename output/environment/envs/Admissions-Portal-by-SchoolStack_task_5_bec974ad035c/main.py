import os
from datetime import date, datetime
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Path, Query, Body
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# --- Configuration & Database Setup ---

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required.")

# Ensure SQLAlchemy URL format for SQLite
if DATABASE_PATH.startswith("sqlite:///"):
    db_url = DATABASE_PATH
elif DATABASE_PATH.startswith("sqlite://"):
    # Handle sqlite:// without slashes if needed, though spec says strip prefix to get path
    # Standard SQLAlchemy expects sqlite:///path or sqlite:///:memory:
    # The prompt says "strip the exact 'sqlite:///' prefix". 
    # If it's just sqlite://, we might need to adjust. Assuming standard sqlite:/// based on prompt hint.
    db_url = f"sqlite:///{DATABASE_PATH[9:]}" if DATABASE_PATH.startswith("sqlite:///") else DATABASE_PATH
else:
    # Fallback if it's a raw path? Prompt implies it IS a SQLAlchemy URL.
    # "DATABASE_PATH is an absolute SQLAlchemy SQLite URL."
    db_url = DATABASE_PATH

engine = create_engine(db_url, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="Admissions Portal API", version="1.0.0")

# --- Pydantic Models ---

class FormSearchResult(BaseModel):
    id: int
    name: str

class SectionCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    is_optional: bool
    max_entries: Optional[int] = None
    display_order: Optional[int] = 0

class SectionResponse(BaseModel):
    id: int
    form_id: int
    name: str
    is_optional: bool
    max_entries: Optional[int] = None

class FieldCreateRequest(BaseModel):
    field_name: str
    field_label: str
    field_type: str
    is_required: bool
    display_order: Optional[int] = 0

class FieldResponse(BaseModel):
    id: int
    section_id: int
    field_name: str
    field_label: str
    field_type: str
    is_required: bool

class QuestionResponse(BaseModel):
    id: int
    question_text: str
    question_type: str
    is_required: bool

class DocumentCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    is_required: bool
    condition_question_id: Optional[int] = None
    condition_value: Optional[str] = None

class DocumentResponse(BaseModel):
    id: int
    form_id: int
    name: str
    is_required: bool
    condition_question_id: Optional[int] = None
    condition_value: Optional[str] = None

class DeadlineResponse(BaseModel):
    id: int
    deadline_type: str
    deadline_date: str

class DeadlineUpdateRequest(BaseModel):
    deadline_date: str

class DeadlineFullResponse(BaseModel):
    id: int
    form_id: int
    deadline_type: str
    deadline_date: str

# --- Helper Functions ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Note: Since we are using raw SQL with SQLAlchemy Core/ORM sessions, 
# we will use `db.execute` and `db.commit`.

# --- Endpoints ---

@app.get("/forms", response_model=List[FormSearchResult], operation_id="find_forms_by_name", tags=["Forms"])
def find_forms_by_name(name: str = Query(..., description="The name or partial name of the form to search for.")):
    """Search for forms by their name."""
    db = SessionLocal()
    try:
        query = text("SELECT id, name FROM forms WHERE name LIKE :name_pattern")
        results = db.execute(query, {"name_pattern": f"%{name}%"}).fetchall()
        return [{"id": r.id, "name": r.name} for r in results]
    finally:
        db.close()

@app.post("/forms/{form_id}/sections", response_model=SectionResponse, operation_id="create_form_section", tags=["Forms"])
def create_form_section(
    form_id: int = Path(..., description="ID of the form to update."),
    payload: SectionCreateRequest = Body(...)
):
    """Adds a new section to the specified form."""
    db = SessionLocal()
    try:
        # Check if form exists
        form_exists = db.execute(text("SELECT id FROM forms WHERE id = :fid"), {"fid": form_id}).scalar()
        if not form_exists:
            raise HTTPException(status_code=404, detail="Form not found")

        # Insert section
        insert_query = text("""
            INSERT INTO form_sections (form_id, name, description, is_optional, max_entries, display_order)
            VALUES (:form_id, :name, :description, :is_optional, :max_entries, :display_order)
        """)
        
        # Convert boolean to integer for SQLite storage as per schema CHECK constraint
        is_opt_int = 1 if payload.is_optional else 0
        
        result = db.execute(insert_query, {
            "form_id": form_id,
            "name": payload.name,
            "description": payload.description,
            "is_optional": is_opt_int,
            "max_entries": payload.max_entries,
            "display_order": payload.display_order if payload.display_order is not None else 0
        })
        db.commit()
        
        new_id = result.lastrowid
        
        # Fetch created record to ensure consistency and return correct types
        fetch_query = text("SELECT id, form_id, name, is_optional, max_entries FROM form_sections WHERE id = :sid")
        row = db.execute(fetch_query, {"sid": new_id}).fetchone()
        
        return SectionResponse(
            id=row.id,
            form_id=row.form_id,
            name=row.name,
            is_optional=bool(row.is_optional),
            max_entries=row.max_entries
        )
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/sections/{section_id}/fields", response_model=FieldResponse, operation_id="create_section_field", tags=["Forms"])
def create_section_field(
    section_id: int = Path(..., description="ID of the section to add the field to."),
    payload: FieldCreateRequest = Body(...)
):
    """Adds a new field definition to an existing section."""
    db = SessionLocal()
    try:
        # Check if section exists
        section_exists = db.execute(text("SELECT id FROM form_sections WHERE id = :sid"), {"sid": section_id}).scalar()
        if not section_exists:
            raise HTTPException(status_code=404, detail="Section not found")

        # Validate field type against allowed values in DB schema
        allowed_types = ['text', 'textarea', 'number', 'date', 'select', 'checkbox', 'radio']
        if payload.field_type not in allowed_types:
            raise HTTPException(status_code=400, detail=f"Invalid field_type. Must be one of {allowed_types}")

        insert_query = text("""
            INSERT INTO section_fields (section_id, field_name, field_label, field_type, is_required, display_order)
            VALUES (:section_id, :field_name, :field_label, :field_type, :is_required, :display_order)
        """)
        
        is_req_int = 1 if payload.is_required else 0
        
        result = db.execute(insert_query, {
            "section_id": section_id,
            "field_name": payload.field_name,
            "field_label": payload.field_label,
            "field_type": payload.field_type,
            "is_required": is_req_int,
            "display_order": payload.display_order if payload.display_order is not None else 0
        })
        db.commit()
        
        new_id = result.lastrowid
        
        fetch_query = text("SELECT id, section_id, field_name, field_label, field_type, is_required FROM section_fields WHERE id = :fid")
        row = db.execute(fetch_query, {"fid": new_id}).fetchone()
        
        return FieldResponse(
            id=row.id,
            section_id=row.section_id,
            field_name=row.field_name,
            field_label=row.field_label,
            field_type=row.field_type,
            is_required=bool(row.is_required)
        )
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.get("/forms/{form_id}/questions", response_model=List[QuestionResponse], operation_id="get_form_questions", tags=["Forms"])
def get_form_questions(form_id: int = Path(..., description="ID of the form.")):
    """Retrieves all questions associated with a specific form."""
    db = SessionLocal()
    try:
        # Check if form exists
        form_exists = db.execute(text("SELECT id FROM forms WHERE id = :fid"), {"fid": form_id}).scalar()
        if not form_exists:
            raise HTTPException(status_code=404, detail="Form not found")

        query = text("""
            SELECT id, question_text, question_type, is_required 
            FROM form_questions 
            WHERE form_id = :fid
            ORDER BY display_order ASC, id ASC
        """)
        rows = db.execute(query, {"fid": form_id}).fetchall()
        
        return [
            QuestionResponse(
                id=r.id,
                question_text=r.question_text,
                question_type=r.question_type,
                is_required=bool(r.is_required)
            ) for r in rows
        ]
    finally:
        db.close()

@app.post("/forms/{form_id}/documents", response_model=DocumentResponse, operation_id="create_form_document", tags=["Forms"])
def create_form_document(
    form_id: int = Path(..., description="ID of the form."),
    payload: DocumentCreateRequest = Body(...)
):
    """Adds a new document requirement to the form."""
    db = SessionLocal()
    try:
        # Check if form exists
        form_exists = db.execute(text("SELECT id FROM forms WHERE id = :fid"), {"fid": form_id}).scalar()
        if not form_exists:
            raise HTTPException(status_code=404, detail="Form not found")

        # If condition_question_id is provided, verify it belongs to this form
        if payload.condition_question_id:
            q_check = db.execute(
                text("SELECT id FROM form_questions WHERE id = :qid AND form_id = :fid"),
                {"qid": payload.condition_question_id, "fid": form_id}
            ).scalar()
            if not q_check:
                raise HTTPException(status_code=400, detail="Condition question ID does not belong to this form")

        insert_query = text("""
            INSERT INTO form_documents (form_id, name, description, is_required, condition_question_id, condition_value)
            VALUES (:form_id, :name, :description, :is_required, :condition_question_id, :condition_value)
        """)
        
        is_req_int = 1 if payload.is_required else 0
        
        result = db.execute(insert_query, {
            "form_id": form_id,
            "name": payload.name,
            "description": payload.description,
            "is_required": is_req_int,
            "condition_question_id": payload.condition_question_id,
            "condition_value": payload.condition_value
        })
        db.commit()
        
        new_id = result.lastrowid
        
        fetch_query = text("""
            SELECT id, form_id, name, is_required, condition_question_id, condition_value 
            FROM form_documents 
            WHERE id = :did
        """)
        row = db.execute(fetch_query, {"did": new_id}).fetchone()
        
        return DocumentResponse(
            id=row.id,
            form_id=row.form_id,
            name=row.name,
            is_required=bool(row.is_required),
            condition_question_id=row.condition_question_id,
            condition_value=row.condition_value
        )
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.get("/forms/{form_id}/deadlines", response_model=List[DeadlineResponse], operation_id="get_form_deadlines", tags=["Forms"])
def get_form_deadlines(form_id: int = Path(..., description="ID of the form.")):
    """Retrieves all deadline configurations for a specific form."""
    db = SessionLocal()
    try:
        # Check if form exists
        form_exists = db.execute(text("SELECT id FROM forms WHERE id = :fid"), {"fid": form_id}).scalar()
        if not form_exists:
            raise HTTPException(status_code=404, detail="Form not found")

        query = text("""
            SELECT id, deadline_type, deadline_date 
            FROM form_deadlines 
            WHERE form_id = :fid
            ORDER BY deadline_date ASC
        """)
        rows = db.execute(query, {"fid": form_id}).fetchall()
        
        return [
            DeadlineResponse(
                id=r.id,
                deadline_type=r.deadline_type,
                deadline_date=r.deadline_date
            ) for r in rows
        ]
    finally:
        db.close()

@app.put("/deadlines/{deadline_id}", response_model=DeadlineFullResponse, operation_id="update_form_deadline", tags=["Forms"])
def update_form_deadline(
    deadline_id: int = Path(..., description="ID of the deadline record to update."),
    payload: DeadlineUpdateRequest = Body(...)
):
    """Updates the date of an existing deadline."""
    db = SessionLocal()
    try:
        # Check if deadline exists
        existing = db.execute(
            text("SELECT id, form_id, deadline_type FROM form_deadlines WHERE id = :did"),
            {"did": deadline_id}
        ).fetchone()
        
        if not existing:
            raise HTTPException(status_code=404, detail="Deadline not found")

        # Update
        update_query = text("""
            UPDATE form_deadlines 
            SET deadline_date = :new_date, updated_at = CURRENT_TIMESTAMP
            WHERE id = :did
        """)
        
        db.execute(update_query, {
            "new_date": payload.deadline_date,
            "did": deadline_id
        })
        db.commit()
        
        # Fetch updated record
        fetch_query = text("""
            SELECT id, form_id, deadline_type, deadline_date 
            FROM form_deadlines 
            WHERE id = :did
        """)
        row = db.execute(fetch_query, {"did": deadline_id}).fetchone()
        
        return DeadlineFullResponse(
            id=row.id,
            form_id=row.form_id,
            deadline_type=row.deadline_type,
            deadline_date=row.deadline_date
        )
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
