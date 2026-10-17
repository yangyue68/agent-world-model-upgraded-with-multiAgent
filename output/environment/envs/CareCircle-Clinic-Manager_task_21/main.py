import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

# DATABASE_PATH is provided by the environment/runtime
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)

app = FastAPI()

class DeleteResponse(BaseModel):
    message: str
    deleted_patient_id: int

@app.delete("/patients/{patient_id}", response_model=DeleteResponse, operation_id="delete_patient_profile")
def delete_patient_profile(patient_id: int):
    """
    Remove the complete patient profile for the specified patient ID.
    This relies on ON DELETE CASCADE constraints in the database schema
    to remove associated appointments, medical records, prescriptions, and billing info.
    """
    
    # Check if the patient exists first to provide appropriate error handling
    with engine.connect() as conn:
        result = conn.execute(text("SELECT id FROM patients WHERE id = :pid"), {"pid": patient_id})
        row = result.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail=f"Patient with ID {patient_id} not found.")
        
        # Perform the deletion
        # The foreign keys are defined with ON DELETE CASCADE, so deleting from 'patients'
        # will automatically delete related rows in other tables.
        conn.execute(text("DELETE FROM patients WHERE id = :pid"), {"pid": patient_id})
        conn.commit()

    return DeleteResponse(
        message=f"Patient profile {patient_id} deleted successfully along with all associated records.",
        deleted_patient_id=patient_id
    )
