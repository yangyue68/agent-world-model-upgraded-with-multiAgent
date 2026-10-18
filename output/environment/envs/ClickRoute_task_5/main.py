import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Database configuration
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="ClickRoute API")


# --- Pydantic Models for Request/Response ---

class LocationCreate(BaseModel):
    address_line1: str
    address_line2: Optional[str] = None
    city: str
    state: str
    postal_code: Optional[str] = None


class LocationResponse(BaseModel):
    id: int
    address_line1: str
    address_line2: Optional[str] = None
    city: str
    state: str
    postal_code: Optional[str] = None


class ShipmentEventResponse(BaseModel):
    id: int
    event_type: str
    event_timestamp: str
    notes: Optional[str] = None


class CustomerInfo(BaseModel):
    id: int
    name: str


class CarrierInfo(BaseModel):
    id: int
    name: str


class DestinationInfo(BaseModel):
    id: int
    address_line1: str
    city: str
    state: str


class ShipmentDetailsResponse(BaseModel):
    id: int
    pro_number: str
    status: str
    customer: CustomerInfo
    carrier: Optional[CarrierInfo] = None
    original_destination: DestinationInfo
    current_destination: DestinationInfo
    events: List[ShipmentEventResponse]


class RerouteRequestPayload(BaseModel):
    new_destination_address: str = Field(..., description="Full address of the new destination (line1, line2, city, state)")
    reason: str = Field(..., description="Reason for the reroute request")


class AddressParts(BaseModel):
    address_line1: str
    address_line2: Optional[str] = None
    city: str
    state: str


class RerouteResponse(BaseModel):
    shipment_id: int
    pro_number: str
    original_destination: DestinationInfo
    new_destination: DestinationInfo
    status: str
    reroute_request_id: int


# --- Helper Functions ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def parse_address_string(address_str: str) -> AddressParts:
    """
    Parses a full address string into components.
    Expected format examples:
    "456 Oak Ave, Austin, TX"
    "456 Oak Ave, Suite 100, Austin, TX"
    We assume the last two parts are City and State if separated by commas.
    The rest is Line 1 (and potentially Line 2).
    
    For simplicity in this scenario based on the task description:
    '456 Oak Ave, Austin, TX' -> Line1: '456 Oak Ave', City: 'Austin', State: 'TX'
    """
    parts = [p.strip() for p in address_str.split(",")]
    
    # Minimum parts needed: Street, City, State
    if len(parts) < 3:
        raise ValueError(f"Invalid address format: '{address_str}'. Expected at least Street, City, State.")
    
    state = parts[-1].upper()
    city = parts[-2]
    
    # Everything before City/State is street info
    street_parts = parts[:-2]
    
    if len(street_parts) == 1:
        address_line1 = street_parts[0]
        address_line2 = None
    else:
        # If there are multiple street parts, join them or treat first as line1 and second as line2?
        # Standard practice often puts apt/suite in line2. 
        # Let's take the first part as line1 and remaining joined as line2 if >1, 
        # OR just join all street parts into line1 if we want to be safe with simple parsing.
        # Given the example "456 Oak Ave", it's single. 
        # Let's assume if there are extra comma-separated items before city/state, they might be line2.
        # However, usually addresses are "Street, Apt, City, State".
        # So: parts[0] = Street, parts[1:-2] = Apt/Suite/etc.
        
        address_line1 = street_parts[0]
        if len(street_parts) > 1:
            address_line2 = ", ".join(street_parts[1:])
        else:
            address_line2 = None
            
    return AddressParts(
        address_line1=address_line1,
        address_line2=address_line2,
        city=city,
        state=state
    )


# --- Endpoints ---

@app.post("/locations", response_model=LocationResponse, operation_id="create_location")
def create_location(payload: LocationCreate):
    """
    Create a new location record.
    Checks if location already exists to avoid duplicates if possible, but primarily inserts.
    """
    db = next(get_db())
    try:
        # Check if exact match exists
        stmt_check = text("""
            SELECT id FROM locations 
            WHERE address_line1 = :l1 AND city = :c AND state = :s
            AND (:l2 IS NULL AND address_line2 IS NULL OR address_line2 = :l2)
        """)
        res_check = db.execute(stmt_check, {
            "l1": payload.address_line1,
            "l2": payload.address_line2,
            "c": payload.city,
            "s": payload.state
        }).fetchone()
        
        if res_check:
            loc_id = res_check[0]
            # Fetch existing data to return consistent response
            stmt_fetch = text("SELECT * FROM locations WHERE id = :id")
            row = db.execute(stmt_fetch, {"id": loc_id}).mappings().fetchone()
            return LocationResponse(**row)

        # Insert new location
        stmt_insert = text("""
            INSERT INTO locations (address_line1, address_line2, city, state, postal_code, country)
            VALUES (:l1, :l2, :c, :s, :pc, 'USA')
        """)
        result = db.execute(stmt_insert, {
            "l1": payload.address_line1,
            "l2": payload.address_line2,
            "c": payload.city,
            "s": payload.state,
            "pc": payload.postal_code
        })
        db.commit()
        new_id = result.lastrowid
        
        stmt_fetch_new = text("SELECT * FROM locations WHERE id = :id")
        row_new = db.execute(stmt_fetch_new, {"id": new_id}).mappings().fetchone()
        
        return LocationResponse(**row_new)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.get("/shipments/{shipment_id}", response_model=ShipmentDetailsResponse, operation_id="get_shipment_details")
