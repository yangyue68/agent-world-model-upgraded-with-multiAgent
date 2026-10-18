import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="ClickRoute API", version="1.0.0")


# --- Pydantic Models for Request/Response ---

class AddServiceRequest(BaseModel):
    service_type: str = Field(..., description="Type of service to add (liftgate_delivery, inside_delivery, signature_required, residential_delivery)")
    notes: Optional[str] = Field(None, description="Optional notes about the service request")

class RerouteRequest(BaseModel):
    reason: str = Field(..., description="Reason for requesting the reroute")

class ShipmentServiceResponseItem(BaseModel):
    id: int
    service_type: str
    status: str
    notes: Optional[str] = None
    requested_at: str

class EvaluationResponse(BaseModel):
    admin_rule_id: int
    rule_name: str
    matched: bool
    details: str

class AddServiceResponse(BaseModel):
    shipment_service_id: int
    message: str

class RerouteResponse(BaseModel):
    shipment_reroute_id: int
    message: str


# --- Helper Functions ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user_id() -> Optional[int]:
    """Reads the current user ID from the environment variable injected by the runtime."""
    uid_str = os.environ.get("AWM_CURRENT_USER_ID")
    if uid_str:
        try:
            return int(uid_str)
        except ValueError:
            pass
    return None


# --- Endpoints ---

@app.post("/shipments/{shipment_id}/services", response_model=AddServiceResponse, operation_id="add_shipment_service")
def add_shipment_service(
    shipment_id: int = Path(..., description="Internal ID of the shipment to add service to"),
    body: AddServiceRequest = Body(...)
):
    """
    Requests a service such as liftgate delivery, inside delivery, or signature required for a shipment.
    """
    valid_service_types = ['liftgate_delivery', 'inside_delivery', 'signature_required', 'residential_delivery']
    if body.service_type not in valid_service_types:
        raise HTTPException(status_code=400, detail=f"Invalid service type. Must be one of {valid_service_types}")

    db = next(get_db())
    try:
        # Verify shipment exists
        stmt_check_shipment = text("SELECT id FROM shipments WHERE id = :sid")
        res = db.execute(stmt_check_shipment, {"sid": shipment_id})
        if not res.fetchone():
            raise HTTPException(status_code=404, detail="Shipment not found")

        user_id = get_current_user_id()
        
        # Insert new service
        stmt_insert = text("""
            INSERT INTO shipment_services (shipment_id, service_type, status, requested_by_user_id, notes)
            VALUES (:sid, :stype, 'requested', :uid, :notes)
        """)
        db.execute(stmt_insert, {
            "sid": shipment_id,
            "stype": body.service_type,
            "uid": user_id,
            "notes": body.notes
        })
        db.commit()

        # Get the generated ID
        stmt_get_id = text("SELECT last_insert_rowid()")
        new_id_res = db.execute(stmt_get_id).fetchone()
        new_id = new_id_res[0] if new_id_res else 0

        return AddServiceResponse(
            shipment_service_id=new_id,
            message=f"Service '{body.service_type}' added successfully."
        )
    finally:
        db.close()


@app.post("/shipments/{shipment_id}/reroute", response_model=RerouteResponse, operation_id="reroute_shipment")
def reroute_shipment(
    shipment_id: int = Path(..., description="Internal ID of the shipment to reroute"),
    body: RerouteRequest = Body(...)
):
    """
    Creates a reroute request for a shipment that matches the admin rule 
    'flag all residential deliveries >50 lbs for liftgate request'.
    """
    db = next(get_db())
    try:
        # Verify shipment exists and check conditions
        stmt_check = text("""
            SELECT s.id, s.status, s.weight_lbs, a.is_residential
            FROM shipments s
            JOIN addresses a ON s.consignee_address_id = a.id
            WHERE s.id = :sid
        """)
        row = db.execute(stmt_check, {"sid": shipment_id}).fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Shipment not found")

        _, status, weight, is_residential = row
        
        # Business logic validation based on spec description
        if status != 'in_transit':
             raise HTTPException(status_code=400, detail="Shipment must be in-transit to initiate this specific reroute.")
        
        if not is_residential:
             raise HTTPException(status_code=400, detail="Consignee address must be residential.")
             
        if weight <= 50:
             raise HTTPException(status_code=400, detail="Weight must be greater than 50 lbs.")

        user_id = get_current_user_id()
        if user_id is None:
            # If no user context, we might default or error depending on strictness. 
            # Spec says read env var at request time. If missing, FK constraint will fail if NOT NULL.
            # The schema has requested_by_user_id INTEGER NOT NULL. So we must have an ID.
            raise HTTPException(status_code=401, detail="User authentication required.")

        # Insert reroute record
        stmt_insert = text("""
            INSERT INTO shipment_reroutes (shipment_id, requested_by_user_id, reason, status)
            VALUES (:sid, :uid, :reason, 'requested')
        """)
        db.execute(stmt_insert, {
            "sid": shipment_id,
            "uid": user_id,
            "reason": body.reason
        })
        db.commit()

        # Get the generated ID
        stmt_get_id = text("SELECT last_insert_rowid()")
        new_id_res = db.execute(stmt_get_id).fetchone()
        new_id = new_id_res[0] if new_id_res else 0

        return RerouteResponse(
            shipment_reroute_id=new_id,
            message="Reroute request initiated successfully."
        )
    finally:
        db.close()


