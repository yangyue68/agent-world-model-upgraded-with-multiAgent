import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, ForeignKey, CheckConstraint, func, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    email = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

class ApplicationForm(Base):
    __tablename__ = "application_forms"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(Text)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    application_fee_cents = Column(Integer, nullable=False, default=0)
    deadline = Column(DateTime)
    status = Column(String, nullable=False, default="draft")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    # Relationships
    sections = relationship("FormSection", back_populates="form", cascade="all, delete-orphan")
    requirements = relationship("FormRequirement", back_populates="form", cascade="all, delete-orphan")

class FormSection(Base):
    __tablename__ = "form_sections"
    id = Column(Integer, primary_key=True, index=True)
    form_id = Column(Integer, ForeignKey("application_forms.id"), nullable=False)
    title = Column(String, nullable=False)
    position = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    # Relationships
    form = relationship("ApplicationForm", back_populates="sections")
    fields = relationship("FormField", back_populates="section", cascade="all, delete-orphan")

class FormField(Base):
    __tablename__ = "form_fields"
    id = Column(Integer, primary_key=True, index=True)
    section_id = Column(Integer, ForeignKey("form_sections.id"), nullable=False)
    label = Column(String, nullable=False)
    field_type = Column(String, nullable=False)
    is_required = Column(Integer, nullable=False, default=0)
    position = Column(Integer, nullable=False, default=0)
    placeholder = Column(String)
    help_text = Column(String)
    validation_rules = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    # Relationships
    section = relationship("FormSection", back_populates="fields")

class FormRequirement(Base):
    __tablename__ = "form_requirements"
    id = Column(Integer, primary_key=True, index=True)
    form_id = Column(Integer, ForeignKey("application_forms.id"), nullable=False)
    requirement_type = Column(String, nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text)
    is_required = Column(Integer, nullable=False, default=1)
    allowed_file_types = Column(String)
    max_file_size_bytes = Column(Integer)
    recommendation_count = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    # Relationships
    form = relationship("ApplicationForm", back_populates="requirements")

# Pydantic models
from typing import Literal

class FormFieldRequest(BaseModel):
    label: str
    field_type: Literal["text", "email", "date", "number", "textarea", "file", "recommendation_request"]
    is_required: bool
    position: int
    placeholder: Optional[str] = None
    help_text: Optional[str] = None

class FormSectionRequest(BaseModel):
    title: str
    position: int
    fields: List[FormFieldRequest]

class FormRequirementRequest(BaseModel):
    requirement_type: Literal["document_upload", "recommendation_request"]
    title: str
    description: Optional[str] = None
    is_required: bool
    allowed_file_types: Optional[str] = None
    max_file_size_bytes: Optional[int] = None
    recommendation_count: Optional[int] = None

class CreateApplicationFormRequest(BaseModel):
    name: str
    description: Optional[str] = None
    application_fee_cents: int
    deadline: Optional[str] = None
    sections: List[FormSectionRequest]
    requirements: Optional[List[FormRequirementRequest]] = None

class CreateApplicationFormResponse(BaseModel):
    id: int
    name: str
    status: str
    created_at: str

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/application-forms", response_model=CreateApplicationFormResponse, operation_id="create_application_form")
def create_application_form(form_data: CreateApplicationFormRequest):
    db = next(get_db())
    
    # Parse deadline with flexible formats
    deadline = None
    if form_data.deadline:
        try:
            # Try parsing with T separator
            deadline = datetime.fromisoformat(form_data.deadline.replace('Z', '+00:00'))
        except ValueError:
            # Fallback to space separator
            deadline = datetime.strptime(form_data.deadline, "%Y-%m-%d %H:%M:%S")
    
    # Create the form
    form = ApplicationForm(
        name=form_data.name,
        description=form_data.description,
        created_by=1,  # Default user ID
        application_fee_cents=form_data.application_fee_cents,
        deadline=deadline,
        status="draft"
    )
    db.add(form)
    db.flush()  # Get the ID without committing
    
    # Create sections
    for section_data in form_data.sections:
        section = FormSection(
            form_id=form.id,
            title=section_data.title,
            position=section_data.position
        )
        db.add(section)
        db.flush()
        
        # Create fields
        for field_data in section_data.fields:
            field = FormField(
                section_id=section.id,
                label=field_data.label,
                field_type=field_data.field_type,
                is_required=int(field_data.is_required),
                position=field_data.position,
                placeholder=field_data.placeholder,
                help_text=field_data.help_text
            )
            db.add(field)
    
    # Create requirements
    if form_data.requirements:
        for req_data in form_data.requirements:
            requirement = FormRequirement(
                form_id=form.id,
                requirement_type=req_data.requirement_type,
                title=req_data.title,
                description=req_data.description,
                is_required=int(req_data.is_required),
                allowed_file_types=req_data.allowed_file_types,
                max_file_size_bytes=req_data.max_file_size_bytes,
                recommendation_count=req_data.recommendation_count or 1
            )
            db.add(requirement)
    
    db.commit()
    db.refresh(form)
    
    return CreateApplicationFormResponse(
        id=form.id,
        name=form.name,
        status=form.status,
        created_at=form.created_at.isoformat()
    )
