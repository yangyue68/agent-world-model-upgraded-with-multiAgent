import os
import sqlite3
from datetime import date
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Path, Query, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Database setup
DATABASE_PATH = "sqlite:////tmp/schoolstack.db"
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

def get_form_id_by_name(db, name: str) -> int:
    result = db.execute(text("SELECT id FROM forms WHERE name = :name"), {"name": name}).fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="Form not found")
    return result[0]

def get_question_id_by_text(db, form_id: int, question_text: str) -> int:
    result = db.execute(
        text("SELECT id FROM form_questions WHERE form_id = :form_id AND question_text = :question_text"),
        {"form_id": form_id, "question_text": question_text}
    ).fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="Question not found")
    return result[0]

def get_deadline_id_by_type(db, form_id: int, deadline_type: str) -> int:
    result = db.execute(
        text("SELECT id FROM form_deadlines WHERE form_id = :form_id AND deadline_type = :deadline_type"),
        {"form_id": form_id, "deadline_type": deadline_type}
    ).fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="Deadline not found")
    return result[0]

# Endpoints
@app.get("/forms", operation_id="find_forms_by_name", response_model=List[Form])
def find_forms_by_name(name: str = Query(...), db = Depends(get_db)):
    """Find forms by name"""
    results = db.execute(
        text("SELECT id, name FROM forms WHERE name LIKE :name"),
        {"name": f"%{name}%"}
    ).fetchall()
    return [{"id": r[0], "name": r[1]} for r in results]

@app.post("/forms/{form_id}/sections", operation_id="create_form_section", response_model=Section)
def create_form_section(
    form_id: int = Path(...),
    name: str = None,
    description: str = None,
    is_optional: bool = False,
    max_entries: Optional[int] = None,
    display_order: int = 0,
    db = Depends(get_db)
):
    """Create a new section in a form"""
    # Validate form exists
    db.execute(text("SELECT 1 FROM forms WHERE id = :form_id"), {"form_id": form_id}).fetchone()
    
    # Insert section
    result = db.execute(
        text("""
            INSERT INTO form_sections (form_id, name, description, is_optional, max_entries, display_order)
            VALUES (:form_id, :name, :description, :is_optional, :max_entries, :display_order)
            RETURNING id
        """),
        {
            "form_id": form_id,
            "name": name,
            "description": description,
            "is_optional": 1 if is_optional else 0,
            "max_entries": max_entries,
            "display_order": display_order
        }
    )
    db.commit()
    section_id = result.fetchone()[0]
    
    return {
        "id": section_id,
        "form_id": form_id,
        "name": name,
        "is_optional": is_optional,
        "max_entries": max_entries
    }

@app.post("/sections/{section_id}/fields", operation_id="create_section_field", response_model=Field)
def create_section_field(
    section_id: int = Path(...),
    field_name: str = None,
    field_label: str = None,
    field_type: str = None,
    is_required: bool = False,
    display_order: int = 0,
    db = Depends(get_db)
):
    """Add a field to a section"""
    # Validate section exists
    db.execute(text("SELECT 1 FROM form_sections WHERE id = :section_id"), {"section_id": section_id}).fetchone()
    
    # Insert field
    result = db.execute(
        text("""
            INSERT INTO section_fields (section_id, field_name, field_label, field_type, is_required, display_order)
            VALUES (:section_id, :field_name, :field_label, :field_type, :is_required, :display_order)
            RETURNING id
        """),
        {
            "section_id": section_id,
            "field_name": field_name,
            "field_label": field_label,
            "field_type": field_type,
            "is_required": 1 if is_required else 0,
            "display_order": display_order
        }
    )
    db.commit()
    field_id = result.fetchone()[0]
    
    return {
        "id": field_id,
        "section_id": section_id,
        "field_name": field_name,
        "field_label": field_label,
        "field_type": field_type,
        "is_required": is_required
    }

@app.get("/forms/{form_id}/questions", operation_id="get_form_questions", response_model=List[Question])
def get_form_questions(form_id: int = Path(...), db = Depends(get_db)):
    """Get questions for a form"""
    results = db.execute(
        text("SELECT id, question_text, question_type, is_required FROM form_questions WHERE form_id = :form_id ORDER BY display_order"),
        {"form_id": form_id}
    ).fetchall()
    return [
        {
            "id": r[0],
            "question_text": r[1],
            "question_type": r[2],
            "is_required": bool(r[3])
        } for r in results
    ]

@app.post("/forms/{form_id}/documents", operation_id="create_form_document", response_model=Document)
def create_form_document(
    form_id: int = Path(...),
    name: str = None,
    description: str = None,
    is_required: bool = False,
    condition_question_id: Optional[int] = None,
    condition_value: Optional[str] = None,
    db = Depends(get_db)
):
    """Add a document requirement to a form"""
    # Validate form exists
    db.execute(text("SELECT 1 FROM forms WHERE id = :form_id"), {"form_id": form_id}).fetchone()
    
    # Validate condition question if provided
    if condition_question_id:
        db.execute(
            text("SELECT 1 FROM form_questions WHERE id = :question_id"),
            {"question_id": condition_question_id}
        ).fetchone()
    
    # Insert document
    result = db.execute(
        text("""
            INSERT INTO form_documents (form_id, name, description, is_required, condition_question_id, condition_value)
            VALUES (:form_id, :name, :description, :is_required, :condition_question_id, :condition_value)
            RETURNING id
        """),
        {
            "form_id": form_id,
            "name": name,
            "description": description,
            "is_required": 1 if is_required else 0,
            "condition_question_id": condition_question_id,
            "condition_value": condition_value
        }
    )
    db.commit()
    document_id = result.fetchone()[0]
    
    return {
        "id": document_id,
        "form_id": form_id,
        "name": name,
        "is_required": is_required,
        "condition_question_id": condition_question_id,
        "condition_value": condition_value
    }

