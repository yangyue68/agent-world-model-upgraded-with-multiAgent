import os
from datetime import datetime

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class CreateApplicationResponse(BaseModel):
    application_id: int


class UploadDocumentResponse(BaseModel):
    document_id: int
    file_path: str


class SubmitEssayResponse(BaseModel):
    essay_id: int


class SubmitApplicationResponse(BaseModel):
    status: str


@app.post("/applications", response_model=CreateApplicationResponse, operation_id="create_application")
def create_application():
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    now = datetime.utcnow().isoformat(sep=' ', timespec='seconds')
    with engine.begin() as conn:
        result = conn.execute(
            text("""
                INSERT INTO applications (user_id, status, created_at, updated_at)
                VALUES (:user_id, 'draft', :now, :now)
            """),
            {"user_id": user_id, "now": now}
        )
        application_id = result.lastrowid

    return CreateApplicationResponse(application_id=application_id)


@app.post("/applications/{application_id}/documents", response_model=UploadDocumentResponse, operation_id="upload_document")
def upload_document(
    application_id: int,
    document_type: str = Query(..., description="Type of document (resume or transcript)")
):
    if document_type not in ("resume", "transcript"):
        raise HTTPException(status_code=400, detail="Invalid document type. Must be 'resume' or 'transcript'.")

    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    with engine.begin() as conn:
        # Verify the application exists and belongs to the current user
        app_row = conn.execute(
            text("SELECT id FROM applications WHERE id = :app_id AND user_id = :user_id"),
            {"app_id": application_id, "user_id": user_id}
        ).fetchone()
        if not app_row:
            raise HTTPException(status_code=404, detail="Application not found or access denied")

        # Check if a document of this type already exists for this application
        existing_doc = conn.execute(
            text("SELECT id FROM application_documents WHERE application_id = :app_id AND document_type = :doc_type"),
            {"app_id": application_id, "doc_type": document_type}
        ).fetchone()
        if existing_doc:
            raise HTTPException(status_code=409, detail=f"Document of type '{document_type}' already uploaded for this application.")

        # Simulate file path and size
        file_path = f"path/to/{document_type}_{application_id}.pdf"
        file_size = 123456  # simulated size
        now = datetime.utcnow().isoformat(sep=' ', timespec='seconds')

        result = conn.execute(
            text("""
                INSERT INTO application_documents (application_id, document_type, file_path, file_size, uploaded_at)
                VALUES (:app_id, :doc_type, :file_path, :file_size, :now)
            """),
            {
                "app_id": application_id,
                "doc_type": document_type,
                "file_path": file_path,
                "file_size": file_size,
                "now": now
            }
        )
        document_id = result.lastrowid

    return UploadDocumentResponse(document_id=document_id, file_path=file_path)


@app.put("/applications/{application_id}/essays", response_model=SubmitEssayResponse, operation_id="submit_essay")
def submit_essay(application_id: int):
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    question = "Why do you want to pursue this degree?"
    content = "I am passionate about leveraging data to drive meaningful insights and solve real-world problems. Pursuing this degree will equip me with advanced skills in machine learning, statistical analysis, and data engineering, enabling me to contribute effectively to the field of data science."

    with engine.begin() as conn:
        # Verify the application exists and belongs to the current user
        app_row = conn.execute(
            text("SELECT id FROM applications WHERE id = :app_id AND user_id = :user_id"),
            {"app_id": application_id, "user_id": user_id}
        ).fetchone()
        if not app_row:
            raise HTTPException(status_code=404, detail="Application not found or access denied")

        # Check if an essay already exists for this application
        existing_essay = conn.execute(
            text("SELECT id FROM application_essays WHERE application_id = :app_id"),
            {"app_id": application_id}
        ).fetchone()
        if existing_essay:
            raise HTTPException(status_code=409, detail="Essay already submitted for this application.")

        now = datetime.utcnow().isoformat(sep=' ', timespec='seconds')
        result = conn.execute(
            text("""
                INSERT INTO application_essays (application_id, question, content, submitted_at)
                VALUES (:app_id, :question, :content, :now)
            """),
            {
                "app_id": application_id,
                "question": question,
                "content": content,
                "now": now
            }
        )
        essay_id = result.lastrowid

    return SubmitEssayResponse(essay_id=essay_id)


@app.post("/applications/{application_id}/submit", response_model=SubmitApplicationResponse, operation_id="submit_application")
def submit_application(application_id: int):
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    with engine.begin() as conn:
        # Verify the application exists and belongs to the current user
        app_row = conn.execute(
            text("SELECT id, status FROM applications WHERE id = :app_id AND user_id = :user_id"),
            {"app_id": application_id, "user_id": user_id}
        ).fetchone()
        if not app_row:
            raise HTTPException(status_code=404, detail="Application not found or access denied")

        current_status = app_row[1]
        if current_status != "draft":
            raise HTTPException(status_code=400, detail=f"Cannot submit application with status '{current_status}'. Only draft applications can be submitted.")

        # Update status to submitted
        now = datetime.utcnow().isoformat(sep=' ', timespec='seconds')
        conn.execute(
            text("""
                UPDATE applications
                SET status = 'submitted', updated_at = :now
                WHERE id = :app_id
            """),
            {"app_id": application_id, "now": now}
        )

    return SubmitApplicationResponse(status="submitted")