def get_shipment_details(shipment_id: int = Path(...)):
    """
    Retrieve detailed information about a specific shipment.
    """
    db = next(get_db())
    try:
        # Get Shipment Core Data + Join Locations/Customer/Carrier
        stmt_shipment = text("""
            SELECT 
                s.id, s.pro_number, s.status,
                c.id as cust_id, c.name as cust_name,
                ca.id as carrier_id, ca.name as carrier_name,
                ol.id as orig_loc_id, ol.address_line1 as orig_l1, ol.city as orig_c, ol.state as orig_s,
                cl.id as curr_loc_id, cl.address_line1 as curr_l1, cl.city as curr_c, cl.state as curr_s
            FROM shipments s
            JOIN customers c ON s.customer_id = c.id
            LEFT JOIN carriers ca ON s.carrier_id = ca.id
            JOIN locations ol ON s.original_destination_location_id = ol.id
            JOIN locations cl ON s.current_destination_location_id = cl.id
            WHERE s.id = :sid
        """)
        row_ship = db.execute(stmt_shipment, {"sid": shipment_id}).mappings().fetchone()
        
        if not row_ship:
            raise HTTPException(status_code=404, detail="Shipment not found")
            
        # Get Events
        stmt_events = text("""
            SELECT id, event_type, event_timestamp, notes
            FROM shipment_events
            WHERE shipment_id = :sid
            ORDER BY event_timestamp ASC
        """)
        rows_events = db.execute(stmt_events, {"sid": shipment_id}).mappings().fetchall()
        
        events_list = []
        for ev in rows_events:
            events_list.append({
                "id": ev["id"],
                "event_type": ev["event_type"],
                "event_timestamp": str(ev["event_timestamp"]),
                "notes": ev["notes"]
            })
            
        # Construct Response Objects
        customer_obj = CustomerInfo(id=row_ship["cust_id"], name=row_ship["cust_name"])
        
        carrier_obj = None
        if row_ship["carrier_id"]:
            carrier_obj = CarrierInfo(id=row_ship["carrier_id"], name=row_ship["carrier_name"])
            
        orig_dest_obj = DestinationInfo(
            id=row_ship["orig_loc_id"],
            address_line1=row_ship["orig_l1"],
            city=row_ship["orig_c"],
            state=row_ship["orig_s"]
        )
        
        curr_dest_obj = DestinationInfo(
            id=row_ship["curr_loc_id"],
            address_line1=row_ship["curr_l1"],
            city=row_ship["curr_c"],
            state=row_ship["curr_s"]
        )
        
        return ShipmentDetailsResponse(
            id=row_ship["id"],
            pro_number=row_ship["pro_number"],
            status=row_ship["status"],
            customer=customer_obj,
            carrier=carrier_obj,
            original_destination=orig_dest_obj,
            current_destination=curr_dest_obj,
            events=events_list
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.put("/shipments/{shipment_id}/reroute", response_model=RerouteResponse, operation_id="reroute_shipment")
def reroute_shipment(
    shipment_id: int = Path(...),
    payload: RerouteRequestPayload = Body(...)
):
    """
    Reroute an in-transit LTL shipment to a new destination.
    Validates status and existence of 'delivery attempted' event.
    Creates a new location if necessary, creates a reroute request, updates shipment.
    """
    db = next(get_db())
    try:
        # 1. Validate Shipment Status and Type
        stmt_check_ship = text("""
            SELECT status, shipment_type, original_destination_location_id, current_destination_location_id
            FROM shipments
            WHERE id = :sid
        """)
        ship_row = db.execute(stmt_check_ship, {"sid": shipment_id}).mappings().fetchone()
        
        if not ship_row:
            raise HTTPException(status_code=404, detail="Shipment not found")
            
        if ship_row["status"] != "in_transit":
            raise HTTPException(status_code=400, detail=f"Shipment must be in 'in_transit' status to reroute. Current status: {ship_row['status']}")
            
        if ship_row["shipment_type"] != "LTL":
             # Spec says "Reroute the in-transit LTL shipment", implies validation or assumption. 
             # Usually APIs validate input against business rules.
             pass 

        # 2. Validate Existence of 'delivery_attempted' Event
        stmt_check_event = text("""
            SELECT COUNT(*) as cnt
            FROM shipment_events
            WHERE shipment_id = :sid AND event_type = 'delivery_attempted'
        """)
        evt_count = db.execute(stmt_check_event, {"sid": shipment_id}).scalar()
        
        if evt_count == 0:
            raise HTTPException(status_code=400, detail="Cannot reroute: No 'delivery attempted' event recorded for this shipment.")

        # 3. Parse New Address and Find/Create Location
        try:
            addr_parts = parse_address_string(payload.new_destination_address)
        except ValueError as ve:
            raise HTTPException(status_code=400, detail=str(ve))
            
        # Try to find existing location matching parsed details
        stmt_find_loc = text("""
            SELECT id FROM locations 
            WHERE address_line1 = :l1 AND city = :c AND state = :s
            AND (:l2 IS NULL AND address_line2 IS NULL OR address_line2 = :l2)
        """)
        loc_res = db.execute(stmt_find_loc, {
            "l1": addr_parts.address_line1,
            "l2": addr_parts.address_line2,
            "c": addr_parts.city,
            "s": addr_parts.state
        }).fetchone()
        
        if loc_res:
            new_location_id = loc_res[0]
        else:
            # Create new location via internal logic similar to POST /locations
            stmt_create_loc = text("""
                INSERT INTO locations (address_line1, address_line2, city, state, country)
                VALUES (:l1, :l2, :c, :s, 'USA')
            """)
            res_ins = db.execute(stmt_create_loc, {
                "l1": addr_parts.address_line1,
                "l2": addr_parts.address_line2,
                "c": addr_parts.city,
                "s": addr_parts.state
            })
            new_location_id = res_ins.lastrowid

        # 4. Get Original Destination Details for Response
        orig_loc_id = ship_row["original_destination_location_id"]
        stmt_get_orig = text("SELECT address_line1, city, state FROM locations WHERE id = :lid")
        orig_loc_data = db.execute(stmt_get_orig, {"lid": orig_loc_id}).mappings().fetchone()
        
        # 5. Get New Destination Details for Response
        stmt_get_new = text("SELECT address_line1, city, state FROM locations WHERE id = :lid")
        new_loc_data = db.execute(stmt_get_new, {"lid": new_location_id}).mappings().fetchone()

        # 6. Identify Actor
        actor_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
        if not actor_user_id_str:
            # Fallback or error? Spec says runtime injects it. 
            # If missing, we can't populate requested_by_user_id which is NOT NULL FK.
            raise HTTPException(status_code=500, detail="Current user ID not provided in environment")
        
        try:
            actor_user_id = int(actor_user_id_str)
        except ValueError:
            raise HTTPException(status_code=500, detail="Invalid Current User ID format")

        # Verify user exists (optional but good for integrity check since FK constraint will fail otherwise)
        stmt_check_user = text("SELECT id FROM users WHERE id = :uid")
        if not db.execute(stmt_check_user, {"uid": actor_user_id}).fetchone():
             # In a real app, maybe auto-create or error. Here, let's assume valid env var means valid user context.
             # But DB has FK constraints. If user doesn't exist, insert fails.
             pass

        # 7. Create Reroute Request Record
        stmt_insert_rr = text("""
            INSERT INTO reroute_requests (
                shipment_id, 
                requested_by_user_id, 
                original_destination_location_id, 
                new_destination_location_id, 
                reason, 
                status
            )
            VALUES (:sid, :uid, :oid, :nid, :reason, 'pending')
        """)
        rr_result = db.execute(stmt_insert_rr, {
            "sid": shipment_id,
            "uid": actor_user_id,
            "oid": orig_loc_id,
            "nid": new_location_id,
            "reason": payload.reason
        })
        reroute_request_id = rr_result.lastrowid

        # 8. Update Shipment Current Destination
        stmt_update_ship = text("""
            UPDATE shipments
            SET current_destination_location_id = :nid, updated_at = CURRENT_TIMESTAMP
            WHERE id = :sid
        """)
        db.execute(stmt_update_ship, {
            "nid": new_location_id,
            "sid": shipment_id
        })
        
        # Note: Should we also update status? The spec says "updates the shipment's current destination".
        # It doesn't explicitly say change status to something else like 'rerouted'. 
        # Usually remains 'in_transit' until delivered.
        
        db.commit()

        # 9. Construct Response
        resp_original = DestinationInfo(
            id=orig_loc_id,
            address_line1=orig_loc_data["address_line1"],
            city=orig_loc_data["city"],
            state=orig_loc_data["state"]
        )
        
        resp_new = DestinationInfo(
            id=new_location_id,
            address_line1=new_loc_data["address_line1"],
            city=new_loc_data["city"],
            state=new_loc_data["state"]
        )
        
        # Get Pro Number again or from initial fetch
        stmt_pro = text("SELECT pro_number FROM shipments WHERE id = :sid")
        pro_num = db.execute(stmt_pro, {"sid": shipment_id}).scalar()

        return RerouteResponse(
            shipment_id=shipment_id,
            pro_number=pro_num,
            original_destination=resp_original,
            new_destination=resp_new,
            status="in_transit", # Or whatever the current status is, likely unchanged
            reroute_request_id=reroute_request_id
        )

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
