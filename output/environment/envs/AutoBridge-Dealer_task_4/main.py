from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import os
DATABASE_PATH = os.environ.get("DATABASE_PATH")

app = FastAPI()

# Create database session
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class DeleteResponse(BaseModel):
    message: str
    deleted_vin: str

@app.delete("/vehicles/{vin}", response_model=DeleteResponse, operation_id="delete_vehicle_by_vin")
def delete_vehicle_by_vin(vin: str):
    db = SessionLocal()
    try:
        # Check if vehicle exists
        result = db.execute(
            text("SELECT id, vin FROM vehicles WHERE vin = :vin"),
            {"vin": vin}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Vehicle not found")
        
        # Delete the vehicle
        db.execute(
            text("DELETE FROM vehicles WHERE vin = :vin"),
            {"vin": vin}
        )
        
        db.commit()
        
        return DeleteResponse(
            message="Vehicle successfully deleted",
            deleted_vin=vin
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        db.close()
