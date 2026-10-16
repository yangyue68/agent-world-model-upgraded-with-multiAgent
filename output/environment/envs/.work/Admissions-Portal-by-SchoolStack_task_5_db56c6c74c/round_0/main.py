import os
import re
from datetime import date
from typing import List, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Path, Query
from pydantic import BaseModel
from sqlalchemy import (
    Column,
    DateTime,
    Date,
    Integer,
    String,
    Text,
    Boolean,
    ForeignKey,
    CheckConstraint,
    create_engine,
    func,
    case,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.sql import text
DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class Form(Base):
    __tablename__ = "forms"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(Text)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class FormSection(Base):
    __tablename__ = "form_sections"
    id = Column(Integer, primary_key=True, index=True)
    form_id = Column(Integer, ForeignKey("forms.id"), nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text)
    is_optional = Column(Boolean, nullable=False, default=False, server_default=text("0"))
    max_entries = Column(Integer)
    display_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())
    form = relationship("Form")

class SectionField(Base):
    __tablename__ = "section_fields"
    id = Column(Integer, primary_key=True, index=True)
    section_id = Column(Integer, ForeignKey("form_sections.id"), nullable=False)
    field_name = Column(String, nullable=False)
    field_label = Column(String, nullable=False)
    field_type = Column(String, nullable=False, check=CheckConstraint("field_type IN ('text','textarea','number','date','select','checkbox','radio')"))
    is_required = Column(Boolean, nullable=False, default=False, server_default=text("0"))
    display_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())
    section = relationship("FormSection")

class FormQuestion(Base):
    __tablename__ = "form_questions"
    id = Column(Integer, primary_key=True, index=True)
    form_id = Column(Integer, ForeignKey("forms.id"), nullable=False)
    question_text = Column(Text, nullable=False)
    question_type = Column(String, nullable=False, check=CheckConstraint("question_type IN ('text','textarea','number','date','select','checkbox','radio')"))
    is_required = Column(Boolean, nullable=False, default=False, server_default=text("0"))
    display_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())
    form = relationship("Form")

class FormDocument(Base):
    __tablename__ = "form_documents"
    id = Column(Integer, primary_key=True, index=True)
    form_id = Column(Integer, ForeignKey("forms.id"), nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text)
    is_required = Column(Boolean, nullable=False, default=False, server_default=text("0"))
    condition_question_id = Column(Integer, ForeignKey("form_questions.id"), nullable=True)
    condition_value = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())
    form = relationship("Form")
    condition_question = relationship("FormQuestion")

class FormDeadline(Base):
    __tablename__ = "form_deadlines"
    id = Column(Integer, primary_key=True, index=True)
    form_id = Column(Integer, ForeignKey("forms.id"), nullable=False)
    deadline_type = Column(String, nullable=False, check=CheckConstraint("deadline_type IN ('early_action','regular_decision','priority','final')"))
    deadline_date = Column(Date, nullable=False)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())
    form = relationship("Form")

class Application(Base):
    __tablename__ = "applications"
    id = Column(Integer, primary_key=True, index=True)
    form_id = Column(Integer, ForeignKey("forms.id"), nullable=False)
    applicant_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String, nullable=False, default="draft", check=CheckConstraint("status IN ('draft','submitted','under_review','accepted','rejected','waitlisted')"))
    submitted_at = Column(DateTime)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())
    form = relationship("Form")
    applicant = relationship("User")

class ApplicationAnswer(Base):
    __tablename__ = "application_answers"
    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("applications.id"), nullable=False)
    question_id = Column(Integer, ForeignKey("form_questions.id"), nullable=False)
    answer_value = Column(Text)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())
    application = relationship("Application")
    question = relationship("FormQuestion")

class ApplicationSectionEntry(Base):
    __tablename__ = "application_section_entries"
    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("applications.id"), nullable=False)
    section_id = Column(Integer, ForeignKey("form_sections.id"), nullable=False)
    entry_index = Column(Integer, nullable=False, check=CheckConstraint("entry_index >= 1 AND entry_index <= 5"))
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())
    application = relationship("Application")
    section = relationship("FormSection")

class ApplicationSectionEntryValue(Base):
    __tablename__ = "application_section_entry_values"
    id = Column(Integer, primary_key=True, index=True)
    entry_id = Column(Integer, ForeignKey("application_section_entries.id"), nullable=False)
    field_id = Column(Integer, ForeignKey("section_fields.id"), nullable=False)
    value = Column(Text)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())
    entry = relationship("ApplicationSectionEntry")
    field = relationship("SectionField")