@app.get("/shipments/{shipment_id}/evaluation", response_model=EvaluationResponse, operation_id="get_shipment_evaluation")
def get_shipment_evaluation(
    shipment_id: int = Path(..., description="Internal ID of the shipment to evaluate")
):
    """
    Fetches the evaluation result of a shipment against active admin rules.
    Specifically checks if it matches the rule 'flag all residential deliveries >50 lbs for liftgate request'.
    """
    db = next(get_db())
    try:
        # Find the specific rule mentioned in the scenario/spec
        stmt_find_rule = text("SELECT id, name FROM admin_rules WHERE name = :rname AND is_active = 1")
        rule_row = db.execute(stmt_find_rule, {"rname": "flag all residential deliveries >50 lbs for liftgate request"}).fetchone()
        
        if not rule_row:
            raise HTTPException(status_code=404, detail="Admin rule not found or inactive.")
            
        rule_id, rule_name = rule_row

        # Check existing evaluation for this shipment and rule
        stmt_eval = text("""
            SELECT matched, details 
            FROM shipment_rule_evaluations 
            WHERE shipment_id = :sid AND admin_rule_id = :rid
            ORDER BY evaluated_at DESC LIMIT 1
        """)
        eval_row = db.execute(stmt_eval, {"sid": shipment_id, "rid": rule_id}).fetchone()

        if eval_row:
            matched_int, details = eval_row
            matched_bool = bool(matched_int)
            return EvaluationResponse(
                admin_rule_id=rule_id,
                rule_name=rule_name,
                matched=matched_bool,
                details=details or ""
            )
        else:
            # No evaluation recorded yet. We could calculate it dynamically here, 
            # but the spec implies retrieving the evaluation result. 
            # Given the DB is pre-populated with evaluations for relevant scenarios, 
            # returning a default non-matched state or calculating is possible.
            # Let's calculate dynamically to ensure correctness if not present.
            
            stmt_data = text("""
                SELECT s.weight_lbs, a.is_residential
                FROM shipments s
                JOIN addresses a ON s.consignee_address_id = a.id
                WHERE s.id = :sid
            """)
            data_row = db.execute(stmt_data, {"sid": shipment_id}).fetchone()
            
            if not data_row:
                 raise HTTPException(status_code=404, detail="Shipment not found")
                 
            weight, is_res = data_row
            
            # Logic: weight > 50 AND is_residential == 1
            did_match = (weight > 50) and (is_res == 1)
            det_msg = f"Calculated match: Weight={weight}, Residential={bool(is_res)}"
            
            return EvaluationResponse(
                admin_rule_id=rule_id,
                rule_name=rule_name,
                matched=did_match,
                details=det_msg
            )

    finally:
        db.close()


@app.get("/shipments/{shipment_id}/services", response_model=List[ShipmentServiceResponseItem], operation_id="get_shipment_services")
def get_shipment_services(
    shipment_id: int = Path(..., description="Internal ID of the shipment to retrieve services for")
):
    """
    Retrieves all services currently associated with a shipment.
    """
    db = next(get_db())
    try:
        # Verify shipment exists
        stmt_check = text("SELECT id FROM shipments WHERE id = :sid")
        if not db.execute(stmt_check, {"sid": shipment_id}).fetchone():
            raise HTTPException(status_code=404, detail="Shipment not found")

        stmt_select = text("""
            SELECT id, service_type, status, notes, requested_at
            FROM shipment_services
            WHERE shipment_id = :sid
            ORDER BY requested_at ASC
        """)
        rows = db.execute(stmt_select, {"sid": shipment_id}).fetchall()
        
        results = []
        for r in rows:
            sid, stype, stat, notes, req_at = r
            # Format datetime to string ISO-like format expected by frontend usually, 
            # though SQLite returns strings often. Ensuring consistency.
            results.append(ShipmentServiceResponseItem(
                id=sid,
                service_type=stype,
                status=stat,
                notes=notes,
                requested_at=str(req_at)
            ))
            
        return results
    finally:
        db.close()
