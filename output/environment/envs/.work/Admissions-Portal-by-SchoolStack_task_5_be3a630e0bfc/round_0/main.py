```python
import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query, Path, Body
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="Admissions Portal API")

# Pydantic Models for Responses and Requests

class FormSearchResult(BaseModel):
    id: int
    name: str

class SectionCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    is_optional: bool
    max_entries: Optional[int] = None
    display_order: Optional[int] = None

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
    display_order: Optional[int] = None

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


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/forms", response_model=List[FormSearchResult], operation_id="find_forms_by_name", tags=["Forms"])
def find_forms_by_name(name: str = Query(..., description="The name or partial name of the form to search for.")):
    db = next(get_db())
    try:
        query = text("SELECT id, name FROM forms WHERE name LIKE :name_pattern")
        results = db.execute(query, {"name_pattern": f"%{name}%"}).fetchall()
        return [FormSearchResult(id=row.id, name=row.name) for row in results]
    finally:
        db.close()

@app.post("/forms/{form_id}/sections", response_model=SectionResponse, operation_id="create_form_section", tags=["Forms"])
def create_form_section(form_id: int = Path(...), body: SectionCreateRequest = Body(...)):
    db = next(get_db())
    try:
        # Check if form exists
        form_exists = db.execute(text("SELECT 1 FROM forms WHERE id = :id"), {"id": form_id}).fetchone()
        if not form_exists:
            raise HTTPException(status_code=404, detail="Form not found")
        
        # Check for duplicate section name within the form
        existing_section = db.execute(
            text("SELECT 1 FROM form_sections WHERE form_id = :fid AND name = :name"),
            {"fid": form_id, "name": body.name}
        ).fetchone()
        if existing_section:
            raise HTTPException(status_code=409, detail="Section with this name already exists in the form")

        insert_query = text("""
            INSERT INTO form_sections (form_id, name, description, is_optional, max_entries, display_order)
            VALUES (:form_id, :name, :description, :is_optional, :max_entries, :display_order)
        """)
        
        params = {
            "form_id": form_id,
            "name": body.name,
            "description": body.description,
            "is_optional": 1 if body.is_optional else 0,
            "max_entries": body.max_entries,
            "display_order": body.display_order if body.display_order is not None else 0
        }
        
        result = db.execute(insert_query, params)
        db.commit()
        
        new_id = result.lastrowid
        
        return SectionResponse(
            id=new_id,
            form_id=form_id,
            name=body.name,
            is_optional=body.is_optional,
            max_entries=body.max_entries
        )
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()

@app.post("/sections/{section_id}/fields", response_model=FieldResponse, operation_id="create_section_field", tags=["Forms"])
def create_section_field(section_id: int = Path(...), body: FieldCreateRequest = Body(...)):
    db = next(get_db())
    try:
        # Check if section exists
        section_exists = db.execute(text("SELECT 1 FROM form_sections WHERE id = :id"), {"id": section_id}).fetchone()
        if not section_exists:
            raise HTTPException(status_code=404, detail="Section not found")
        
        # Check for duplicate field name within the section
        existing_field = db.execute(
            text("SELECT 1 FROM section_fields WHERE section_id = :sid AND field_name = :fname"),
            {"sid": section_id, "fname": body.field_name}
        ).fetchone()
        if existing_field:
            raise HTTPException(status_code=409, detail="Field with this name already exists in the section")

        insert_query = text("""
            INSERT INTO section_fields (section_id, field_name, field_label, field_type, is_required, display_order)
            VALUES (:section_id, :field_name, :field_label, :field_type, :is_required, :display_order)
        """)
        
        params = {
            "section_id": section_id,
            "field_name": body.field_name,
            "field_label": body.field_label,
            "field_type": body.field_type,
            "is_required": 1 if body.is_required else 0,
            "display_order": body.display_order if body.display_order is not None else 0
        }
        
        result = db.execute(insert_query, params)
        db.commit()
        
        new_id = result.lastrowid
        
        return FieldResponse(
            id=new_id,
            section_id=section_id,
            field_name=body.field_name,
            field_label=body.field_label,
            field_type=body.field_type,
            is_required=body.is_required
        )
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()

@app.get("/forms/{form_id}/questions", response_model=List[QuestionResponse], operation_id="get_form_questions", tags=["Forms"])
def get_form_questions(form_id: int = Path(...)):
    db = next(get_db())
    try:
        # Check if form exists
        form_exists = db.execute(text("SELECT 1 FROM forms WHERE id = :id"), {"id": form_id}).fetchone()
        if not form_exists:
            raise HTTPException(status_code=404, detail="Form not found")
            
        query = text("SELECT id, question_text, question_type, is_required FROM form_questions WHERE form_id = :fid ORDER BY display_order")
        results = db.execute(query, {"fid": form_id}).fetchall()
        
        return [
            QuestionResponse(
                id=row.id,
                question_text=row.question_text,
                question_type=row.question_type,
                is_required=bool(row.is_required)
            )
            for row in results
        ]
    finally:
        db.close()

@app.post("/forms/{form_id}/documents", response_model=DocumentResponse, operation_id="create_form_document", tags=["Forms"])
def create_form_document(form_id: int = Path(...), body: DocumentCreateRequest = Body(...)):
    db = next(get_db())
    try:
        # Check if form exists
        form_exists = db.execute(text("SELECT 1 FROM forms WHERE id = :id"), {"id": form_id}).fetchone()
        if not form_exists:
            raise HTTPException(status_code=404, detail="Form not found")
        
        # Check for duplicate document name within the form
        existing_doc = db.execute(
            text("SELECT 1 FROM form_documents WHERE form_id = :fid AND name = :name"),
            {"fid": form_id, "name": body.name}
        ).fetchone()
        if existing_doc:
            raise HTTPException(status_code=409, detail="Document with this name already exists in the form")

        # Validate condition question if provided
        if body.condition_question_id is not None:
            q_exists = db.execute(
                text("SELECT 1 FROM form_questions WHERE id = :qid AND form_id = :fid"),
                {"qid": body.condition_question_id, "fid": form_id}
            ).fetchone()
            if not q_exists:
                raise HTTPException(status_code=400, detail="Condition question ID does not belong to this form")

        insert_query = text("""
            INSERT INTO form_documents (form_id, name, description, is_required, condition_question_id, condition_value)
            VALUES (:form_id, :name, :description, :is_required, :condition_question_id, :condition_value)
        """)
        
        params = {
            "form_id": form_id,
            "name": body.name,
            "description": body.description,
            "is_required": 1 if body.is_required else 0,
            "condition_question_id": body.condition_question_id,
            "condition_value": body.condition_value
        }
        
        result = db.execute(insert_query, params)
        db.commit()
        
        new_id = result.lastrowid
        
        return DocumentResponse(
            id=new_id,
            form_id=form_id,
            name=body.name,
            is_required=body.is_required,
            condition_question_id=body.condition_question_id,
            condition_value=body.condition_value
        )
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()

@app.get("/forms/{form_id}/deadlines", response_model=List[DeadlineResponse], operation_id="get_form_deadlines", tags=["Forms"])
def get_form_deadlines(form_id: int = Path(...)):
    db = next(get_db())
    try:
        # Check if form exists
        form_exists = db.execute(text("SELECT 1 FROM forms WHERE id = :id"), {"id": form_id}).fetchone()
        if not form_exists:
            raise HTTPException(status_code=404, detail="Form not found")
            
        query = text("SELECT id, deadline_type, deadline_date FROM form_deadlines WHERE form_id = :fid")
        results = db.execute(query, {"fid": form_id}).fetchall()
        
        return [
            DeadlineResponse(
                id=row.id,
                deadline_type=row.deadline_type,
                deadline_date=str(row.deadline_date)
            )
            for row in results
        ]
    finally:
        db.close()

@app.put("/deadlines/{deadline_id}", response_model=DeadlineFullResponse, operation_id="update_form_deadline", tags=["Forms"])
def update_form_deadline(deadline_id: int = Path(...), body: DeadlineUpdateRequest = Body(...)):
    db = next(get_db())
    try:
        # Get current deadline info to verify existence and get form_id/type for response
        current = db.execute(
            text("SELECT form_id, deadline_type FROM form_deadlines WHERE id = :id"),
            {"id": deadline_id}
        ).fetchone()
        
        if not current:
            raise HTTPException(status_code=404, detail="Deadline not found")
        
        update_query = text("""
            UPDATE form_deadlines 
            SET deadline_date = :new_date, updated_at = CURRENT_TIMESTAMP
            WHERE id = :id
        """)
        
        db.execute(update_query, {"new_date": body.deadline_date, "id": deadline_id})
        db.commit()
        
        return DeadlineFullResponse(
            id=deadline_id,
            form_id=current.form_id,
            deadline_type=current.deadline_type,
            deadline_date=body.deadline_date
        )
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
