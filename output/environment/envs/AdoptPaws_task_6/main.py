import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()

# --- Pydantic Models for Responses ---

class AnimalLookupResponse(BaseModel):
    id: int
    name: str

class VaccinationRecord(BaseModel):
    id: int
    vaccine_type: str
    administered_date: Optional[str] = None
    lot_number: Optional[str] = None
    next_due_date: Optional[str] = None

class MedicationRecord(BaseModel):
    id: int
    medication_name: str
    dosage: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    status: str
    discontinued_reason: Optional[str] = None

class UpdateVaccinationRequest(BaseModel):
    administered_date: Optional[str] = None
    lot_number: Optional[str] = None
    next_due_date: Optional[str] = None

class UpdateMedicationRequest(BaseModel):
    status: Optional[str] = None
    end_date: Optional[str] = None
    discontinued_reason: Optional[str] = None

class OperationStatusResponse(BaseModel):
    id: int
    status: str
    message: str

# --- Helper to get DB session ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- Endpoints ---

@app.get("/animals/lookup", response_model=AnimalLookupResponse, operation_id="lookup_animal_by_microchip")
def lookup_animal_by_microchip(microchip_id: str = Query(...)):
    with SessionLocal() as db:
        result = db.execute(
            text("SELECT id, name FROM animals WHERE microchip_id = :microchip_id"),
            {"microchip_id": microchip_id}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Animal not found")
        
        return AnimalLookupResponse(id=result[0], name=result[1])


@app.get("/vaccinations", response_model=List[VaccinationRecord], operation_id="list_vaccinations_for_animal")
def list_vaccinations_for_animal(animal_id: int = Query(...)):
    with SessionLocal() as db:
        results = db.execute(
            text("""
                SELECT id, vaccine_type, administered_date, lot_number, next_due_date 
                FROM vaccinations 
                WHERE animal_id = :animal_id
            """),
            {"animal_id": animal_id}
        ).fetchall()
        
        records = []
        for row in results:
            # Handle date formatting if necessary, though SQLite returns strings usually
            admin_date = str(row[2]) if row[2] else None
            due_date = str(row[4]) if row[4] else None
            
            records.append(VaccinationRecord(
                id=row[0],
                vaccine_type=row[1],
                administered_date=admin_date,
                lot_number=row[3],
                next_due_date=due_date
            ))
            
        return records


@app.patch("/vaccinations/{vaccination_id}", response_model=OperationStatusResponse, operation_id="update_vaccination_record")
def update_vaccination_record(vaccination_id: int = Path(...), request: UpdateVaccinationRequest = None):
    if request is None:
        request = UpdateVaccinationRequest()
        
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    current_user_id = int(current_user_id_str) if current_user_id_str and current_user_id_str.isdigit() else None

    with SessionLocal() as db:
        # Check existence
        exists = db.execute(
            text("SELECT 1 FROM vaccinations WHERE id = :id"),
            {"id": vaccination_id}
        ).fetchone()
        
        if not exists:
            raise HTTPException(status_code=404, detail="Vaccination record not found")
        
        updates = []
        params = {"id": vaccination_id}
        
        if request.administered_date is not None:
            updates.append("administered_date = :administered_date")
            params["administered_date"] = request.administered_date
            
        if request.lot_number is not None:
            updates.append("lot_number = :lot_number")
            params["lot_number"] = request.lot_number
            
        if request.next_due_date is not None:
            updates.append("next_due_date = :next_due_date")
            params["next_due_date"] = request.next_due_date
            
        # If we are marking it as administered, we might want to set administered_by if provided in context?
        # The spec doesn't explicitly ask to update administered_by via this endpoint's body, 
        # but typically "marking as administered" implies who did it. 
        # However, strictly following the API spec parameters: only date, lot, next_due are in body.
        # We will just update what is provided.
        
        if not updates:
             return OperationStatusResponse(id=vaccination_id, status="success", message="No fields updated")

        updates.append("updated_at = CURRENT_TIMESTAMP")
        
        sql = f"UPDATE vaccinations SET {', '.join(updates)} WHERE id = :id"
        
        db.execute(text(sql), params)
        db.commit()
        
        return OperationStatusResponse(id=vaccination_id, status="success", message="Vaccination record updated successfully")


@app.get("/medications", response_model=List[MedicationRecord], operation_id="list_medications_for_animal")
def list_medications_for_animal(animal_id: int = Query(...), status: Optional[str] = Query(None)):
    with SessionLocal() as db:
        query = """
            SELECT id, medication_name, dosage, start_date, end_date, status, discontinued_reason 
            FROM medications 
            WHERE animal_id = :animal_id
        """
        params = {"animal_id": animal_id}
        
        if status:
            query += " AND status = :status"
            params["status"] = status
            
        results = db.execute(text(query), params).fetchall()
        
        records = []
        for row in results:
            start_date = str(row[3]) if row[3] else None
            end_date = str(row[4]) if row[4] else None
            
            records.append(MedicationRecord(
                id=row[0],
                medication_name=row[1],
                dosage=row[2],
                start_date=start_date,
                end_date=end_date,
                status=row[5],
                discontinued_reason=row[6]
            ))
            
        return records


@app.patch("/medications/{medication_id}", response_model=OperationStatusResponse, operation_id="update_medication_record")
def update_medication_record(medication_id: int = Path(...), request: UpdateMedicationRequest = None):
    if request is None:
        request = UpdateMedicationRequest()
        
    with SessionLocal() as db:
        # Check existence
        exists = db.execute(
            text("SELECT 1 FROM medications WHERE id = :id"),
            {"id": medication_id}
        ).fetchone()
        
        if not exists:
            raise HTTPException(status_code=404, detail="Medication record not found")
        
        updates = []
        params = {"id": medication_id}
        
        if request.status is not None:
            updates.append("status = :status")
            params["status"] = request.status
            
        if request.end_date is not None:
            updates.append("end_date = :end_date")
            params["end_date"] = request.end_date
            
        if request.discontinued_reason is not None:
            updates.append("discontinued_reason = :discontinued_reason")
            params["discontinued_reason"] = request.discontinued_reason
            
        if not updates:
             return OperationStatusResponse(id=medication_id, status="success", message="No fields updated")

        updates.append("updated_at = CURRENT_TIMESTAMP")
        
        sql = f"UPDATE medications SET {', '.join(updates)} WHERE id = :id"
        
        db.execute(text(sql), params)
        db.commit()
        
        return OperationStatusResponse(id=medication_id, status="success", message="Medication record updated successfully")
