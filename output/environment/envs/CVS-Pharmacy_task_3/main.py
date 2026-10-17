import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class PrescriptionResponse(BaseModel):
    id: int
    prescription_number: str
    status: str
    estimated_ready_date: Optional[str] = None
    preferred_pharmacy_id: Optional[int] = None


class PharmacySearchResponse(BaseModel):
    id: int
    name: str
    address_line1: str
    address_line2: Optional[str] = None
    city: str
    state: str
    zip_code: str


class UpdatePickupLocationRequest(BaseModel):
    address_line1: str
    address_line2: Optional[str] = None
    city: str
    state: str
    zip_code: str


def _parse_datetime(dt_str: Optional[str]) -> Optional[str]:
    if dt_str is None:
        return None
    # Normalize to ISO format string for response
    try:
        # Try parsing with T separator first
        if 'T' in dt_str:
            dt_obj = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        else:
            # Space separated
            dt_obj = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        
        # Return as ISO string without microseconds if zero, or keep standard representation
        # The spec says format date-time. Usually ISO 8601.
        return dt_obj.isoformat()
    except Exception:
        return dt_str


@app.get("/prescriptions/{prescription_number}", response_model=PrescriptionResponse, operation_id="get_prescription_status")
def get_prescription_status(prescription_number: str):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    query = text("""
        SELECT id, prescription_number, status, estimated_ready_date, preferred_pharmacy_id
        FROM prescriptions
        WHERE prescription_number = :pn AND user_id = :uid
    """)
    
    with engine.connect() as conn:
        result = conn.execute(query, {"pn": prescription_number, "uid": current_user_id}).fetchone()
    
    if not result:
        raise HTTPException(status_code=404, detail="Prescription not found")
    
    row = dict(result._mapping)
    return PrescriptionResponse(
        id=row["id"],
        prescription_number=row["prescription_number"],
        status=row["status"],
        estimated_ready_date=_parse_datetime(row["estimated_ready_date"]),
        preferred_pharmacy_id=row["preferred_pharmacy_id"]
    )


@app.get("/pharmacies/search", response_model=PharmacySearchResponse, operation_id="search_pharmacies")
def search_pharmacies(
    address_line1: str = Query(...),
    city: str = Query(...),
    state: str = Query(...),
    zip_code: str = Query(...),
    address_line2: Optional[str] = Query(None)
):
    # Build dynamic conditions based on provided params
    # Note: Spec requires address_line1, city, state, zip_code. address_line2 is optional.
    
    base_query = """
        SELECT id, name, address_line1, address_line2, city, state, zip_code
        FROM pharmacies
        WHERE address_line1 = :al1 AND city = :c AND state = :s AND zip_code = :z
    """
    params = {
        "al1": address_line1,
        "c": city,
        "s": state,
        "z": zip_code
    }
    
    if address_line2 is not None:
        base_query += " AND address_line2 = :al2"
        params["al2"] = address_line2
        
    query = text(base_query + " LIMIT 1")
    
    with engine.connect() as conn:
        result = conn.execute(query, params).fetchone()
        
    if not result:
        raise HTTPException(status_code=404, detail="Pharmacy not found")
        
    row = dict(result._mapping)
    return PharmacySearchResponse(**row)


@app.put("/prescriptions/{prescription_number}/pharmacy", response_model=PrescriptionResponse, operation_id="update_prescription_pickup_location")
def update_prescription_pickup_location(
    prescription_number: str,
    body: UpdatePickupLocationRequest
):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")

    # First, find the pharmacy ID based on the address details
    # We reuse the logic from search but internally
    
    base_query = """
        SELECT id
        FROM pharmacies
        WHERE address_line1 = :al1 AND city = :c AND state = :s AND zip_code = :z
    """
    params = {
        "al1": body.address_line1,
        "c": body.city,
        "s": body.state,
        "z": body.zip_code
    }
    
    if body.address_line2 is not None:
        base_query += " AND address_line2 = :al2"
        params["al2"] = body.address_line2
        
    pharmacy_query = text(base_query + " LIMIT 1")
    
    with engine.connect() as conn:
        pharm_result = conn.execute(pharmacy_query, params).fetchone()
        
    if not pharm_result:
        raise HTTPException(status_code=404, detail="Pharmacy not found")
        
    pharmacy_id = pharm_result[0]
    
    # Now update the prescription
    # Ensure the prescription belongs to the current user
    update_stmt = text("""
        UPDATE prescriptions
        SET preferred_pharmacy_id = :pid, updated_at = CURRENT_TIMESTAMP
        WHERE prescription_number = :pn AND user_id = :uid
    """)
    
    with engine.begin() as conn:
        res = conn.execute(update_stmt, {
            "pid": pharmacy_id,
            "pn": prescription_number,
            "uid": current_user_id
        })
        
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Prescription not found or does not belong to user")
            
    # Fetch the updated record to return
    fetch_stmt = text("""
        SELECT id, prescription_number, status, estimated_ready_date, preferred_pharmacy_id
        FROM prescriptions
        WHERE prescription_number = :pn AND user_id = :uid
    """)
    
    with engine.connect() as conn:
        final_res = conn.execute(fetch_stmt, {"pn": prescription_number, "uid": current_user_id}).fetchone()
        
    if not final_res:
         # Should theoretically not happen after successful update
        raise HTTPException(status_code=500, detail="Failed to retrieve updated prescription")
        
    row = dict(final_res._mapping)
    return PrescriptionResponse(
        id=row["id"],
        prescription_number=row["prescription_number"],
        status=row["status"],
        estimated_ready_date=_parse_datetime(row["estimated_ready_date"]),
        preferred_pharmacy_id=row["preferred_pharmacy_id"]
    )
