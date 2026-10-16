import os
import sqlite3
from datetime import date
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Path, Query, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise ValueError("DATABASE_PATH environment variable is required")

# Strip the 'sqlite:///' prefix to get the file path
DB_FILE_PATH = DATABASE_PATH.replace("sqlite:///", "")

# Create SQLAlchemy engine
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# FastAPI app
app = FastAPI()

# Models
class Form(BaseModel):
    id: int
    name: str

class Section(BaseModel):
    id: int
    form_id: int
    name: str
    is_optional: bool
    max_entries: Optional[int] = None

class Field(BaseModel):
    id: int
    section_id: int
    field_name: str
    field_label: str
    field_type: str
    is_required: bool

class Question(BaseModel):
    id: int
    question_text: str
    question_type: str
    is_required: bool

class Document(BaseModel):
    id: int
    form_id: int
    name: str
    is_required: bool
    condition_question_id: Optional[int] = None
    condition_value: Optional[str] = None

class Deadline(BaseModel):
    id: int
    form_id: int
    deadline_type: str
    deadline_date: date

# Helper functions
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# API Endpoints
@app.get("/forms", response_model=List[Form], operation_id="find_forms_by_name")
def find_forms_by_name(name: str = Query(...), db = Depends(get_db)):
    result = db.execute(
        text("SELECT id, name FROM forms WHERE name LIKE :name"),
        {"name": f"%{name}%"}
    ).fetchall()
    return [Form(id=row[0], name=row[1]) for row in result]

@app.post("/forms/{form_id}/sections", response_model=Section, operation_id="create_form_section")
def create_form_section(
    form_id: int = Path(...),
    name: str = None,
    description: str = None,
    is_optional: bool = None,
    max_entries: int = None,
    display_order: int = None,
    db = Depends(get_db)
):
    # Validate form exists
    form = db.execute(text("SELECT id FROM forms WHERE id = :form_id"), {"form_id": form_id}).fetchone()
    if not form:
        raise HTTPException(status_code=404, detail="Form not found")
    
    # Insert section
    result = db.execute(
        text("""
            INSERT INTO form_sections (form_id, name, description, is_optional, max_entries, display_order)
            VALUES (:form_id, :name, :description, :is_optional, :max_entries, :display_order)
            RETURNING id, form_id, name, is_optional, max_entries
        """),
        {
            "form_id": form_id,
            "name": name,
            "description": description,
            "is_optional": int(is_optional),
            "max_entries": max_entries,
            "display_order": display_order or 0
        }
    )
    db.commit()
    
    row = result.fetchone()
    return Section(
        id=row[0],
        form_id=row[1],
        name=row[2],
        is_optional=bool(row[3]),
        max_entries=row[4]
    )

@app.post("/sections/{section_id}/fields", response_model=Field, operation_id="create_section_field")
def create_section_field(
    section_id: int = Path(...),
    field_name: str = None,
    field_label: str = None,
    field_type: str = None,
    is_required: bool = None,
    display_order: int = None,
    db = Depends(get_db)
):
    # Validate section exists
    section = db.execute(text("SELECT id FROM form_sections WHERE id = :section_id"), {"section_id": section_id}).fetchone()
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")
    
    # Insert field
    result = db.execute(
        text("""
            INSERT INTO section_fields (section_id, field_name, field_label, field_type, is_required, display_order)
            VALUES (:section_id, :field_name, :field_label, :field_type, :is_required, :display_order)
            RETURNING id, section_id, field_name, field_label, field_type, is_required
        """),
        {
            "section_id": section_id,
            "field_name": field_name,
            "field_label": field_label,
            "field_type": field_type,
            "is_required": int(is_required),
            "display_order": display_order or 0
        }
    )
    db.commit()
    
    row = result.fetchone()
    return Field(
        id=row[0],
        section_id=row[1],
        field_name=row[2],
        field_label=row[3],
        field_type=row[4],
        is_required=bool(row[5])
    )

