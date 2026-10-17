import os
from datetime import datetime, date
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="AssetLink Medical Equipment Tracker API")


class ServiceTicketCreateRequest(BaseModel):
    equipment_asset_tag: str = Field(..., description="Asset tag of the equipment requiring service")
    location_name: str = Field(..., description="Name of the location where the equipment is currently situated")
    priority: str = Field(..., description="Priority level of the service ticket (Low, Medium, High, Critical)")
    issue_description: str = Field(..., description="Detailed description of the issue encountered with the equipment")
    assigned_engineer_full_name: str = Field(..., description="Full name of the biomedical engineer to whom the ticket should be assigned")
    expected_resolution_date: str = Field(..., description="Expected date by which the issue should be resolved (YYYY-MM-DD format)")


class ServiceTicketResponse(BaseModel):
    ticket_number: str
    equipment_id: int
    location_id: int
    priority: str
    issue_description: str
    status: str
    assigned_to: int
    expected_resolution_date: str
    created_by: int
    created_at: str


def generate_ticket_number(session) -> str:
    """Generate a unique ticket number in the format TKT-YYYY-NNNN"""
    current_year = datetime.now().year
    prefix = f"TKT-{current_year}-"
    
    # Find the highest existing ticket number for this year
    result = session.execute(
        text("""
            SELECT ticket_number FROM service_tickets 
            WHERE ticket_number LIKE :prefix 
            ORDER BY ticket_number DESC 
            LIMIT 1
        """),
        {"prefix": f"{prefix}%"}
    ).fetchone()
    
    if result:
        last_num = int(result[0].split("-")[-1])
        new_num = last_num + 1
    else:
        new_num = 1
    
    return f"{prefix}{new_num:04d}"


@app.post("/service-tickets", operation_id="create_corrective_service_ticket", response_model=ServiceTicketResponse)
def create_corrective_service_ticket(request: ServiceTicketCreateRequest):
    session = SessionLocal()
    try:
        # Validate priority
        valid_priorities = ["Low", "Medium", "High", "Critical"]
        if request.priority not in valid_priorities:
            raise HTTPException(status_code=400, detail=f"Invalid priority. Must be one of: {valid_priorities}")
        
        # Parse expected resolution date
        try:
            exp_date = datetime.strptime(request.expected_resolution_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid expected_resolution_date format. Use YYYY-MM-DD.")
        
        # Look up equipment by asset_tag
        equipment_result = session.execute(
            text("SELECT id, current_location_id FROM equipment WHERE asset_tag = :tag"),
            {"tag": request.equipment_asset_tag}
        ).fetchone()
        
        if not equipment_result:
            raise HTTPException(status_code=404, detail=f"Equipment with asset_tag '{request.equipment_asset_tag}' not found.")
        
        equipment_id = equipment_result[0]
        
        # Look up location by name
        location_result = session.execute(
            text("SELECT id FROM locations WHERE name = :name"),
            {"name": request.location_name}
        ).fetchone()
        
        if not location_result:
            raise HTTPException(status_code=404, detail=f"Location '{request.location_name}' not found.")
        
        location_id = location_result[0]
        
        # Look up user by full name and role
        user_result = session.execute(
            text("SELECT id FROM users WHERE full_name = :name AND role = 'biomedical_engineer'"),
            {"name": request.assigned_engineer_full_name}
        ).fetchone()
        
        if not user_result:
            raise HTTPException(status_code=404, detail=f"Biomedical engineer '{request.assigned_engineer_full_name}' not found.")
        
        assigned_to_user_id = user_result[0]
        
        # Get current user ID from environment variable
        current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
        if not current_user_id_str:
            raise HTTPException(status_code=500, detail="Current user ID not available.")
        
        try:
            created_by_user_id = int(current_user_id_str)
        except ValueError:
            raise HTTPException(status_code=500, detail="Invalid current user ID format.")
        
        # Verify the creator exists
        creator_result = session.execute(
            text("SELECT id FROM users WHERE id = :uid"),
            {"uid": created_by_user_id}
        ).fetchone()
        
        if not creator_result:
            raise HTTPException(status_code=400, detail="Creator user not found.")
        
        # Generate ticket number
        ticket_number = generate_ticket_number(session)
        
        # Insert the new service ticket
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        session.execute(
            text("""
                INSERT INTO service_tickets (
                    ticket_number, equipment_id, location_id, priority, issue_description,
                    status, assigned_to, expected_resolution_date, created_by, created_at, updated_at
                ) VALUES (
                    :ticket_number, :equipment_id, :location_id, :priority, :issue_description,
                    'open', :assigned_to, :expected_resolution_date, :created_by, :created_at, :updated_at
                )
            """),
            {
                "ticket_number": ticket_number,
                "equipment_id": equipment_id,
                "location_id": location_id,
                "priority": request.priority,
                "issue_description": request.issue_description,
                "assigned_to": assigned_to_user_id,
                "expected_resolution_date": request.expected_resolution_date,
                "created_by": created_by_user_id,
                "created_at": now,
                "updated_at": now,
            }
        )
        session.commit()
        
        # Fetch the inserted record to return it
        inserted = session.execute(
            text("""
                SELECT ticket_number, equipment_id, location_id, priority, issue_description,
                       status, assigned_to, expected_resolution_date, created_by, created_at
                FROM service_tickets WHERE ticket_number = :tn
            """),
            {"tn": ticket_number}
        ).fetchone()
        
        if not inserted:
            raise HTTPException(status_code=500, detail="Failed to retrieve created ticket.")
        
        return ServiceTicketResponse(
            ticket_number=inserted[0],
            equipment_id=inserted[1],
            location_id=inserted[2],
            priority=inserted[3],
            issue_description=inserted[4],
            status=inserted[5],
            assigned_to=inserted[6],
            expected_resolution_date=str(inserted[7]),
            created_by=inserted[8],
            created_at=str(inserted[9])
        )
    
    except HTTPException:
        session.rollback()
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()