@app.get("/forms/{form_id}/deadlines", operation_id="get_form_deadlines", response_model=List[Deadline])
def get_form_deadlines(form_id: int = Path(...), db = Depends(get_db)):
    """Get deadlines for a form"""
    results = db.execute(
        text("SELECT id, deadline_type, deadline_date FROM form_deadlines WHERE form_id = :form_id ORDER BY deadline_type"),
        {"form_id": form_id}
    ).fetchall()
    return [
        {
            "id": r[0],
            "form_id": form_id,
            "deadline_type": r[1],
            "deadline_date": r[2]
        } for r in results
    ]

@app.put("/deadlines/{deadline_id}", operation_id="update_form_deadline", response_model=Deadline)
def update_form_deadline(
    deadline_id: int = Path(...),
    deadline_date: date = None,
    db = Depends(get_db)
):
    """Update a form deadline"""
    # Validate deadline exists
    db.execute(text("SELECT 1 FROM form_deadlines WHERE id = :deadline_id"), {"deadline_id": deadline_id}).fetchone()
    
    # Update deadline
    db.execute(
        text("UPDATE form_deadlines SET deadline_date = :deadline_date WHERE id = :deadline_id"),
        {"deadline_date": deadline_date, "deadline_id": deadline_id}
    )
    db.commit()
    
    # Fetch updated deadline
    result = db.execute(
        text("SELECT id, form_id, deadline_type, deadline_date FROM form_deadlines WHERE id = :deadline_id"),
        {"deadline_id": deadline_id}
    ).fetchone()
    
    return {
        "id": result[0],
        "form_id": result[1],
        "deadline_type": result[2],
        "deadline_date": result[3]
    }

# Main execution logic
@app.on_event("startup")
def startup_event():
    db = next(get_db())
    try:
        # Find the form ID for "Fall 2025 Undergraduate Admissions"
        form_id = get_form_id_by_name(db, "Fall 2025 Undergraduate Admissions")
        
        # Create the "Extracurricular Activities" section
        section_result = db.execute(
            text("""
                INSERT INTO form_sections (form_id, name, description, is_optional, max_entries, display_order)
                VALUES (:form_id, :name, :description, :is_optional, :max_entries, :display_order)
                RETURNING id
            """),
            {
                "form_id": form_id,
                "name": "Extracurricular Activities",
                "description": "List your extracurricular activities",
                "is_optional": 1,
                "max_entries": 5,
                "display_order": 3
            }
        )
        db.commit()
        section_id = section_result.fetchone()[0]
        
        # Add fields to the section
        fields_data = [
            ("activity_name", "Activity Name", "text", 1),
            ("role", "Role", "text", 1),
            ("hours_per_week", "Hours Per Week", "number", 1),
            ("description", "Description", "textarea", 0)
        ]
        
        for field_name, field_label, field_type, is_required in fields_data:
            db.execute(
                text("""
                    INSERT INTO section_fields (section_id, field_name, field_label, field_type, is_required, display_order)
                    VALUES (:section_id, :field_name, :field_label, :field_type, :is_required, :display_order)
                """),
                {
                    "section_id": section_id,
                    "field_name": field_name,
                    "field_label": field_label,
                    "field_type": field_type,
                    "is_required": 1 if is_required else 0,
                    "display_order": fields_data.index((field_name, field_label, field_type, is_required)) + 1
                }
            )
        db.commit()
        
        # Get the question ID for "Are you applying for need-based aid?"
        question_id = get_question_id_by_text(db, form_id, "Are you applying for need-based aid?")
        
        # Add the Financial Aid Statement document
        db.execute(
            text("""
                INSERT INTO form_documents (form_id, name, description, is_required, condition_question_id, condition_value)
                VALUES (:form_id, :name, :description, :is_required, :condition_question_id, :condition_value)
            """),
            {
                "form_id": form_id,
                "name": "Financial Aid Statement",
                "description": "A statement regarding financial need",
                "is_required": 1,
                "condition_question_id": question_id,
                "condition_value": "Yes"
            }
        )
        db.commit()
        
        # Update the regular decision deadline
        deadline_id = get_deadline_id_by_type(db, form_id, "regular_decision")
        db.execute(
            text("UPDATE form_deadlines SET deadline_date = :deadline_date WHERE id = :deadline_id"),
            {"deadline_date": "2025-01-05", "deadline_id": deadline_id}
        )
        db.commit()
        
    except Exception as e:
        print(f"Error during initialization: {e}")
        raise
    finally:
        db.close()
