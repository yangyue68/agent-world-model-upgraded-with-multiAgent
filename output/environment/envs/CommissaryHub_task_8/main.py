import os
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from typing import Optional

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()

class DeleteResponse(BaseModel):
    message: str
    deleted_reservation_id: int

def get_current_user_id() -> int:
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

@app.delete("/reservations/{reservation_id}", response_model=DeleteResponse, operation_id="delete_recurring_reservation")
def delete_recurring_reservation(reservation_id: int, current_user_id: int = Depends(get_current_user_id)):
    """
    Deletes a recurring reservation identified by ID. 
    Only the owner or admin can delete the reservation. 
    The reservation must be marked as recurring.
    """
    
    # Fetch the reservation details to verify ownership and recurrence status
    query = text("""
        SELECT r.id, r.user_id, r.is_recurring, r.status
        FROM reservations r
        WHERE r.id = :reservation_id
    """)
    
    with engine.connect() as conn:
        result = conn.execute(query, {"reservation_id": reservation_id})
        row = result.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Reservation not found")
            
        res_id, res_user_id, is_recurring, status = row
        
        # Check if it is actually a recurring reservation
        if is_recurring != 1:
            raise HTTPException(status_code=400, detail="Reservation is not recurring")
            
        # Check ownership (assuming only owner can delete based on spec "Only the owner or admin")
        # Since we don't have an explicit 'admin' role in the schema provided, we strictly check owner.
        # If there were an admin flag, we would check that too. Here we assume strict ownership for safety unless specified otherwise.
        if res_user_id != current_user_id:
             # Note: The prompt says "Only the owner or admin". Without an admin column in users table, 
             # we cannot determine who is an admin. We will enforce owner-only deletion as per standard security practices 
             # when admin logic isn't explicitly defined in the schema.
             raise HTTPException(status_code=403, detail="Forbidden: You do not own this reservation")

        # Perform the deletion
        delete_query = text("DELETE FROM reservations WHERE id = :reservation_id")
        conn.execute(delete_query, {"reservation_id": reservation_id})
        conn.commit()

    return DeleteResponse(
        message=f"Recurring reservation {reservation_id} has been successfully deleted.",
        deleted_reservation_id=reservation_id
    )
