import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI(title="BioVault Cryo Repository API")


class AliquotItem(BaseModel):
    container_id: str = Field(..., description="The external identifier of the container")
    position: str = Field(..., description="Position within the container (e.g., A01)")
    volume_ml: float = Field(..., description="Volume in milliliters")


class CreateAliquotEventRequest(BaseModel):
    specimen_id: str = Field(..., description="The external identifier of the specimen to be aliquoted")
    protocol_name: str = Field(..., description="The name of the protocol used for aliquoting")
    operator_initials: str = Field(..., description="The initials of the operator performing the aliquoting")
    qc_note: Optional[str] = Field(None, description="Quality control notes related to the aliquoting process")
    aliquots: List[AliquotItem] = Field(..., description="List of aliquot details including container ID, position, and volume")


class CreateAliquotEventResponse(BaseModel):
    aliquot_event_id: int
    message: str


@app.post("/aliquot_events", response_model=CreateAliquotEventResponse, operation_id="create_aliquot_event")
def create_aliquot_event(request: CreateAliquotEventRequest):
    # Get current user ID from environment variable
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Missing authenticated user context")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=500, detail="Invalid user ID format")

    with engine.connect() as conn:
        # 1. Find specimen by external ID
        result = conn.execute(
            text("SELECT id FROM specimens WHERE specimen_id = :specimen_id"),
            {"specimen_id": request.specimen_id}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail=f"Specimen '{request.specimen_id}' not found")
        specimen_db_id = result[0]

        # 2. Find protocol by name
        result = conn.execute(
            text("SELECT id FROM protocols WHERE name = :protocol_name"),
            {"protocol_name": request.protocol_name}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail=f"Protocol '{request.protocol_name}' not found")
        protocol_db_id = result[0]

        # 3. Verify operator exists (using current_user_id as the operator_user_id)
        # The spec says record operator initials 'JL', but the DB schema uses operator_user_id FK to users.
        # We assume the logged-in user corresponds to the operator.
        result = conn.execute(
            text("SELECT id FROM users WHERE id = :user_id"),
            {"user_id": current_user_id}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail=f"Operator user ID {current_user_id} not found")

        # 4. Insert into aliquot_events
        insert_event_sql = text("""
            INSERT INTO aliquot_events (specimen_id, protocol_id, operator_user_id, qc_note)
            VALUES (:specimen_id, :protocol_id, :operator_user_id, :qc_note)
        """)
        
        conn.execute(
            insert_event_sql,
            {
                "specimen_id": specimen_db_id,
                "protocol_id": protocol_db_id,
                "operator_user_id": current_user_id,
                "qc_note": request.qc_note
            }
        )
        
        # Get the last inserted event ID
        event_id_result = conn.execute(text("SELECT last_insert_rowid()")).fetchone()
        aliquot_event_id = event_id_result[0]

        # 5. Insert each aliquot into the aliquots table
        insert_aliquot_sql = text("""
            INSERT INTO aliquots (aliquot_event_id, container_id, position, volume_ml)
            VALUES (:event_id, :container_db_id, :position, :volume_ml)
        """)

        for item in request.aliquots:
            # Find container by external ID
            result = conn.execute(
                text("SELECT id FROM containers WHERE container_id = :container_id"),
                {"container_id": item.container_id}
            ).fetchone()
            
            if not result:
                raise HTTPException(status_code=404, detail=f"Container '{item.container_id}' not found")
            container_db_id = result[0]

            conn.execute(
                insert_aliquot_sql,
                {
                    "event_id": aliquot_event_id,
                    "container_db_id": container_db_id,
                    "position": item.position,
                    "volume_ml": item.volume_ml
                }
            )

        conn.commit()

    return CreateAliquotEventResponse(
        aliquot_event_id=aliquot_event_id,
        message="Aliquot event created successfully"
    )
