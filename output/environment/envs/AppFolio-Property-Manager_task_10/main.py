import os
from datetime import datetime, date
from typing import Optional

from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="AppFolio Property Manager API")


class TerminateLeaseRequest(BaseModel):
    termination_date: str
    termination_reason: str


class UpdateUnitAvailabilityRequest(BaseModel):
    new_status: str
    effective_date: str
    reason: Optional[str] = None


class DeactivateTenantPortalAccountRequest(BaseModel):
    deactivation_reason: Optional[str] = None


@app.post("/leases/{lease_id}/terminate", operation_id="terminate_lease")
def terminate_lease(
    lease_id: int = Path(...),
    request: TerminateLeaseRequest = Body(...)
):
    db = SessionLocal()
    try:
        # Get lease details
        lease_result = db.execute(
            text("""
                SELECT l.id, l.lease_number, l.unit_id, l.tenant_id, l.status, 
                       u.property_id, p.name as property_name
                FROM leases l
                JOIN units u ON l.unit_id = u.id
                JOIN properties p ON u.property_id = p.id
                WHERE l.id = :lease_id
            """),
            {"lease_id": lease_id}
        ).fetchone()

        if not lease_result:
            raise HTTPException(status_code=404, detail="Lease not found")

        if lease_result.status != "Active":
            raise HTTPException(status_code=400, detail="Only active leases can be terminated")

        unit_id = lease_result.unit_id
        tenant_id = lease_result.tenant_id
        current_user_id = os.environ.get("AWM_CURRENT_USER_ID")

        # Parse termination date
        try:
            term_date = datetime.strptime(request.termination_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid termination_date format. Use YYYY-MM-DD")

        now = datetime.now()

        # Update lease status to Ended
        db.execute(
            text("""
                UPDATE leases 
                SET status = 'Ended', 
                    termination_reason = :reason,
                    terminated_at = :terminated_at,
                    updated_at = :updated_at
                WHERE id = :lease_id
            """),
            {
                "reason": request.termination_reason,
                "terminated_at": now,
                "updated_at": now,
                "lease_id": lease_id
            }
        )

        # Insert lease event
        db.execute(
            text("""
                INSERT INTO lease_events (lease_id, event_type, event_date, description, performed_by_user_id, created_at)
                VALUES (:lease_id, 'terminated', :event_date, :description, :performed_by_user_id, :created_at)
            """),
            {
                "lease_id": lease_id,
                "event_date": term_date,
                "description": f"Lease terminated - {request.termination_reason}",
                "performed_by_user_id": int(current_user_id) if current_user_id else None,
                "created_at": now
            }
        )

        db.commit()

        return {
            "lease_id": lease_id,
            "unit_id": unit_id,
            "tenant_id": tenant_id,
            "message": f"Lease {lease_result.lease_number} has been terminated successfully."
        }
    finally:
        db.close()


@app.get("/leases/{lease_id}", operation_id="get_lease_details")
def get_lease_details(lease_id: int = Path(...)):
    db = SessionLocal()
    try:
        result = db.execute(
            text("""
                SELECT l.id, l.lease_number, l.unit_id, l.tenant_id, l.start_date, l.end_date,
                       l.status, l.termination_reason, l.terminated_at,
                       u.unit_number, p.name as property_name,
                       t.first_name as tenant_first_name, t.last_name as tenant_last_name, t.email as tenant_email
                FROM leases l
                JOIN units u ON l.unit_id = u.id
                JOIN properties p ON u.property_id = p.id
                JOIN tenants t ON l.tenant_id = t.id
                WHERE l.id = :lease_id
            """),
            {"lease_id": lease_id}
        ).fetchone()

        if not result:
            raise HTTPException(status_code=404, detail="Lease not found")

        response = {
            "id": result.id,
            "lease_number": result.lease_number,
            "unit_id": result.unit_id,
            "tenant_id": result.tenant_id,
            "start_date": str(result.start_date),
            "end_date": str(result.end_date),
            "status": result.status,
            "unit_number": result.unit_number,
            "property_name": result.property_name,
            "tenant_first_name": result.tenant_first_name,
            "tenant_last_name": result.tenant_last_name,
            "tenant_email": result.tenant_email,
        }

        if result.termination_reason is not None:
            response["termination_reason"] = result.termination_reason
        if result.terminated_at is not None:
            response["terminated_at"] = str(result.terminated_at)

        return response
    finally:
        db.close()


@app.put("/units/{unit_id}/availability", operation_id="update_unit_availability")
def update_unit_availability(
    unit_id: int = Path(...),
    request: UpdateUnitAvailabilityRequest = Body(...)
):
    db = SessionLocal()
    try:
        # Validate new_status
        valid_statuses = ["Occupied", "Available", "Notice", "Maintenance"]
        if request.new_status not in valid_statuses:
            raise HTTPException(status_code=400, detail=f"Invalid new_status. Must be one of: {valid_statuses}")

        # Get current unit info
        unit_result = db.execute(
            text("""
                SELECT id, availability_status, available_date
                FROM units
                WHERE id = :unit_id
            """),
            {"unit_id": unit_id}
        ).fetchone()

        if not unit_result:
            raise HTTPException(status_code=404, detail="Unit not found")

        previous_status = unit_result.availability_status
        current_user_id = os.environ.get("AWM_CURRENT_USER_ID")

        # Parse effective date
        try:
            eff_date = datetime.strptime(request.effective_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid effective_date format. Use YYYY-MM-DD")

        now = datetime.now()

        # Update unit availability status and available_date
        db.execute(
            text("""
                UPDATE units 
                SET availability_status = :new_status,
                    available_date = :available_date,
                    updated_at = :updated_at
                WHERE id = :unit_id
            """),
            {
                "new_status": request.new_status,
                "available_date": eff_date if request.new_status == "Available" else None,
                "updated_at": now,
                "unit_id": unit_id
            }
        )

        # Insert into unit_availability_history
        db.execute(
            text("""
                INSERT INTO unit_availability_history (unit_id, previous_status, new_status, effective_date, reason, changed_by_user_id, created_at)
                VALUES (:unit_id, :previous_status, :new_status, :effective_date, :reason, :changed_by_user_id, :created_at)
            """),
            {
                "unit_id": unit_id,
                "previous_status": previous_status,
                "new_status": request.new_status,
                "effective_date": eff_date,
                "reason": request.reason,
                "changed_by_user_id": int(current_user_id) if current_user_id else None,
                "created_at": now
            }
        )

        db.commit()

        return {
            "unit_id": unit_id,
            "previous_status": previous_status,
            "new_status": request.new_status,
            "effective_date": request.effective_date,
            "message": f"Unit {unit_id} availability updated to '{request.new_status}' effective {request.effective_date}."
        }
    finally:
        db.close()


@app.put("/tenants/{tenant_id}/portal_account/deactivate", operation_id="deactivate_tenant_portal_account")
def deactivate_tenant_portal_account(
    tenant_id: int = Path(...),
    request: DeactivateTenantPortalAccountRequest = Body(...)
):
    db = SessionLocal()
    try:
        # Check if tenant exists
        tenant_result = db.execute(
            text("""
                SELECT id, first_name, last_name, portal_account_active
                FROM tenants
                WHERE id = :tenant_id
            """),
            {"tenant_id": tenant_id}
        ).fetchone()

        if not tenant_result:
            raise HTTPException(status_code=404, detail="Tenant not found")

        # Get portal account
        portal_result = db.execute(
            text("""
                SELECT id, username, is_active
                FROM tenant_portal_accounts
                WHERE tenant_id = :tenant_id
            """),
            {"tenant_id": tenant_id}
        ).fetchone()

        if not portal_result:
            raise HTTPException(status_code=404, detail="Portal account not found for this tenant")

        if portal_result.is_active == 0:
            raise HTTPException(status_code=400, detail="Portal account is already deactivated")

        now = datetime.now()

        # Deactivate portal account
        db.execute(
            text("""
                UPDATE tenant_portal_accounts 
                SET is_active = 0,
                    deactivated_at = :deactivated_at,
                    deactivation_reason = :reason,
                    updated_at = :updated_at
                WHERE id = :portal_id
            """),
            {
                "deactivated_at": now,
                "reason": request.deactivation_reason or "Manual deactivation",
                "updated_at": now,
                "portal_id": portal_result.id
            }
        )

        # Update tenant's portal_account_active flag
        db.execute(
            text("""
                UPDATE tenants 
                SET portal_account_active = 0,
                    updated_at = :updated_at
                WHERE id = :tenant_id
            """),
            {
                "updated_at": now,
                "tenant_id": tenant_id
            }
        )

        db.commit()

        return {
            "tenant_id": tenant_id,
            "portal_account_id": portal_result.id,
            "message": f"Portal account for tenant {tenant_result.first_name} {tenant_result.last_name} has been deactivated."
        }
    finally:
        db.close()
