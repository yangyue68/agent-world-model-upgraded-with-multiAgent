import os
import sqlite3
from datetime import date
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Path, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise ValueError("DATABASE_PATH environment variable is required")

# Strip the 'sqlite:///' prefix to get the file path
DB_FILE_PATH = DATABASE_PATH.replace("sqlite:///", "")

# Create engine and session
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()

# Request and Response Models
class Form(BaseModel):
    id: int
    name: str

class SectionCreate(BaseModel):
    name: str
    description: Optional[str] = None
    is_optional: bool
    max_entries: Optional[int] = None
    display_order: Optional[int] = None

class SectionFieldCreate(BaseModel):
    field_name: str
    field_label: str
    field_type: str
    is_required: bool
    display_order: Optional[int] = None

class Question(BaseModel):
    id: int
    question_text: str
    question_type: str
    is_required: bool

class DocumentCreate(BaseModel):
    name: str
    description: Optional[str] = None
    is_required: bool
    condition_question_id: Optional[int] = None
    condition_value: Optional[str] = None

class Deadline(BaseModel):
    id: int
    deadline_type: str
    deadline_date: date

class DeadlineUpdate(BaseModel):
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
@app.get("/forms", response_model=List[Form])
def find_forms_by_name(name: str, db = Depends(get_db)):
    """Find forms by name"""
    results = db.execute(
        text("SELECT id, name FROM forms WHERE name LIKE :name"),
        {"name": f"%{name}%"}
    ).fetchall()
    return [{"id": r[0], "name": r[1]} for r in results]

@app.post("/forms/{form_id}/sections", response_model=dict)
def create_form_section(form_id: int, section: SectionCreate, db = Depends(get_db)):
    """Create a new section in a form"""
    # Check if form exists
    db.execute(text("SELECT 1 FROM forms WHERE id = :form_id"), {"form_id": form_id}).fetchone()
    
    # Insert section
    result = db.execute(
        text("""
            INSERT INTO form_sections (form_id, name, description, is_optional, max_entries, display_order)
            VALUES (:form_id, :name, :description, :is_optional, :max_entries, :display_order)
            RETURNING id, form_id, name, is_optional, max_entries
        """),
        {
            "form_id": form_id,
            "name": section.name,
            "description": section.description,
            "is_optional": 1 if section.is_optional else 0,
            "max_entries": section.max_entries,
            "display_order": section.display_order or 0
        }
    ).fetchone()
    
    db.commit()
    
    return {
        "id": result[0],
        "form_id": result[1],
        "name": result[2],
        "is_optional": bool(result[3]),
        "max_entries": result[4]
    }

@app.post("/sections/{section_id}/fields", response_model=dict)
def create_section_field(section_id: int, field: SectionFieldCreate, db = Depends(get_db)):
    """Add a field to a section"""
    # Check if section exists
    db.execute(text("SELECT 1 FROM form_sections WHERE id = :section_id"), {"section_id": section_id}).fetchone()
    
    # Insert field
    result = db.execute(
        text("""
            INSERT INTO section_fields (section_id, field_name, field_label, field_type, is_required, display_order)
            VALUES (:section_id, :field_name, :field_label, :field_type, :is_required, :display_order)
            RETURNING id, section_id, field_name, field_label, field_type, is_required
        """),
        {
            "section_id": section_id,
            "field_name": field.field_name,
            "field_label": field.field_label,
            "field_type": field.field_type,
            "is_required": 1 if field.is_required else 0,
            "display_order": field.display_order or 0
        }
    ).fetchone()
    
    db.commit()
    
    return {
        "id": result[0],
        "section_id": result[1],
        "field_name": result[2],
        "field_label": result[3],
        "field_type": result[4],
        "is_required": bool(result[5])
    }

