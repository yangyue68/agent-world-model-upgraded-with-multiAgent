import os
from datetime import datetime, timedelta
from typing import List

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()

# --- Pydantic Models ---

class PendingConsultationRequest(BaseModel):
    id: int
    patient_name: str
    reason_for_visit: str
    created_at: str  # ISO format string

class AssignRequestBody(BaseModel):
    assigned_by_user_id: int

class AssignedConsultationResponse(BaseModel):
    id: int
    patient_name: str
    reason_for_visit: str
    assigned_specialist_id: int
    assigned_at: str  # ISO format string

class SpecialistInfo(BaseModel):
    id: int
    user_id: int


# --- Helper Functions ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

REASON_TO_SPECIALTY_MAP = {
    "chest pain": "cardiology",
    "skin rash": "dermatology",
    "anxiety": "behavioral_health"
}

# --- Endpoints ---

@app.get("/consultation-requests/pending", response_model=List[PendingConsultationRequest], operation_id="find_pending_consultation_requests")
def find_pending_consultation_requests():
    """
    Find all pending consultation requests from the last 24 hours.
    """
    now = datetime.utcnow()
    cutoff_time = now - timedelta(hours=24)
    
    with engine.connect() as conn:
        query = text("""
            SELECT id, patient_name, reason_for_visit, created_at
            FROM consultation_requests
            WHERE status = 'pending'
              AND created_at >= :cutoff_time
            ORDER BY created_at ASC
        """)
        result = conn.execute(query, {"cutoff_time": cutoff_time})
        
        items = []
        for row in result.fetchall():
            # Convert datetime object to ISO string if necessary, 
            # though SQLAlchemy usually returns strings depending on driver/config.
            # Ensuring consistent output format.
            created_at_val = row.created_at
            if isinstance(created_at_val, datetime):
                created_at_str = created_at_val.isoformat()
            else:
                created_at_str = str(created_at_val)
                
            items.append(PendingConsultationRequest(
                id=row.id,
                patient_name=row.patient_name,
                reason_for_visit=row.reason_for_visit,
                created_at=created_at_str
            ))
            
    return items

@app.put("/consultation-requests/{id}/assign", response_model=AssignedConsultationResponse, operation_id="assign_consultation_request")
def assign_consultation_request(
    id: int = Path(..., description="ID of the consultation request to assign"),
    body: AssignRequestBody = None
):
    """
    Assign a pending consultation request to an appropriate specialist based on the reason for visit.
    Routes patients with 'chest pain' to cardiology, 'skin rash' to dermatology, and 'anxiety' to behavioral health.
    Requires the current user to be a care coordinator.
    """
    if body is None:
        raise HTTPException(status_code=400, detail="Request body required")

    # Read the actor ID from the environment variable as per instructions
    env_actor_id = os.environ.get("AWM_CURRENT_USER_ID")
    if not env_actor_id:
        raise HTTPException(status_code=401, detail="Current user ID not found in environment")
    
    try:
        assigned_by_user_id = int(env_actor_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Current User ID in environment")

    # Verify the assigning user exists and is a care coordinator
    with engine.connect() as conn:
        user_check = conn.execute(text("SELECT role FROM users WHERE id = :uid"), {"uid": assigned_by_user_id}).fetchone()
        if not user_check:
            raise HTTPException(status_code=404, detail="Assigning user not found")
        if user_check.role != 'care_coordinator':
             raise HTTPException(status_code=403, detail="User is not a care coordinator") 

        # Fetch the consultation request
        req_row = conn.execute(text("""
            SELECT id, patient_name, reason_for_visit, status
            FROM consultation_requests
            WHERE id = :rid
        """), {"rid": id}).fetchone()

        if not req_row:
            raise HTTPException(status_code=404, detail="Consultation request not found")
        
        if req_row.status != 'pending':
            raise HTTPException(status_code=400, detail=f"Cannot assign request with status '{req_row.status}'")

        reason = req_row.reason_for_visit.lower().strip()
        specialty = REASON_TO_SPECIALTY_MAP.get(reason)

        if not specialty:
            raise HTTPException(status_code=400, detail=f"No routing rule defined for reason: '{req_row.reason_for_visit}'")

        # Find a specialist for this specialty
        # Select the first available specialist ordered by ID to ensure determinism
        spec_row = conn.execute(text("""
            SELECT id
            FROM specialists
            WHERE specialty = :spec
            ORDER BY id ASC
            LIMIT 1
        """), {"spec": specialty}).fetchone()

        if not spec_row:
            raise HTTPException(status_code=500, detail=f"No specialist found for specialty: '{specialty}'")

        specialist_id = spec_row.id
        
        # Perform the update
        now_dt = datetime.utcnow()
        now_iso = now_dt.isoformat(sep=' ', timespec='seconds') 
        
        # Using parameterized SQL for safety
        update_stmt = text("""
            UPDATE consultation_requests
            SET status = 'assigned',
                assigned_specialist_id = :sid,
                assigned_by_user_id = :bid,
                assigned_at = :aat,
                updated_at = :uat
            WHERE id = :rid
        """)
        
        conn.execute(update_stmt, {
            "sid": specialist_id,
            "bid": assigned_by_user_id,
            "aat": now_iso,
            "uat": now_iso,
            "rid": id
        })
        conn.commit()

        # Return the updated record data
        return AssignedConsultationResponse(
            id=id,
            patient_name=req_row.patient_name,
            reason_for_visit=req_row.reason_for_visit,
            assigned_specialist_id=specialist_id,
            assigned_at=now_iso
        )

@app.get("/specialists/by-specialty/{specialty}", response_model=List[SpecialistInfo], operation_id="find_specialists_by_specialty")
def find_specialists_by_specialty(specialty: str = Path(..., description="The specialty of the specialists to retrieve")):
    """
    Retrieve a list of specialists who specialize in the given specialty area.
    """
    with engine.connect() as conn:
        query = text("""
            SELECT id, user_id
            FROM specialists
            WHERE specialty = :spec
            ORDER BY id ASC
        """)
        result = conn.execute(query, {"spec": specialty})
        
        items = []
        for row in result.fetchall():
            items.append(SpecialistInfo(
                id=row.id,
                user_id=row.user_id
            ))
            
    return items
