import os
import sqlite3
from datetime import date
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Extract DATABASE_PATH from environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise ValueError("DATABASE_PATH environment variable is required")

# Strip 'sqlite:///' prefix to get file path
DB_FILE_PATH = DATABASE_PATH.replace("sqlite:///", "")

# Create SQLAlchemy engine
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Initialize FastAPI app
app = FastAPI()

# Pydantic models for request/response types
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

# Helper function to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Endpoint: Find forms by name
@app.get("/forms", response_model=List[Form])
def find_forms_by_name(name: str):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id, name FROM forms WHERE name LIKE :name"),
            {"name": f"%{name}%"}
        )
        return [Form(id=row[0], name=row[1]) for row in result.fetchall()]

# Endpoint: Create a new section in a form
@app.post("/forms/{form_id}/sections", response_model=Form)
def create_form_section(
    form_id: int,
    section_data: SectionCreate,
    db = get_db()
):
    # Validate form exists
    result = db.execute(text("SELECT id FROM forms WHERE id = :form_id"), {"form_id": form_id})
    if not result.fetchone():
        raise HTTPException(status_code=404, detail="Form not found")
    
    # Insert new section
    stmt = text("""
        INSERT INTO form_sections (form_id, name, description, is_optional, max_entries, display_order)
        VALUES (:form_id, :name, :description, :is_optional, :max_entries, :display_order)
        RETURNING id, form_id, name, is_optional, max_entries
    """)
    
    params = {
        "form_id": form_id,
        "name": section_data.name,
        "description": section_data.description,
        "is_optional": 1 if section_data.is_optional else 0,
        "max_entries": section_data.max_entries,
        "display_order": section_data.display_order or 0
    }
    
    result = db.execute(stmt, params)
    db.commit()
    
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=500, detail="Failed to create section")
    
    return {
        "id": row[0],
        "form_id": row[1],
        "name": row[2],
        "is_optional": bool(row[3]),
        "max_entries": row[4]
    }

# Endpoint: Add a field to a section
@app.post("/sections/{section_id}/fields", response_model=SectionFieldCreate)
def create_section_field(
    section_id: int,
    field_data: SectionFieldCreate,
    db = get_db()
):
    # Validate section exists
    result = db.execute(text("SELECT id FROM form_sections WHERE id = :section_id"), {"section_id": section_id})
    if not result.fetchone():
        raise HTTPException(status_code=404, detail="Section not found")
    
    # Insert new field
    stmt = text("""
        INSERT INTO section_fields (section_id, field_name, field_label, field_type, is_required, display_order)
        VALUES (:section_id, :field_name, :field_label, :field_type, :is_required, :display_order)
        RETURNING id, section_id, field_name, field_label, field_type, is_required
    """)
    
    params = {
        "section_id": section_id,
        "field_name": field_data.field_name,
        "field_label": field_data.field_label,
        "field_type": field_data.field_type,
        "is_required": 1 if field_data.is_required else 0,
        "display_order": field_data.display_order or 0
    }
    
    result = db.execute(stmt, params)
    db.commit()
    
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=500, detail="Failed to create field")
    
    return {
        "id": row[0],
        "section_id": row[1],
        "field_name": row[2],
        "field_label": row[3],
        "field_type": row[4],
        "is_required": bool(row[5])
    }

# Endpoint: Get questions for a form
@app.get("/forms/{form_id}/questions", response_model=List[Question])
def get_form_questions(form_id: int):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id, question_text, question_type, is_required FROM form_questions WHERE form_id = :form_id ORDER BY display_order"),
            {"form_id": form_id}
        )
        return [
            Question(
                id=row[0],
                question_text=row[1],
                question_type=row[2],
                is_required=bool(row[3])
            ) for row in result.fetchall()
        ]

# Endpoint: Add a document requirement to a form
@app.post("/forms/{form_id}/documents", response_model=DocumentCreate)
def create_form_document(
    form_id: int,
    document_data: DocumentCreate,
    db = get_db()
):
    # Validate form exists
    result = db.execute(text("SELECT id FROM forms WHERE id = :form_id"), {"form_id": form_id})
    if not result.fetchone():
        raise HTTPException(status_code=404, detail="Form not found")
    
    # Check if condition question exists if provided
    if document_data.condition_question_id:
        result = db.execute(
            text("SELECT id FROM form_questions WHERE id = :question_id AND form_id = :form_id"),
            {"question_id": document_data.condition_question_id, "form_id": form_id}
        )
        if not result.fetchone():
            raise HTTPException(status_code=404, detail="Condition question not found in this form")
    
    # Insert new document
    stmt = text("""
        INSERT INTO form_documents (form_id, name, description, is_required, condition_question_id, condition_value)
        VALUES (:form_id, :name, :description, :is_required, :condition_question_id, :condition_value)
        RETURNING id, form_id, name, is_required, condition_question_id, condition_value
    """)
    
    params = {
        "form_id": form_id,
        "name": document_data.name,
        "description": document_data.description,
        "is_required": 1 if document_data.is_required else 0,
        "condition_question_id": document_data.condition_question_id,
        "condition_value": document_data.condition_value
    }
    
    result = db.execute(stmt, params)
    db.commit()
    
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=500, detail="Failed to create document")
    
    return {
        "id": row[0],
        "form_id": row[1],
        "name": row[2],
        "is_required": bool(row[3]),
        "condition_question_id": row[4],
        "condition_value": row[5]
    }

