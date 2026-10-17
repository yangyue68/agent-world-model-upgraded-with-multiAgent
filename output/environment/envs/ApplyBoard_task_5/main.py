import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class CreateFormFieldRequest(BaseModel):
    field_name: str
    field_type: str
    is_required: bool


class FormFieldResponse(BaseModel):
    id: int
    form_id: int
    field_name: str
    field_type: str
    is_required: bool


class CreateDropdownOptionRequest(BaseModel):
    option_value: str


class DropdownOptionResponse(BaseModel):
    id: int
    field_id: int
    option_value: str


@app.post("/application_forms/{form_id}/fields", response_model=FormFieldResponse, operation_id="create_form_field")
def create_form_field(
    form_id: int = Path(..., description="ID of the application form to add the field to"),
    request: CreateFormFieldRequest = Body(...)
):
    """
    Adds a new field to an existing application form. The field must be of type 'dropdown' and marked as required.
    """
    # Validate that field_type is 'dropdown' as per spec description
    if request.field_type != "dropdown":
        raise HTTPException(status_code=400, detail="Field type must be 'dropdown'")
    
    # Validate that is_required is True as per spec description
    if not request.is_required:
        raise HTTPException(status_code=400, detail="Field must be marked as required")

    db = SessionLocal()
    try:
        # Check if form exists
        form_exists = db.execute(text("SELECT 1 FROM application_forms WHERE id = :id"), {"id": form_id}).fetchone()
        if not form_exists:
            raise HTTPException(status_code=404, detail="Application form not found")

        # Insert new field
        result = db.execute(
            text("""
                INSERT INTO form_fields (form_id, field_name, field_type, is_required)
                VALUES (:form_id, :field_name, :field_type, :is_required)
            """),
            {
                "form_id": form_id,
                "field_name": request.field_name,
                "field_type": request.field_type,
                "is_required": request.is_required
            }
        )
        db.commit()
        
        new_id = result.lastrowid
        
        # Fetch the created record to return it
        row = db.execute(
            text("SELECT id, form_id, field_name, field_type, is_required FROM form_fields WHERE id = :id"),
            {"id": new_id}
        ).fetchone()
        
        if not row:
            raise HTTPException(status_code=500, detail="Failed to retrieve created field")
            
        return FormFieldResponse(
            id=row[0],
            form_id=row[1],
            field_name=row[2],
            field_type=row[3],
            is_required=bool(row[4])
        )
    finally:
        db.close()


@app.post("/form_fields/{field_id}/dropdown_options", response_model=DropdownOptionResponse, operation_id="create_dropdown_option")
def create_dropdown_option(
    field_id: int = Path(..., description="ID of the dropdown field to add the option to"),
    request: CreateDropdownOptionRequest = Body(...)
):
    """
    Creates a new dropdown option for a specified dropdown field.
    """
    db = SessionLocal()
    try:
        # Check if field exists and is a dropdown
        field_row = db.execute(
            text("SELECT id, field_type FROM form_fields WHERE id = :id"),
            {"id": field_id}
        ).fetchone()
        
        if not field_row:
            raise HTTPException(status_code=404, detail="Form field not found")
        
        if field_row[1] != "dropdown":
            raise HTTPException(status_code=400, detail="Field is not a dropdown type")

        # Insert new option
        result = db.execute(
            text("""
                INSERT INTO dropdown_options (field_id, option_value)
                VALUES (:field_id, :option_value)
            """),
            {
                "field_id": field_id,
                "option_value": request.option_value
            }
        )
        db.commit()
        
        new_id = result.lastrowid
        
        # Fetch the created record to return it
        row = db.execute(
            text("SELECT id, field_id, option_value FROM dropdown_options WHERE id = :id"),
            {"id": new_id}
        ).fetchone()
        
        if not row:
            raise HTTPException(status_code=500, detail="Failed to retrieve created option")
            
        return DropdownOptionResponse(
            id=row[0],
            field_id=row[1],
            option_value=row[2]
        )
    finally:
        db.close()
