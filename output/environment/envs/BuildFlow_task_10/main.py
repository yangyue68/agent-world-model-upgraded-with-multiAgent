import os
import base64
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Path, Body
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


# --- Pydantic Models for Request/Response ---

class CostCodeResponse(BaseModel):
    id: int
    code: str
    name: str
    description: str

class ProjectPhaseResponse(BaseModel):
    id: int
    project_id: str
    name: str
    description: str
    planned_start_date: Optional[str] = None
    planned_end_date: Optional[str] = None
    actual_start_date: Optional[str] = None
    actual_end_date: Optional[str] = None

class ChangeOrderCreateRequest(BaseModel):
    project_id: str
    title: str
    scope_description: str
    cost_impact: float
    cost_code_id: int
    schedule_impact_days: int
    schedule_impact_phase_id: int

class ChangeOrderResponse(BaseModel):
    id: int
    project_id: str
    title: str
    scope_description: str
    cost_impact: float
    cost_code_id: int
    schedule_impact_days: int
    schedule_impact_phase_id: int
    status: str
    created_by: int
    created_at: str
    updated_at: str

class AttachmentUploadRequest(BaseModel):
    file_name: str
    file_type: str
    file_size_bytes: int
    file_content: str  # Base64 encoded
    description: Optional[str] = None

class AttachmentResponse(BaseModel):
    id: int
    change_order_id: int
    file_name: str
    file_type: str
    file_size_bytes: int
    description: Optional[str] = None
    uploaded_by: int
    created_at: str
    updated_at: str


# --- Helper Functions ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def parse_datetime(dt_str: Optional[str]) -> Optional[str]:
    if dt_str is None:
        return None
    # Normalize to ISO format string for response consistency if needed, 
    # but SQLite usually returns strings like 'YYYY-MM-DD HH:MM:SS'.
    # The spec asks for string responses. We'll pass through what we get or ensure it's a string.
    return str(dt_str)

# --- Endpoints ---

@app.get("/cost_codes", response_model=CostCodeResponse, operation_id="find_cost_code_by_code")
def find_cost_code_by_code(code: str = Query(..., description="The cost code identifier (e.g., '08 81 00')")):
    db = next(get_db())
    try:
        result = db.execute(text("SELECT id, code, name, description FROM cost_codes WHERE code = :code"), {"code": code}).fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Cost code not found")
        
        return CostCodeResponse(
            id=result.id,
            code=result.code,
            name=result.name,
            description=result.description
        )
    finally:
        db.close()

