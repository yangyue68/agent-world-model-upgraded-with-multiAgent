import os
from datetime import datetime

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class ShowingSearchResponse(BaseModel):
    showing_id: int


class CancelResponse(BaseModel):
    success: bool
    message: str


@app.get("/showings/search", response_model=ShowingSearchResponse, operation_id="find_showing_by_unit_and_time")
def find_showing_by_unit_and_time(
    unit_id: int = Query(...),
    scheduled_at: str = Query(...)
):
    # Parse the scheduled_at string to handle both 'T...Z' and space-separated forms if needed, 
    # but the spec says YYYY-MM-DD HH:MM:SS. We'll try to normalize or just use it directly in SQL comparison.
    # SQLite stores datetimes as strings usually. Let's assume the input format matches storage or we can compare loosely.
    
    query = text("""
        SELECT id FROM showings 
        WHERE unit_id = :unit_id 
        AND scheduled_at LIKE :scheduled_pattern
    """)
    
    # The spec example uses "2025-03-15 14:00:00". 
    # If the user passes ISO with T/Z, we might need to convert. 
    # However, the parameter description says "ISO format (YYYY-MM-DD HH:MM:SS)".
    # Let's try to match exactly first, then maybe a broader match if needed.
    
    # To be safe against slight formatting differences (e.g. missing seconds), we can use LIKE with %
    # But strict equality is better if formats match.
    
    # Let's normalize the input to ensure it matches the DB format if possible.
    # If input is "2025-03-15T14:00:00Z", replace T with space and remove Z.
    normalized_scheduled_at = scheduled_at.replace('T', ' ').replace('Z', '')
    
    # Try exact match first
    with engine.connect() as conn:
        result = conn.execute(query, {"unit_id": unit_id, "scheduled_pattern": f"{normalized_scheduled_at}%"}).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Showing not found")
            
        return ShowingSearchResponse(showing_id=result[0])


@app.put("/showings/{showing_id}/cancel", response_model=CancelResponse, operation_id="cancel_showing_and_update_lead")
def cancel_showing_and_update_lead(showing_id: int):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=400, detail="No current user context")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID format")

    with engine.begin() as conn:
        # 1. Get showing details to find lead_id
        showing_row = conn.execute(text("SELECT lead_id, status FROM showings WHERE id = :id"), {"id": showing_id}).fetchone()
        if not showing_row:
            raise HTTPException(status_code=404, detail="Showing not found")
        
        lead_id = showing_row[0]
        current_status = showing_row[1]
        
        # Check if already cancelled? Spec doesn't say, but usually idempotent or error. 
        # Task says "Cancel the showing... update lead... add note".
        
        # 2. Update showing status to 'Cancelled'
        conn.execute(
            text("UPDATE showings SET status = 'Cancelled', updated_at = CURRENT_TIMESTAMP WHERE id = :id"),
            {"id": showing_id}
        )
        
        # 3. Update associated lead status to 'Cancelled'
        if lead_id:
            conn.execute(
                text("UPDATE leads SET status = 'Cancelled', updated_at = CURRENT_TIMESTAMP WHERE id = :id"),
                {"id": lead_id}
            )
            
        # 4. Add note
        # Note content is fixed by task description for this specific endpoint variant? 
        # Wait, looking at the API spec:
        # PUT /showings/{showing_id}/cancel -> "Cancel the showing... update lead status... and add a note indicating the applicant requested to reschedule."
        # POST /showings/{showing_id}/cancel-with-note -> "Cancel the showing... update lead status... and add a note with the content 'Applicant requested to reschedule.'"
        
        # Both seem to do the same thing based on the description text provided in the prompt's api_spec.
        # The PUT description says: "add a note indicating the applicant requested to reschedule."
        # The POST description says: "add a note with the content 'Applicant requested to reschedule.'"
        
        # I will implement the PUT endpoint to add the note as described.
        
        note_content = "Applicant requested to reschedule."
        
        conn.execute(
            text("""
                INSERT INTO notes (lead_id, showing_id, user_id, content, created_at, updated_at)
                VALUES (:lead_id, :showing_id, :user_id, :content, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """),
            {
                "lead_id": lead_id,
                "showing_id": showing_id,
                "user_id": current_user_id,
                "content": note_content
            }
        )
        
    return CancelResponse(success=True, message="Showing cancelled successfully")


@app.post("/showings/{showing_id}/cancel-with-note", response_model=CancelResponse, operation_id="cancel_showing_with_note")
def cancel_showing_with_note(showing_id: int):
    # This endpoint seems functionally identical to the PUT one based on descriptions.
    # Implementing similarly.
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=400, detail="No current user context")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID format")

    with engine.begin() as conn:
        showing_row = conn.execute(text("SELECT lead_id, status FROM showings WHERE id = :id"), {"id": showing_id}).fetchone()
        if not showing_row:
            raise HTTPException(status_code=404, detail="Showing not found")
        
        lead_id = showing_row[0]
        
        conn.execute(
            text("UPDATE showings SET status = 'Cancelled', updated_at = CURRENT_TIMESTAMP WHERE id = :id"),
            {"id": showing_id}
        )
        
        if lead_id:
            conn.execute(
                text("UPDATE leads SET status = 'Cancelled', updated_at = CURRENT_TIMESTAMP WHERE id = :id"),
                {"id": lead_id}
            )
            
        note_content = "Applicant requested to reschedule."
        
        conn.execute(
            text("""
                INSERT INTO notes (lead_id, showing_id, user_id, content, created_at, updated_at)
                VALUES (:lead_id, :showing_id, :user_id, :content, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """),
            {
                "lead_id": lead_id,
                "showing_id": showing_id,
                "user_id": current_user_id,
                "content": note_content
            }
        )
        
    return CancelResponse(success=True, message="Showing cancelled successfully")