@app.get("/forms/{form_id}/questions", response_model=List[Question])
def get_form_questions(form_id: int, db = Depends(get_db)):
    """Get questions for a form"""
    results = db.execute(
        text("""
            SELECT id, question_text, question_type, is_required 
            FROM form_questions 
            WHERE form_id = :form_id
        """),
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

@app.post("/forms/{form_id}/documents", response_model=dict)
def create_form_document(form_id: int, document: DocumentCreate, db = Depends(get_db)):
    """Add a document requirement to a form"""
    # Check if form exists
    db.execute(text("SELECT 1 FROM forms WHERE id = :form_id"), {"form_id": form_id}).fetchone()
    
    # Insert document
    result = db.execute(
        text("""
            INSERT INTO form_documents (form_id, name, description, is_required, condition_question_id, condition_value)
            VALUES (:form_id, :name, :description, :is_required, :condition_question_id, :condition_value)
            RETURNING id, form_id, name, is_required, condition_question_id, condition_value
        """),
        {
            "form_id": form_id,
            "name": document.name,
            "description": document.description,
            "is_required": 1 if document.is_required else 0,
            "condition_question_id": document.condition_question_id,
            "condition_value": document.condition_value
        }
    ).fetchone()
    
    db.commit()
    
    return {
        "id": result[0],
        "form_id": result[1],
        "name": result[2],
        "is_required": bool(result[3]),
        "condition_question_id": result[4],
        "condition_value": result[5]
    }

@app.get("/forms/{form_id}/deadlines", response_model=List[Deadline])
def get_form_deadlines(form_id: int, db = Depends(get_db)):
    """Get deadlines for a form"""
    results = db.execute(
        text("""
            SELECT id, deadline_type, deadline_date 
            FROM form_deadlines 
            WHERE form_id = :form_id
        """),
        {"form_id": form_id}
    ).fetchall()
    
    return [
        {
            "id": r[0],
            "deadline_type": r[1],
            "deadline_date": date.fromisoformat(r[2]) if isinstance(r[2], str) else r[2]
        } for r in results
    ]

@app.put("/deadlines/{deadline_id}", response_model=Deadline)
def update_form_deadline(deadline_id: int, deadline_update: DeadlineUpdate, db = Depends(get_db)):
    """Update a form deadline"""
    # Check if deadline exists
    db.execute(text("SELECT 1 FROM form_deadlines WHERE id = :deadline_id"), {"deadline_id": deadline_id}).fetchone()
    
    # Update deadline
    db.execute(
        text("""
            UPDATE form_deadlines 
            SET deadline_date = :deadline_date 
            WHERE id = :deadline_id
        """),
        {
            "deadline_date": deadline_update.deadline_date,
            "deadline_id": deadline_id
        }
    )
    
    db.commit()
    
    # Fetch updated deadline
    result = db.execute(
        text("""
            SELECT id, form_id, deadline_type, deadline_date 
            FROM form_deadlines 
            WHERE id = :deadline_id
        """),
        {"deadline_id": deadline_id}
    ).fetchone()
    
    return {
        "id": result[0],
        "form_id": result[1],
        "deadline_type": result[2],
        "deadline_date": date.fromisoformat(result[3]) if isinstance(result[3], str) else result[3]
    }

# Main execution logic
@app.on_event("startup")
def startup_event():
    # Get the form ID for "Fall 2025 Undergraduate Admissions"
    with next(get_db()) as db:
        form_id = get_form_id_by_name(db, "Fall 2025 Undergraduate Admissions")
        
        # Step 1: Add Extracurricular Activities section
        section_response = create_form_section(
            form_id=form_id,
            section=SectionCreate(
                name="Extracurricular Activities",
                description="List your extracurricular activities",
                is_optional=True,
                max_entries=5,
                display_order=3
            ),
            db=db
        )
        section_id = section_response["id"]
        
        # Step 2: Add fields to the section
        field_names = ["activity_name", "role", "hours_per_week", "description"]
        field_labels = ["Activity Name", "Role", "Hours Per Week", "Description"]
        field_types = ["text", "text", "number", "textarea"]
        
        for i, (field_name, field_label, field_type) in enumerate(zip(field_names, field_labels, field_types)):
            create_section_field(
                section_id=section_id,
                field=SectionFieldCreate(
                    field_name=field_name,
                    field_label=field_label,
                    field_type=field_type,
                    is_required=False,
                    display_order=i+1
                ),
                db=db
            )
        
        # Step 3: Find the question ID for "Are you applying for need-based aid?"
        question_id = get_question_id_by_text(db, form_id, "Are you applying for need-based aid?")
        
        # Step 4: Add Financial Aid Statement document
        create_form_document(
            form_id=form_id,
            document=DocumentCreate(
                name="Financial Aid Statement",
                description="Required statement for need-based aid applicants",
                is_required=True,
                condition_question_id=question_id,
                condition_value="Yes"
            ),
            db=db
        )
        
        # Step 5: Update regular decision deadline
        deadline_id = get_deadline_id_by_type(db, form_id, "regular_decision")
        update_form_deadline(
            deadline_id=deadline_id,
            deadline_update=DeadlineUpdate(deadline_date=date(2025, 1, 5)),
            db=db
        )
