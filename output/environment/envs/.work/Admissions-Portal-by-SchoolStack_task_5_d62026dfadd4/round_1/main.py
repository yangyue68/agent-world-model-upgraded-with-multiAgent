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

# Pydantic models for request/response
class Form(BaseModel):
    id: int
    name: str

class SectionCreate(BaseModel):
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

class FieldCreate(BaseModel):
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

class DocumentResponse(BaseModel):
    id: int
    form_id: int
    name: str
    is_required: bool
    condition_question_id: Optional[int] = None
    condition_value: Optional[str] = None

class Deadline(BaseModel):
    id: int
    deadline_type: str
    deadline_date: date

class DeadlineResponse(BaseModel):
    id: int
    form_id: int
    deadline_type: str
    deadline_date: date

class DeadlineUpdate(BaseModel):
    deadline_date: date

# Helper function to get database session
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
@app.post("/forms/{form_id}/sections", response_model=SectionResponse)
def create_form_section(
    form_id: int,
    section: SectionCreate
):
    with engine.connect() as conn:
        # Check if form exists
        result = conn.execute(text("SELECT id FROM forms WHERE id = :form_id"), {"form_id": form_id})
        if not result.fetchone():
            raise HTTPException(status_code=404, detail="Form not found")

        # Insert new section
        result = conn.execute(
            text("""
                INSERT INTO form_sections 
                (form_id, name, description, is_optional, max_entries, display_order)
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
        )
        row = result.fetchone()
        conn.commit()
        
        return SectionResponse(
            id=row[0],
            form_id=row[1],
            name=row[2],
            is_optional=bool(row[3]),
            max_entries=row[4]
        )

# Endpoint: Add a field to a section
@app.post("/sections/{section_id}/fields", response_model=FieldResponse)
def create_section_field(
    section_id: int,
    field: FieldCreate
):
    with engine.connect() as conn:
        # Check if section exists
        result = conn.execute(text("SELECT id FROM form_sections WHERE id = :section_id"), {"section_id": section_id})
        if not result.fetchone():
            raise HTTPException(status_code=404, detail="Section not found")

        # Insert new field
        result = conn.execute(
            text("""
                INSERT INTO section_fields 
                (section_id, field_name, field_label, field_type, is_required, display_order)
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
        )
        row = result.fetchone()
        conn.commit()
        
        return FieldResponse(
            id=row[0],
            section_id=row[1],
            field_name=row[2],
            field_label=row[3],
            field_type=row[4],
            is_required=bool(row[5])
        )

# Endpoint: Get questions for a form
@app.get("/forms/{form_id}/questions", response_model=List[Question])
def get_form_questions(form_id: int):
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT id, question_text, question_type, is_required 
                FROM form_questions 
                WHERE form_id = :form_id
                ORDER BY display_order
            """),
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
@app.post("/forms/{form_id}/documents", response_model=DocumentResponse)
def create_form_document(
    form_id: int,
    document: DocumentCreate
):
    with engine.connect() as conn:
        # Check if form exists
        result = conn.execute(text("SELECT id FROM forms WHERE id = :form_id"), {"form_id": form_id})
        if not result.fetchone():
            raise HTTPException(status_code=404, detail="Form not found")

        # Insert new document
        result = conn.execute(
            text("""
                INSERT INTO form_documents 
                (form_id, name, description, is_required, condition_question_id, condition_value)
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
        )
        row = result.fetchone()
        conn.commit()
        
        return DocumentResponse(
            id=row[0],
            form_id=row[1],
            name=row[2],
            is_required=bool(row[3]),
            condition_question_id=row[4],
            condition_value=row[5]
        )

