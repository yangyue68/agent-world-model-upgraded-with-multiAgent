import os
from typing import List, Optional

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI(title="CargoChain Freight Audit Suite")


class AuditFlag(BaseModel):
    flag_type: str
    message: str


class LineItemAudit(BaseModel):
    line_item_id: int
    nmfc_code: str
    description: Optional[str]
    quantity: int
    weight: Optional[float]
    declared_freight_class: Optional[str]
    validated_freight_class: Optional[str]
    is_nmfc_active: bool
    is_class_consistent: bool
    validation_notes: Optional[str]
    audit_flags: List[AuditFlag]


class ShipmentAudit(BaseModel):
    shipment_id: int
    shipment_number: str
    ship_date: str
    carrier_name: Optional[str]
    line_items: List[LineItemAudit]


@app.get("/shipments/audit", response_model=List[ShipmentAudit], operation_id="audit_shipments_by_zip_and_date")
def audit_shipments_by_zip_and_date(
    origin_zip: str = Query(..., description="Origin ZIP code"),
    destination_zip: str = Query(..., description="Destination ZIP code"),
    start_date: str = Query(..., description="Start date in YYYY-MM-DD format"),
    end_date: str = Query(..., description="End date in YYYY-MM-DD format"),
):
    """
    Retrieve and validate shipments between specified ZIP codes within a date range.
    
    Fetch all shipments originating from the specified origin ZIP and destined to the 
    specified destination ZIP between the given dates. For each shipment, check if NMFC 
    codes map to active items in CargoChain's certified NMFC database and flag freight 
    class inconsistencies with NMFC Item 181000 (office furniture) or NMFC Item 142000 
    (plastic packaging).
    """
    
    # Define expected classes for specific NMFC items based on task requirements
    EXPECTED_CLASSES = {
        "181000": "125",  # Office Furniture
        "142000": "100"   # Plastic Packaging
    }

    query_sql = text("""
        SELECT 
            s.id AS shipment_id,
            s.shipment_number,
            s.ship_date,
            c.name AS carrier_name,
            li.id AS line_item_id,
            n.nmfc_code,
            li.description,
            li.quantity,
            li.weight,
            li.declared_freight_class,
            n.freight_class AS db_freight_class,
            n.is_active AS db_is_active
        FROM shipments s
        JOIN carriers c ON s.carrier_id = c.id
        LEFT JOIN shipment_line_items li ON s.id = li.shipment_id
        LEFT JOIN nmfc_items n ON li.nmfc_item_id = n.id
        WHERE s.origin_zip = :origin_zip
          AND s.destination_zip = :destination_zip
          AND s.ship_date >= :start_date
          AND s.ship_date <= :end_date
        ORDER BY s.id, li.id
    """)

    results = []
    current_shipment = None
    
    with engine.connect() as conn:
        rows = conn.execute(query_sql, {
            "origin_zip": origin_zip,
            "destination_zip": destination_zip,
            "start_date": start_date,
            "end_date": end_date
        }).fetchall()

        for row in rows:
            # Handle case where there are no line items (though unlikely given schema FKs)
            if row.line_item_id is None:
                continue
                
            # Initialize new shipment object if ID changes
            if current_shipment is None or current_shipment["shipment_id"] != row.shipment_id:
                if current_shipment:
                    results.append(current_shipment)
                
                current_shipment = {
                    "shipment_id": row.shipment_id,
                    "shipment_number": row.shipment_number,
                    "ship_date": str(row.ship_date),
                    "carrier_name": row.carrier_name,
                    "line_items": []
                }

            # Process Line Item Validation
            nmfc_code = row.nmfc_code if row.nmfc_code else ""
            
            # Determine Active Status
            # If nmfc_code exists but item not found in DB, it's inactive/missing
            # If found, use DB value
            is_nmfc_active = False
            if row.db_is_active is not None:
                is_nmfc_active = bool(row.db_is_active)
            
            # Determine Class Consistency
            is_class_consistent = True
            validation_notes = []
            audit_flags = []

            declared_class = row.declared_freight_class
            
            # Logic for consistency check against known special items
            if nmfc_code in EXPECTED_CLASSES:
                expected_class = EXPECTED_CLASSES[nmfc_code]
                if declared_class == expected_class:
                    is_class_consistent = True
                else:
                    is_class_consistent = False
                    audit_flags.append({
                        "flag_type": "class_mismatch",
                        "message": f"Declared class '{declared_class}' does not match expected class '{expected_class}' for NMFC {nmfc_code}."
                    })
            elif row.db_freight_class is not None:
                # General check against DB if available
                if declared_class == row.db_freight_class:
                    is_class_consistent = True
                else:
                    is_class_consistent = False
                    audit_flags.append({
                        "flag_type": "class_mismatch",
                        "message": f"Declared class '{declared_class}' differs from certified class '{row.db_freight_class}'."
                    })
            else:
                # No reference data available
                is_class_consistent = False
                audit_flags.append({
                    "flag_type": "missing_nmfc",
                    "message": "NMFC code not found in certified database; cannot verify class consistency."
                })

            # Flag Inactive NMFC
            if not is_nmfc_active:
                 audit_flags.append({
                    "flag_type": "inactive_nmfc",
                    "message": f"NMFC Code {nmfc_code} is marked as inactive in the certified database."
                })

            # Construct Validated Freight Class
            # If consistent and active, we can assume the declared one is correct OR use DB one?
            # Usually "Validated" implies what it *should* be.
            # If mismatch, validated should probably be the correct one from DB/Special rules.
            # If missing/inactive, maybe null or keep declared? Let's stick to logic:
            # If we know the right class (DB or Special), use that. Otherwise fallback to declared or None.
            
            final_validated_class = None
            if nmfc_code in EXPECTED_CLASSES:
                final_validated_class = EXPECTED_CLASSES[nmfc_code]
            elif row.db_freight_class is not None:
                final_validated_class = row.db_freight_class
            else:
                # Cannot determine truth, leave as None or echo declared? 
                # Spec says "validated_freight_class". If unknown, likely None.
                final_validated_class = None

            notes_str = "; ".join(validation_notes) if validation_notes else None

            line_item_obj = {
                "line_item_id": row.line_item_id,
                "nmfc_code": nmfc_code,
                "description": row.description,
                "quantity": row.quantity,
                "weight": float(row.weight) if row.weight is not None else None,
                "declared_freight_class": declared_class,
                "validated_freight_class": final_validated_class,
                "is_nmfc_active": is_nmfc_active,
                "is_class_consistent": is_class_consistent,
                "validation_notes": notes_str,
                "audit_flags": audit_flags
            }
            
            current_shipment["line_items"].append(line_item_obj)

        # Append last shipment
        if current_shipment:
            results.append(current_shipment)

    return results
