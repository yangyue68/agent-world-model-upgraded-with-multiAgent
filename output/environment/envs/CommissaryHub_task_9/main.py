import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class DeleteResponse(BaseModel):
    message: str
    deleted_user_id: int


@app.delete("/users/{user_id}", response_model=DeleteResponse, operation_id="delete_user_and_associated_data")
def delete_user_and_associated_data(user_id: int):
    """
    Remove a user account and all associated data.
    
    Deletes the user by ID along with all associated bookings, booking items, 
    payments, reviews, and activity logs via cascade constraints.
    """
    # First check if the user exists
    with engine.connect() as conn:
        result = conn.execute(text("SELECT id FROM users WHERE id = :uid"), {"uid": user_id})
        row = result.fetchone()
        
        if row is None:
            raise HTTPException(status_code=404, detail=f"User with ID {user_id} not found")
        
        # Delete the user - foreign keys with ON DELETE CASCADE will handle related records
        conn.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
        conn.commit()
    
    return DeleteResponse(
        message=f"User {user_id} and all associated data have been successfully removed",
        deleted_user_id=user_id
    )
