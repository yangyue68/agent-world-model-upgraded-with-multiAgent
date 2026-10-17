import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="CareTeam Collaboration Workspace API")


class CarePlanResponse(BaseModel):
    id: int
    title: str


class DocumentUploadResponse(BaseModel):
    id: int
    care_plan_id: int
    document_type_id: int
    encounter_date: str
    responsible_provider_id: Optional[int]
    file_name: str
    file_path: str
    file_size: Optional[int]
    mime_type: Optional[str]
    uploaded_by: int
    created_at: str
    updated_at: str


@app.get("/care_plans", response_model=list[CarePlanResponse], operation_id="find_care_plans")
def find_care_plans(
    title: str = Query(..., description="Title of the care plan"),
    patient_mrn: str = Query(..., description="Medical Record Number of the patient"),
):
    """
    Search for care plans by their title and associated patient MRN.
    Returns matching care plan IDs and titles.
    """
    db = SessionLocal()
    try:
        # Join patients to filter by MRN, then filter by title
        query = text("""
            SELECT cp.id, cp.title
            FROM care_plans cp
            JOIN patients p ON cp.patient_id = p.id
            WHERE cp.title = :title AND p.mrn = :mrn
        """)
        result = db.execute(query, {"title": title, "mrn": patient_mrn})
        rows = result.fetchall()
        
        return [CarePlanResponse(id=row[0], title=row[1]) for row in rows]
    finally:
        db.close()


@app.post("/care_plans/{care_plan_id}/documents", response_model=DocumentUploadResponse, operation_id="upload_discharge_summary")
def upload_discharge_summary(
    care_plan_id: int,
    document_type_name: str = Query(..., description="Name of the document type (e.g., 'Discharge Summary')"),
    encounter_date: str = Query(..., description="Date of the encounter related to the document (YYYY-MM-DD)"),
    responsible_provider_name: str = Query(..., description="Full name of the responsible provider (e.g., 'Dr. A. Reynolds (PCP)')"),
    file_name: str = Query(..., description="Name of the uploaded file"),
    file_path: str = Query(..., description="Path where the file is stored on the server"),
    file_size: Optional[int] = Query(None, description="Size of the uploaded file in bytes"),
    mime_type: Optional[str] = Query(None, description="MIME type of the uploaded file"),
):
    """
    Upload a PDF document to a specified care plan, tagging it with document type, 
    encounter date, and responsible provider.
    """
    db = SessionLocal()
    try:
        # 1. Validate that the care plan exists
        cp_query = text("SELECT id FROM care_plans WHERE id = :cp_id")
        cp_result = db.execute(cp_query, {"cp_id": care_plan_id}).fetchone()
        if not cp_result:
            raise HTTPException(status_code=404, detail=f"Care plan {care_plan_id} not found.")

        # 2. Resolve document_type_id from name
        dt_query = text("SELECT id FROM document_types WHERE name = :dt_name")
        dt_result = db.execute(dt_query, {"dt_name": document_type_name}).fetchone()
        if not dt_result:
            raise HTTPException(status_code=404, detail=f"Document type '{document_type_name}' not found.")
        document_type_id = dt_result[0]

        # 3. Resolve responsible_provider_id from name
        prov_query = text("SELECT id FROM providers WHERE name = :prov_name")
        prov_result = db.execute(prov_query, {"prov_name": responsible_provider_name}).fetchone()
        if not prov_result:
            raise HTTPException(status_code=404, detail=f"Provider '{responsible_provider_name}' not found.")
        responsible_provider_id = prov_result[0]

        # 4. Get current user ID from environment
        current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
        if not current_user_id_str:
            raise HTTPException(status_code=401, detail="Current user context missing.")
        try:
            uploaded_by = int(current_user_id_str)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid current user ID format.")

        # Verify user exists to satisfy FK constraint
        user_check = db.execute(text("SELECT id FROM users WHERE id = :uid"), {"uid": uploaded_by}).fetchone()
        if not user_check:
             raise HTTPException(status_code=400, detail="Uploaded by user does not exist.")

        # 5. Insert new document record
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        insert_query = text("""
            INSERT INTO documents (
                care_plan_id,
                document_type_id,
                encounter_date,
                responsible_provider_id,
                file_name,
                file_path,
                file_size,
                mime_type,
                uploaded_by,
                created_at,
                updated_at
            ) VALUES (
                :cp_id,
                :dt_id,
                :enc_date,
                :prov_id,
                :f_name,
                :f_path,
                :f_size,
                :m_type,
                :u_by,
                :c_at,
                :u_at
            ) RETURNING id
        """)
        
        params = {
            "cp_id": care_plan_id,
            "dt_id": document_type_id,
            "enc_date": encounter_date,
            "prov_id": responsible_provider_id,
            "f_name": file_name,
            "f_path": file_path,
            "f_size": file_size,
            "m_type": mime_type,
            "u_by": uploaded_by,
            "c_at": now_iso,
            "u_at": now_iso,
        }
        
        res = db.execute(insert_query, params)
        new_doc_id = res.fetchone()[0]
        db.commit()

        # 6. Fetch the created record to return full details including timestamps generated by DB if any
        # Although we inserted them explicitly, fetching ensures consistency with schema defaults/types
        fetch_query = text("""
            SELECT id, care_plan_id, document_type_id, encounter_date, responsible_provider_id,
                   file_name, file_path, file_size, mime_type, uploaded_by, created_at, updated_at
            FROM documents WHERE id = :doc_id
        """)
        doc_row = db.execute(fetch_query, {"doc_id": new_doc_id}).fetchone()
        
        if not doc_row:
            raise HTTPException(status_code=500, detail="Failed to retrieve created document.")

        return DocumentUploadResponse(
            id=doc_row[0],
            care_plan_id=doc_row[1],
            document_type_id=doc_row[2],
            encounter_date=str(doc_row[3]),
            responsible_provider_id=doc_row[4],
            file_name=doc_row[5],
            file_path=doc_row[6],
            file_size=doc_row[7],
            mime_type=doc_row[8],
            uploaded_by=doc_row[9],
            created_at=str(doc_row[10]),
            updated_at=str(doc_row[11])
        )

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