class ApplicationDocument(Base):
    __tablename__ = "application_documents"
    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("applications.id"), nullable=False)
    document_id = Column(Integer, ForeignKey("form_documents.id"), nullable=False)
    file_path = Column(Text, nullable=False)
    uploaded_at = Column(DateTime, default=func.current_timestamp())
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())
    application = relationship("Application")
    document = relationship("FormDocument")

Base.metadata.create_all(bind=engine)

app = FastAPI()

# Pydantic Models
class FormResponse(BaseModel):
    id: int
    name: str

class CreateFormSectionRequest(BaseModel):
    name: str
    description: Optional[str] = None
    is_optional: bool
    max_entries: Optional[int] = None
    display_order: Optional[int] = None

class CreateFormSectionResponse(BaseModel):
    id: int
    form_id: int
    name: str
    is_optional: bool
    max_entries: Optional[int]

class CreateSectionFieldRequest(BaseModel):
    field_name: str
    field_label: str
    field_type: str
    is_required: bool
    display_order: Optional[int] = None

class CreateSectionFieldResponse(BaseModel):
    id: int
    section_id: int
    field_name: str
    field_label: str
    field_type: str
    is_required: bool

class FormQuestionResponse(BaseModel):
    id: int
    question_text: str
    question_type: str
    is_required: bool

class CreateFormDocumentRequest(BaseModel):
    name: str
    description: Optional[str] = None
    is_required: bool
    condition_question_id: Optional[int] = None
    condition_value: Optional[str] = None

class CreateFormDocumentResponse(BaseModel):
    id: int
    form_id: int
    name: str
    is_required: bool
    condition_question_id: Optional[int]
    condition_value: Optional[str]

class FormDeadlineResponse(BaseModel):
    id: int
    deadline_type: str
    deadline_date: date

class UpdateFormDeadlineRequest(BaseModel):
    deadline_date: date

# Helper functions
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def parse_iso_date(date_str: str) -> date:
    # Handle both T...Z and space-separated formats
    if 'T' in date_str:
        date_part = date_str.split('T')[0]
    else:
        date_part = date_str.split()[0]
    return date.fromisoformat(date_part)

# API Endpoints
@app.get("/forms", response_model=List[FormResponse])
def find_forms_by_name(name: str = Query(...), db = Depends(get_db)):
    forms = db.query(Form).filter(Form.name.contains(name)).all()
    return [{"id": f.id, "name": f.name} for f in forms]

@app.post("/forms/{form_id}/sections", response_model=CreateFormSectionResponse)
def create_form_section(
    form_id: int = Path(...),
    section_data: CreateFormSectionRequest = None,
    db = Depends(get_db)
):
    section = FormSection(
        form_id=form_id,
        name=section_data.name,
        description=section_data.description,
        is_optional=section_data.is_optional,
        max_entries=section_data.max_entries,
        display_order=section_data.display_order or 0
    )
    db.add(section)
    db.commit()
    db.refresh(section)
    return {
        "id": section.id,
        "form_id": section.form_id,
        "name": section.name,
        "is_optional": section.is_optional,
        "max_entries": section.max_entries
    }

@app.post("/sections/{section_id}/fields", response_model=CreateSectionFieldResponse)
def create_section_field(
    section_id: int = Path(...),
    field_data: CreateSectionFieldRequest = None,
    db = Depends(get_db)
):
    field = SectionField(
        section_id=section_id,
        field_name=field_data.field_name,
        field_label=field_data.field_label,
        field_type=field_data.field_type,
        is_required=field_data.is_required,
        display_order=field_data.display_order or 0
    )
    db.add(field)
    db.commit()
    db.refresh(field)
    return {
        "id": field.id,
        "section_id": field.section_id,
        "field_name": field.field_name,
        "field_label": field.field_label,
        "field_type": field.field_type,
        "is_required": field.is_required
    }

@app.get("/forms/{form_id}/questions", response_model=List[FormQuestionResponse])
def get_form_questions(
    form_id: int = Path(...),
    db = Depends(get_db)
):
    questions = db.query(FormQuestion).filter(FormQuestion.form_id == form_id).all()
    return [
        {
            "id": q.id,
            "question_text": q.question_text,
            "question_type": q.question_type,
            "is_required": q.is_required
        } for q in questions
    ]