@app.get("/forms/{form_id}/questions", response_model=List[Question], operation_id="get_form_questions")
def get_form_questions(form_id: int = Path(...), db = Depends(get_db)):
    result = db.execute(
        text("""
            SELECT id, question_text, question_type, is_required 
            FROM form_questions 
            WHERE form_id = :form_id
        """),
        {"form_id": form_id}
    ).fetchall()
    return [
        Question(
            id=row[0],
            question_text=row[1],
            question_type=row[2],
            is_required=bool(row[3])
        ) for row in result
    ]

@app.post("/forms/{form_id}/documents", response_model=Document, operation_id="create_form_document")
def create_form_document(
    form_id: int = Path(...),
    name: str = None,
    description: str = None,
    is_required: bool = None,
    condition_question_id: int = None,
    condition_value: str = None,
    db = Depends(get_db)
):
    # Validate form exists
    form = db.execute(text("SELECT id FROM forms WHERE id = :form_id"), {"form_id": form_id}).fetchone()
    if not form:
        raise HTTPException(status_code=404, detail="Form not found")
    
    # Validate condition question if provided
    if condition_question_id:
        question = db.execute(
            text("SELECT id FROM form_questions WHERE id = :question_id AND form_id = :form_id"),
            {"question_id": condition_question_id, "form_id": form_id}
        ).fetchone()
        if not question:
            raise HTTPException(status_code=404, detail="Condition question not found in this form")
    
    # Insert document
    result = db.execute(
        text("""
            INSERT INTO form_documents (form_id, name, description, is_required, condition_question_id, condition_value)
            VALUES (:form_id, :name, :description, :is_required, :condition_question_id, :condition_value)
            RETURNING id, form_id, name, is_required, condition_question_id, condition_value
        """),
        {
            "form_id": form_id,
            "name": name,
            "description": description,
            "is_required": int(is_required),
            "condition_question_id": condition_question_id,
            "condition_value": condition_value
        }
    )
    db.commit()
    
    row = result.fetchone()
    return Document(
        id=row[0],
        form_id=row[1],
        name=row[2],
        is_required=bool(row[3]),
        condition_question_id=row[4],
        condition_value=row[5]
    )

@app.get("/forms/{form_id}/deadlines", response_model=List[Deadline], operation_id="get_form_deadlines")
def get_form_deadlines(form_id: int = Path(...), db = Depends(get_db)):
    result = db.execute(
        text("""
            SELECT id, deadline_type, deadline_date 
            FROM form_deadlines 
            WHERE form_id = :form_id
        """),
        {"form_id": form_id}
    ).fetchall()
    return [
        Deadline(
            id=row[0],
            form_id=form_id,
            deadline_type=row[1],
            deadline_date=date.fromisoformat(row[2])
        ) for row in result
    ]

@app.put("/deadlines/{deadline_id}", response_model=Deadline, operation_id="update_form_deadline")
def update_form_deadline(
    deadline_id: int = Path(...),
    deadline_date: date = None,
    db = Depends(get_db)
):
    # Validate deadline exists
    deadline = db.execute(
        text("SELECT id, form_id, deadline_type FROM form_deadlines WHERE id = :deadline_id"),
        {"deadline_id": deadline_id}
    ).fetchone()
    if not deadline:
        raise HTTPException(status_code=404, detail="Deadline not found")
    
    # Update deadline
    db.execute(
        text("""
            UPDATE form_deadlines 
            SET deadline_date = :deadline_date
            WHERE id = :deadline_id
        """),
        {
            "deadline_date": deadline_date.isoformat(),
            "deadline_id": deadline_id
        }
    )
    db.commit()
    
    return Deadline(
        id=deadline[0],
        form_id=deadline[1],
        deadline_type=deadline[2],
        deadline_date=deadline_date
    )

# Main execution
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
