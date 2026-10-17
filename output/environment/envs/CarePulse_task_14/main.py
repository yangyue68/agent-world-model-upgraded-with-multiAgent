import os
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

# Fix: Read DATABASE_PATH from the runtime environment instead of importing it.
DATABASE_PATH = os.environ.get("DATABASE_PATH") or os.environ.get("DB_URL") or "sqlite:///./test.db"

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class SecureMessageRequest(BaseModel):
    recipient_patient_id: int
    subject: str
    body: str


class SecureMessageResponse(BaseModel):
    message_id: int
    status: str


@app.post("/secure-messages", response_model=SecureMessageResponse, operation_id="send_secure_message")
def send_secure_message(payload: SecureMessageRequest) -> SecureMessageResponse:
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Missing authenticated user context.")
    
    try:
        sender_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authenticated user ID.")

    with engine.begin() as conn:
        # Verify patient exists
        patient_check = conn.execute(
            text("SELECT id FROM patients WHERE id = :pid"),
            {"pid": payload.recipient_patient_id}
        ).fetchone()
        
        if not patient_check:
            raise HTTPException(status_code=404, detail="Patient not found.")

        # Insert the secure message
        result = conn.execute(
            text("""
                INSERT INTO messages (sender_id, recipient_patient_id, subject, body, is_secure)
                VALUES (:sender_id, :recipient_patient_id, :subject, :body, 1)
            """),
            {
                "sender_id": sender_id,
                "recipient_patient_id": payload.recipient_patient_id,
                "subject": payload.subject,
                "body": payload.body,
            }
        )
        
        message_id = result.lastrowid
        
        return SecureMessageResponse(message_id=message_id, status="sent")