@app.post("/forms/{form_id}/documents", response_model=CreateFormDocumentResponse)
def create_form_document(
    form_id: int = Path(...),
    document_data: CreateFormDocumentRequest = None,
    db = Depends(get_db)
):
    document = FormDocument(
        form_id=form_id,
        name=document_data.name,
        description=document_data.description,
        is_required=document_data.is_required,
        condition_question_id=document_data.condition_question_id,
        condition_value=document_data.condition_value
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return {
        "id": document.id,
        "form_id": document.form_id,
        "name": document.name,
        "is_required": document.is_required,
        "condition_question_id": document.condition_question_id,
        "condition_value": document.condition_value
    }

@app.get("/forms/{form_id}/deadlines", response_model=List[FormDeadlineResponse])
def get_form_deadlines(
    form_id: int = Path(...),
    db = Depends(get_db)
):
    deadlines = db.query(FormDeadline).filter(FormDeadline.form_id == form_id).all()
    return [
        {
            "id": d.id,
            "deadline_type": d.deadline_type,
            "deadline_date": d.deadline_date
        } for d in deadlines
    ]

@app.put("/deadlines/{deadline_id}", response_model=FormDeadlineResponse)
def update_form_deadline(
    deadline_id: int = Path(...),
    deadline_data: UpdateFormDeadlineRequest = None,
    db = Depends(get_db)
):
    deadline = db.query(FormDeadline).filter(FormDeadline.id == deadline_id).first()
    if not deadline:
        raise HTTPException(status_code=404, detail="Deadline not found")
    
    deadline.deadline_date = deadline_data.deadline_date
    db.commit()
    db.refresh(deadline)
    return {
        "id": deadline.id,
        "deadline_type": deadline.deadline_type,
        "deadline_date": deadline.deadline_date
    }

# Main initialization logic
@app.on_event("startup")
def initialize_database():
    db = SessionLocal()
    try:
        # Find the form ID for "Fall 2025 Undergraduate Admissions"
        form = db.query(Form).filter(Form.name == "Fall 2025 Undergraduate Admissions").first()
        if not form:
            raise HTTPException(status_code=404, detail="Form not found")
        
        form_id = form.id
        
        # Step 1: Add the new section "Extracurricular Activities"
        extracurricular_section = FormSection(
            form_id=form_id,
            name="Extracurricular Activities",
            description="Details about your extracurricular activities.",
            is_optional=True,
            max_entries=5,
            display_order=3
        )
        db.add(extracurricular_section)
        db.flush()  # Get the ID without committing yet
        
        # Step 2: Add fields to the new section
        fields_data = [
            ("activity_name", "Activity Name", "text", True),
            ("role", "Role", "text", True),
            ("hours_per_week", "Hours Per Week", "number", True),
            ("description", "Description", "textarea", False)
        ]
        
        for field_name, field_label, field_type, is_required in fields_data:
            field = SectionField(
                section_id=extracurricular_section.id,
                field_name=field_name,
                field_label=field_label,
                field_type=field_type,
                is_required=is_required,
                display_order=["activity_name", "role", "hours_per_week", "description"].index(field_name) + 1
            )
            db.add(field)
        
        # Step 3: Find the question ID for "Are you applying for need-based aid?"
        aid_question = db.query(FormQuestion).filter(
            FormQuestion.form_id == form_id,
            FormQuestion.question_text == "Are you applying for need-based aid?"
        ).first()
        
        if not aid_question:
            raise HTTPException(status_code=404, detail="Need-based aid question not found")
        
        # Step 4: Add the Financial Aid Statement document
        financial_aid_doc = FormDocument(
            form_id=form_id,
            name="Financial Aid Statement",
            description="A statement regarding your financial situation for need-based aid consideration.",
            is_required=True,
            condition_question_id=aid_question.id,
            condition_value="Yes"
        )
        db.add(financial_aid_doc)
        
        # Step 5: Update the regular decision deadline to January 5, 2025
        regular_deadline = db.query(FormDeadline).filter(
            FormDeadline.form_id == form_id,
            FormDeadline.deadline_type == "regular_decision"
        ).first()
        
        if regular_deadline:
            regular_deadline.deadline_date = date(2025, 1, 5)
        
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