# Endpoint: Get deadlines for a form
@app.get("/forms/{form_id}/deadlines", response_model=List[Deadline])
def get_form_deadlines(form_id: int):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id, deadline_type, deadline_date FROM form_deadlines WHERE form_id = :form_id ORDER BY deadline_type"),
            {"form_id": form_id}
        )
        return [
            Deadline(
                id=row[0],
                deadline_type=row[1],
                deadline_date=date.fromisoformat(row[2])
            ) for row in result.fetchall()
        ]

# Endpoint: Update a form deadline
@app.put("/deadlines/{deadline_id}", response_model=Deadline)
def update_form_deadline(
    deadline_id: int,
    deadline_update: DeadlineUpdate,
    db = get_db()
):
    # Validate deadline exists
    result = db.execute(text("SELECT id FROM form_deadlines WHERE id = :deadline_id"), {"deadline_id": deadline_id})
    if not result.fetchone():
        raise HTTPException(status_code=404, detail="Deadline not found")
    
    # Update deadline
    stmt = text("""
        UPDATE form_deadlines 
        SET deadline_date = :deadline_date
        WHERE id = :deadline_id
        RETURNING id, form_id, deadline_type, deadline_date
    """)
    
    params = {
        "deadline_date": deadline_update.deadline_date.isoformat(),
        "deadline_id": deadline_id
    }
    
    result = db.execute(stmt, params)
    db.commit()
    
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=500, detail="Failed to update deadline")
    
    return {
        "id": row[0],
        "form_id": row[1],
        "deadline_type": row[2],
        "deadline_date": date.fromisoformat(row[3])
    }

# Main execution logic for task implementation
@app.on_event("startup")
async def startup_event():
    # Find the form ID for "Fall 2025 Undergraduate Admissions"
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id FROM forms WHERE name = :name"),
            {"name": "Fall 2025 Undergraduate Admissions"}
        )
        form_row = result.fetchone()
        if not form_row:
            raise HTTPException(status_code=500, detail="Form not found")
        
        form_id = form_row[0]
        
        # Step 1: Create 'Extracurricular Activities' section
        section_data = SectionCreate(
            name="Extracurricular Activities",
            description="Details about your extracurricular activities",
            is_optional=True,
            max_entries=5,
            display_order=3
        )
        
        # Insert section
        stmt = text("""
            INSERT INTO form_sections (form_id, name, description, is_optional, max_entries, display_order)
            VALUES (:form_id, :name, :description, :is_optional, :max_entries, :display_order)
            RETURNING id
        """)
        
        params = {
            "form_id": form_id,
            "name": section_data.name,
            "description": section_data.description,
            "is_optional": 1 if section_data.is_optional else 0,
            "max_entries": section_data.max_entries,
            "display_order": section_data.display_order
        }
        
        result = conn.execute(stmt, params)
        conn.commit()
        section_id = result.fetchone()[0]
        
        # Step 2: Add fields to the section
        fields = [
            ("activity_name", "Activity Name", "text", True),
            ("role", "Role", "text", True),
            ("hours_per_week", "Hours Per Week", "number", True),
            ("description", "Description", "textarea", False)
        ]
        
        for field_name, field_label, field_type, is_required in fields:
            stmt = text("""
                INSERT INTO section_fields (section_id, field_name, field_label, field_type, is_required, display_order)
                VALUES (:section_id, :field_name, :field_label, :field_type, :is_required, :display_order)
                RETURNING id
            """)
            
            params = {
                "section_id": section_id,
                "field_name": field_name,
                "field_label": field_label,
                "field_type": field_type,
                "is_required": 1 if is_required else 0,
                "display_order": fields.index((field_name, field_label, field_type, is_required))
            }
            
            conn.execute(stmt, params)
            conn.commit()
        
        # Step 3: Find the question ID for "Are you applying for need-based aid?"
        result = conn.execute(
            text("SELECT id FROM form_questions WHERE form_id = :form_id AND question_text = :question_text"),
            {"form_id": form_id, "question_text": "Are you applying for need-based aid?"}
        )
        question_row = result.fetchone()
        if not question_row:
            raise HTTPException(status_code=500, detail="Need-based aid question not found")
        
        question_id = question_row[0]
        
        # Step 4: Add the Financial Aid Statement document
        doc_data = DocumentCreate(
            name="Financial Aid Statement",
            description="A statement regarding your financial situation for need-based aid consideration.",
            is_required=True,
            condition_question_id=question_id,
            condition_value="Yes"
        )
        
        stmt = text("""
            INSERT INTO form_documents (form_id, name, description, is_required, condition_question_id, condition_value)
            VALUES (:form_id, :name, :description, :is_required, :condition_question_id, :condition_value)
            RETURNING id
        """)
        
        params = {
            "form_id": form_id,
            "name": doc_data.name,
            "description": doc_data.description,
            "is_required": 1 if doc_data.is_required else 0,
            "condition_question_id": doc_data.condition_question_id,
            "condition_value": doc_data.condition_value
        }
        
        conn.execute(stmt, params)
        conn.commit()
        
        # Step 5: Update the regular decision deadline to January 5, 2025
        result = conn.execute(
            text("SELECT id FROM form_deadlines WHERE form_id = :form_id AND deadline_type = 'regular_decision'"),
            {"form_id": form_id}
        )
        deadline_row = result.fetchone()
        if not deadline_row:
            raise HTTPException(status_code=500, detail="Regular decision deadline not found")
        
        deadline_id = deadline_row[0]
        
        stmt = text("""
            UPDATE form_deadlines 
            SET deadline_date = :deadline_date
            WHERE id = :deadline_id
        """)
        
        params = {
            "deadline_date": "2025-01-05",
            "deadline_id": deadline_id
        }
        
        conn.execute(stmt, params)
        conn.commit()