# Endpoint: Get deadlines for a form
@app.get("/forms/{form_id}/deadlines", response_model=List[Deadline])
def get_form_deadlines(form_id: int):
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT id, deadline_type, deadline_date 
                FROM form_deadlines 
                WHERE form_id = :form_id
                ORDER BY deadline_type
            """),
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
@app.put("/deadlines/{deadline_id}", response_model=DeadlineResponse)
def update_form_deadline(
    deadline_id: int,
    deadline_update: DeadlineUpdate
):
    with engine.connect() as conn:
        # Check if deadline exists
        result = conn.execute(text("SELECT id FROM form_deadlines WHERE id = :deadline_id"), {"deadline_id": deadline_id})
        if not result.fetchone():
            raise HTTPException(status_code=404, detail="Deadline not found")

        # Update deadline
        result = conn.execute(
            text("""
                UPDATE form_deadlines 
                SET deadline_date = :deadline_date
                WHERE id = :deadline_id
                RETURNING id, form_id, deadline_type, deadline_date
            """),
            {
                "deadline_date": deadline_update.deadline_date.isoformat(),
                "deadline_id": deadline_id
            }
        )
        row = result.fetchone()
        conn.commit()
        
        return DeadlineResponse(
            id=row[0],
            form_id=row[1],
            deadline_type=row[2],
            deadline_date=date.fromisoformat(row[3])
        )

# Main task implementation
@app.post("/task_5", status_code=200)
def execute_task_5():
    """
    Execute the task requirements:
    1. Add 'Extracurricular Activities' section with 5 max entries
    2. Add required 'Financial Aid Statement' document conditional on need-based aid question
    3. Update regular decision deadline to Jan 5, 2025
    """
    with engine.connect() as conn:
        # Step 1: Find the form ID for "Fall 2025 Undergraduate Admissions"
        result = conn.execute(
            text("SELECT id FROM forms WHERE name = :name"),
            {"name": "Fall 2025 Undergraduate Admissions"}
        )
        form_row = result.fetchone()
        if not form_row:
            raise HTTPException(status_code=404, detail="Form not found")
        form_id = form_row[0]

        # Step 2: Create the 'Extracurricular Activities' section
        section_result = conn.execute(
            text("""
                INSERT INTO form_sections 
                (form_id, name, description, is_optional, max_entries, display_order)
                VALUES (:form_id, :name, :description, :is_optional, :max_entries, :display_order)
                RETURNING id
            """),
            {
                "form_id": form_id,
                "name": "Extracurricular Activities",
                "description": "List your extracurricular activities including roles and time commitment.",
                "is_optional": 1,
                "max_entries": 5,
                "display_order": 3
            }
        )
        section_id = section_result.fetchone()[0]
        conn.commit()

        # Step 3: Add fields to the new section
        fields_data = [
            ("activity_name", "Activity Name", "text", 1),
            ("role", "Role", "text", 1),
            ("hours_per_week", "Hours Per Week", "number", 1),
            ("description", "Description", "textarea", 0)
        ]
        
        for i, (field_name, field_label, field_type, is_required) in enumerate(fields_data):
            conn.execute(
                text("""
                    INSERT INTO section_fields 
                    (section_id, field_name, field_label, field_type, is_required, display_order)
                    VALUES (:section_id, :field_name, :field_label, :field_type, :is_required, :display_order)
                """),
                {
                    "section_id": section_id,
                    "field_name": field_name,
                    "field_label": field_label,
                    "field_type": field_type,
                    "is_required": 1 if is_required else 0,
                    "display_order": i + 1
                }
            )
        conn.commit()

        # Step 4: Find the question ID for "Are you applying for need-based aid?"
        result = conn.execute(
            text("SELECT id FROM form_questions WHERE question_text = :question_text AND form_id = :form_id"),
            {"question_text": "Are you applying for need-based aid?", "form_id": form_id}
        )
        question_row = result.fetchone()
        if not question_row:
            raise HTTPException(status_code=404, detail="Need-based aid question not found")
        question_id = question_row[0]

        # Step 5: Add the 'Financial Aid Statement' document
        conn.execute(
            text("""
                INSERT INTO form_documents 
                (form_id, name, description, is_required, condition_question_id, condition_value)
                VALUES (:form_id, :name, :description, :is_required, :condition_question_id, :condition_value)
            """),
            {
                "form_id": form_id,
                "name": "Financial Aid Statement",
                "description": "A statement explaining your financial situation for need-based aid consideration.",
                "is_required": 1,
                "condition_question_id": question_id,
                "condition_value": "Yes"
            }
        )
        conn.commit()

        # Step 6: Update the regular decision deadline to January 5, 2025
        result = conn.execute(
            text("SELECT id FROM form_deadlines WHERE form_id = :form_id AND deadline_type = 'regular_decision'"),
            {"form_id": form_id}
        )
        deadline_row = result.fetchone()
        if not deadline_row:
            raise HTTPException(status_code=404, detail="Regular decision deadline not found")
        deadline_id = deadline_row[0]

        conn.execute(
            text("UPDATE form_deadlines SET deadline_date = :deadline_date WHERE id = :deadline_id"),
            {
                "deadline_date": "2025-01-05",
                "deadline_id": deadline_id
            }
        )
        conn.commit()

    return {"message": "Task completed successfully"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