@app.get("/project_phases", response_model=ProjectPhaseResponse, operation_id="find_phase_by_project_and_name")
def find_phase_by_project_and_name(
    project_id: str = Query(..., description="Unique identifier of the project"),
    name: str = Query(..., description="Name of the project phase (e.g., 'Roofing')")
):
    db = next(get_db())
    try:
        result = db.execute(
            text("""
                SELECT id, project_id, name, description, planned_start_date, planned_end_date, actual_start_date, actual_end_date
                FROM project_phases
                WHERE project_id = :project_id AND name = :name
            """),
            {"project_id": project_id, "name": name}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Project phase not found")
            
        return ProjectPhaseResponse(
            id=result.id,
            project_id=result.project_id,
            name=result.name,
            description=result.description,
            planned_start_date=str(result.planned_start_date) if result.planned_start_date else None,
            planned_end_date=str(result.planned_end_date) if result.planned_end_date else None,
            actual_start_date=str(result.actual_start_date) if result.actual_start_date else None,
            actual_end_date=str(result.actual_end_date) if result.actual_end_date else None
        )
    finally:
        db.close()

@app.post("/change_orders", response_model=ChangeOrderResponse, operation_id="create_change_order")
def create_change_order(payload: ChangeOrderCreateRequest):
    db = next(get_db())
    try:
        current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
        if not current_user_id_str:
            raise HTTPException(status_code=401, detail="User not authenticated")
        current_user_id = int(current_user_id_str)

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        # Insert into change_orders
        stmt = text("""
            INSERT INTO change_orders (
                project_id, title, scope_description, cost_impact, cost_code_id,
                schedule_impact_days, schedule_impact_phase_id, status, created_by, created_at, updated_at
            ) VALUES (
                :project_id, :title, :scope_description, :cost_impact, :cost_code_id,
                :schedule_impact_days, :schedule_impact_phase_id, 'draft', :created_by, :now, :now
            ) RETURNING id
        """)
        
        params = {
            "project_id": payload.project_id,
            "title": payload.title,
            "scope_description": payload.scope_description,
            "cost_impact": payload.cost_impact,
            "cost_code_id": payload.cost_code_id,
            "schedule_impact_days": payload.schedule_impact_days,
            "schedule_impact_phase_id": payload.schedule_impact_phase_id,
            "created_by": current_user_id,
            "now": now
        }
        
        res = db.execute(stmt, params)
        new_id = res.fetchone()[0]
        db.commit()
        
        # Fetch the created record to return full details
        fetch_stmt = text("""
            SELECT id, project_id, title, scope_description, cost_impact, cost_code_id,
                   schedule_impact_days, schedule_impact_phase_id, status, created_by, created_at, updated_at
            FROM change_orders WHERE id = :id
        """)
        row = db.execute(fetch_stmt, {"id": new_id}).fetchone()
        
        return ChangeOrderResponse(
            id=row.id,
            project_id=row.project_id,
            title=row.title,
            scope_description=row.scope_description,
            cost_impact=row.cost_impact,
            cost_code_id=row.cost_code_id,
            schedule_impact_days=row.schedule_impact_days,
            schedule_impact_phase_id=row.schedule_impact_phase_id,
            status=row.status,
            created_by=row.created_by,
            created_at=str(row.created_at),
            updated_at=str(row.updated_at)
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/change_orders/{change_order_id}/attachments", response_model=AttachmentResponse, operation_id="upload_attachment")
def upload_attachment(
    change_order_id: int = Path(..., description="ID of the change order to which the attachment belongs"),
    payload: AttachmentUploadRequest = Body(...)
):
    db = next(get_db())
    try:
        current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
        if not current_user_id_str:
            raise HTTPException(status_code=401, detail="User not authenticated")
        current_user_id = int(current_user_id_str)

        # Verify change order exists
        co_check = db.execute(text("SELECT id FROM change_orders WHERE id = :id"), {"id": change_order_id}).fetchone()
        if not co_check:
            raise HTTPException(status_code=404, detail="Change order not found")

        # Decode base64 content
        try:
            decoded_content = base64.b64decode(payload.file_content)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid base64 content")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        stmt = text("""
            INSERT INTO change_order_attachments (
                change_order_id, file_name, file_type, file_size_bytes, file_content, description, uploaded_by, created_at, updated_at
            ) VALUES (
                :change_order_id, :file_name, :file_type, :file_size_bytes, :file_content, :description, :uploaded_by, :now, :now
            ) RETURNING id
        """)
        
        params = {
            "change_order_id": change_order_id,
            "file_name": payload.file_name,
            "file_type": payload.file_type,
            "file_size_bytes": payload.file_size_bytes,
            "file_content": decoded_content,
            "description": payload.description,
            "uploaded_by": current_user_id,
            "now": now
        }
        
        res = db.execute(stmt, params)
        new_att_id = res.fetchone()[0]
        db.commit()
        
        # Fetch to return
        fetch_stmt = text("""
            SELECT id, change_order_id, file_name, file_type, file_size_bytes, description, uploaded_by, created_at, updated_at
            FROM change_order_attachments WHERE id = :id
        """)
        row = db.execute(fetch_stmt, {"id": new_att_id}).fetchone()
        
        return AttachmentResponse(
            id=row.id,
            change_order_id=row.change_order_id,
            file_name=row.file_name,
            file_type=row.file_type,
            file_size_bytes=row.file_size_bytes,
            description=row.description,
            uploaded_by=row.uploaded_by,
            created_at=str(row.created_at),
            updated_at=str(row.updated_at)
        )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
