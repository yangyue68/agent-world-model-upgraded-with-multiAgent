import os
from typing import Optional, Literal

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# Initialize database engine using the provided DATABASE_PATH environment variable
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine: Engine = create_engine(DATABASE_PATH)

app = FastAPI(title="CleverShuttle Tutoring Platform API")


class SessionUpdateRequest(BaseModel):
    status: Literal["completed"]
    notes: str


class SessionResponse(BaseModel):
    id: str
    status: str
    notes: str


@app.put(
    "/sessions/{session_id}",
    response_model=SessionResponse,
    operation_id="update_session_status_and_notes",
    summary="Update session status to completed and add session notes",
    description="Mark the specified session as 'completed' and update its notes field with the provided content. The session must exist and be in 'scheduled' status to be updated.",
    tags=["PrimaryResource"],
)
def update_session_status_and_notes(
    session_id: str = Path(..., description="The unique identifier of the session to update"),
    body: SessionUpdateRequest = ...,
):
    """
    Update a session's status to 'completed' and set its notes.
    
    - Validates that the session exists.
    - Validates that the session is currently in 'scheduled' status.
    - Updates the status to 'completed' and sets the notes.
    - Returns the updated session details.
    """
    # Check if session exists and get current status
    select_query = text("""
        SELECT id, status FROM sessions WHERE id = :session_id
    """)
    
    with engine.connect() as conn:
        result = conn.execute(select_query, {"session_id": session_id}).fetchone()
        
        if result is None:
            raise HTTPException(status_code=404, detail="Session not found")
        
        current_status = result[1]
        
        # Validate that the session is in 'scheduled' status
        if current_status != "scheduled":
            raise HTTPException(
                status_code=400,
                detail=f"Session cannot be updated because it is in '{current_status}' status. Only 'scheduled' sessions can be marked as completed."
            )
        
        # Perform the update
        update_query = text("""
            UPDATE sessions 
            SET status = :status, notes = :notes, updated_at = CURRENT_TIMESTAMP
            WHERE id = :session_id
        """)
        
        conn.execute(update_query, {
            "session_id": session_id,
            "status": body.status,
            "notes": body.notes
        })
        conn.commit()
        
        # Fetch the updated session to return
        fetch_updated_query = text("""
            SELECT id, status, notes FROM sessions WHERE id = :session_id
        """)
        
        updated_result = conn.execute(fetch_updated_query, {"session_id": session_id}).fetchone()
        
        if updated_result is None:
            raise HTTPException(status_code=500, detail="Failed to retrieve updated session")
        
        return SessionResponse(
            id=updated_result[0],
            status=updated_result[1],
            notes=updated_result[2] or ""
        )
