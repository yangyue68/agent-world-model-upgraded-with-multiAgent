import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class CreateApplicationRequest(BaseModel):
    scholarship_name: str
    high_school: str
    major: str
    gpa: float


class UpdateApplicationRequest(BaseModel):
    essay: str
    transcript_file: str


class ApplicationResponse(BaseModel):
    id: int
    user_id: int
    scholarship_name: str
    status: str
    created_at: str
    updated_at: str


class UpdatedApplicationResponse(BaseModel):
    id: int
    user_id: int
    scholarship_name: str
    status: str
    essay: str
    transcript_file: str
    updated_at: str


def get_current_user_id() -> int:
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")


@app.post("/applications", response_model=ApplicationResponse, operation_id="create_application")
def create_application(request: CreateApplicationRequest):
    user_id = get_current_user_id()
    
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc).isoformat()
        
        # Insert new application with default values for essay and transcript_file as they are NOT NULL in schema
        # The spec says these are filled in later via PUT, but DB requires them. 
        # We insert empty string and empty bytes (or placeholder) to satisfy NOT NULL constraint initially?
        # Looking at schema: essay TEXT NOT NULL, transcript_file BLOB NOT NULL.
        # However, the task is to "Draft an application... fill in all required profile fields... upload a PDF... write essay... save as draft".
        # The API spec splits this into POST (profile fields) and PUT (essay/transcript).
        # If we strictly follow the POST endpoint definition, it only takes profile fields.
        # But the DB requires essay and transcript_file to be non-null.
        # Let's check the initial snapshot. Rows have data.
        # If I create a row via POST, I must provide something for essay and transcript_file or the insert will fail.
        # The prompt says "fill in all required profile fields" in the task description, but the API spec for POST only lists scholarship_name, high_school, major, gpa.
        # It implies the POST creates the shell.
        # To make the POST work against the strict NOT NULL constraints, we might need to insert dummy values or assume the schema allows defaults? No, no defaults listed for essay/transcript.
        # Wait, looking at the DDL: `essay TEXT NOT NULL, transcript_file BLOB NOT NULL`.
        # If the POST endpoint doesn't accept them, how can it succeed?
        # Perhaps the intention is that the POST creates the record with empty strings/bytes?
        # Or maybe the task description implies doing everything in one go? No, there are two endpoints.
        # Let's look at the example policy. It's illustrative.
        # I will insert empty string for essay and empty bytes for transcript_file during POST to satisfy NOT NULL, then update them in PUT.
        
        stmt = text("""
            INSERT INTO scholarship_applications (user_id, scholarship_name, status, essay, transcript_file, high_school, major, gpa, created_at, updated_at)
            VALUES (:user_id, :scholarship_name, 'draft', '', X'', :high_school, :major, :gpa, :created_at, :updated_at)
        """)
        
        result = db.execute(stmt, {
            "user_id": user_id,
            "scholarship_name": request.scholarship_name,
            "high_school": request.high_school,
            "major": request.major,
            "gpa": request.gpa,
            "created_at": now,
            "updated_at": now
        })
        db.commit()
        
        app_id = result.lastrowid
        
        # Fetch back to ensure consistency and return correct timestamps
        fetch_stmt = text("""
            SELECT id, user_id, scholarship_name, status, created_at, updated_at 
            FROM scholarship_applications 
            WHERE id = :id
        """)
        row = db.execute(fetch_stmt, {"id": app_id}).fetchone()
        
        if not row:
            raise HTTPException(status_code=500, detail="Failed to retrieve created application")
            
        return ApplicationResponse(
            id=row.id,
            user_id=row.user_id,
            scholarship_name=row.scholarship_name,
            status=row.status,
            created_at=row.created_at.isoformat() if hasattr(row.created_at, 'isoformat') else str(row.created_at),
            updated_at=row.updated_at.isoformat() if hasattr(row.updated_at, 'isoformat') else str(row.updated_at)
        )
    finally:
        db.close()


@app.put("/applications/{application_id}", response_model=UpdatedApplicationResponse, operation_id="update_application")
def update_application(application_id: int = Path(..., description="Internal ID of the application to update"), request: UpdateApplicationRequest = None):
    user_id = get_current_user_id()
    
    db = SessionLocal()
    try:
        # Check if application exists and belongs to user and is in draft status
        check_stmt = text("""
            SELECT id, user_id, status, scholarship_name 
            FROM scholarship_applications 
            WHERE id = :id AND user_id = :user_id
        """)
        existing = db.execute(check_stmt, {"id": application_id, "user_id": user_id}).fetchone()
        
        if not existing:
            raise HTTPException(status_code=404, detail="Application not found")
        
        if existing.status != 'draft':
            raise HTTPException(status_code=400, detail="Can only update applications in draft status")
        
        now = datetime.now(timezone.utc).isoformat()
        
        # Decode base64 transcript file if needed, or store as provided. 
        # Schema says transcript_file is BLOB. Input is base64 string.
        # We should decode it to bytes before storing.
        import base64
        try:
            transcript_bytes = base64.b64decode(request.transcript_file)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid base64 encoding for transcript_file")
            
        update_stmt = text("""
            UPDATE scholarship_applications 
            SET essay = :essay, transcript_file = :transcript_file, updated_at = :updated_at
            WHERE id = :id
        """)
        
        db.execute(update_stmt, {
            "essay": request.essay,
            "transcript_file": transcript_bytes,
            "updated_at": now,
            "id": application_id
        })
        db.commit()
        
        # Fetch updated record
        fetch_stmt = text("""
            SELECT id, user_id, scholarship_name, status, essay, transcript_file, updated_at 
            FROM scholarship_applications 
            WHERE id = :id
        """)
        row = db.execute(fetch_stmt, {"id": application_id}).fetchone()
        
        if not row:
            raise HTTPException(status_code=500, detail="Failed to retrieve updated application")
            
        # Convert blob back to base64 for response? The spec says response has transcript_file as string.
        # Usually APIs return the same format they received or a URL. Given "base64" in input, returning base64 makes sense.
        encoded_transcript = base64.b64encode(row.transcript_file).decode('utf-8')
        
        return UpdatedApplicationResponse(
            id=row.id,
            user_id=row.user_id,
            scholarship_name=row.scholarship_name,
            status=row.status,
            essay=row.essay,
            transcript_file=encoded_transcript,
            updated_at=row.updated_at.isoformat() if hasattr(row.updated_at, 'isoformat') else str(row.updated_at)
        )
    finally:
        db.close()


@app.get("/applications", response_model=list[ApplicationResponse], operation_id="list_applications")
def list_applications():
    user_id = get_current_user_id()
    
    db = SessionLocal()
    try:
        stmt = text("""
            SELECT id, user_id, scholarship_name, status, created_at, updated_at 
            FROM scholarship_applications 
            WHERE user_id = :user_id
            ORDER BY created_at DESC
        """)
        rows = db.execute(stmt, {"user_id": user_id}).fetchall()
        
        results = []
        for row in rows:
            results.append(ApplicationResponse(
                id=row.id,
                user_id=row.user_id,
                scholarship_name=row.scholarship_name,
                status=row.status,
                created_at=row.created_at.isoformat() if hasattr(row.created_at, 'isoformat') else str(row.created_at),
                updated_at=row.updated_at.isoformat() if hasattr(row.updated_at, 'isoformat') else str(row.updated_at)
            ))
            
        return results
    finally:
        db.close()
