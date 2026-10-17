import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

engine: Engine = create_engine(DATABASE_PATH)

app = FastAPI(title="AeroNav Flight Ops Portal")


class SearchFlightsResponse(BaseModel):
    flight_id: int


class CancelFlightResponse(BaseModel):
    flight_id: int
    status: str
    maintenance_alert_id: int
    weight_balance_logs_archived: List[int]
    fuel_logs_archived: List[int]


@app.get("/flights/search", operation_id="search_flights", response_model=SearchFlightsResponse)
def search_flights(
    flight_number: str = Query(..., description="The flight number (e.g., FL-55802)"),
    tail_number: str = Query(..., description="The aircraft tail number (e.g., N456EF)")
):
    """Search for flights by flight number and tail number."""
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT f.id
                FROM flights f
                JOIN aircraft a ON f.aircraft_id = a.id
                WHERE f.flight_number = :flight_number AND a.tail_number = :tail_number
            """),
            {"flight_number": flight_number, "tail_number": tail_number}
        ).fetchone()

        if result is None:
            raise HTTPException(status_code=404, detail="Flight not found")

        return SearchFlightsResponse(flight_id=result[0])


@app.post("/flights/{flight_id}/cancel", operation_id="cancel_flight", response_model=CancelFlightResponse)
def cancel_flight(flight_id: int):
    """Cancel and close a flight record."""
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Current user ID not available")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid current user ID")

    with engine.begin() as conn:
        # 1. Verify flight exists and get details
        flight_result = conn.execute(
            text("""
                SELECT id, aircraft_id, status 
                FROM flights 
                WHERE id = :flight_id
            """),
            {"flight_id": flight_id}
        ).fetchone()

        if flight_result is None:
            raise HTTPException(status_code=404, detail="Flight not found")

        db_flight_id, aircraft_id, current_status = flight_result

        # Check if already cancelled/closed to avoid duplicate operations or invalid state transitions
        if current_status in ('cancelled', 'closed'):
             # Depending on business logic, we might allow re-closing or just return success. 
             # The spec implies performing the actions. If it's already done, we might still want to ensure logs are archived?
             # However, typically cancelling an already cancelled flight is a no-op or error.
             # Let's assume we proceed to ensure consistency or raise error. 
             # Given the task is specific, let's just proceed to update/insert if needed, but usually status check prevents double processing.
             # For safety, if it's already cancelled, we can just return the current state or raise 400.
             # Let's raise 400 Bad Request as it's an invalid transition.
             raise HTTPException(status_code=400, detail=f"Flight is already {current_status}")

        # 2. Update flight status to 'cancelled'
        conn.execute(
            text("""
                UPDATE flights 
                SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP 
                WHERE id = :flight_id
            """),
            {"flight_id": db_flight_id}
        )

        # 3. Confirm crew assignment cancellation
        # Update all active/assigned/confirmed crew assignments to 'cancelled'
        conn.execute(
            text("""
                UPDATE crew_assignments 
                SET status = 'cancelled', cancelled_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP 
                WHERE flight_id = :flight_id AND status IN ('assigned', 'confirmed')
            """),
            {"flight_id": db_flight_id}
        )

        # 4. Trigger maintenance alert for post-flight inspection (per 100-hour requirement)
        # Insert into maintenance_alerts
        # Alert type must be one of: 'post_flight_inspection','100_hour_inspection','routine','unscheduled'
        # Task says "trigger maintenance alert for post-flight inspection (per 100-hour requirement)"
        # This phrasing suggests using '100_hour_inspection' or 'post_flight_inspection'. 
        # Looking at existing data, '100_hour_inspection' is used for closed flights. 'post_flight_inspection' is also valid.
        # The task explicitly mentions "post-flight inspection" and "100-hour requirement". 
        # Let's use '100_hour_inspection' as it seems more specific to the "requirement" mentioned, 
        # OR 'post_flight_inspection'. Let's look at the schema CHECK constraint again.
        # CHECK (alert_type IN ('post_flight_inspection','100_hour_inspection','routine','unscheduled'))
        # I will use '100_hour_inspection' as it aligns with the "100-hour requirement" part of the prompt which often triggers specific alerts.
        # Actually, "post-flight inspection" is the action, "100-hour requirement" is the reason. 
        # Let's stick to '100_hour_inspection' as it's a distinct type in the enum.
        
        insert_alert_result = conn.execute(
            text("""
                INSERT INTO maintenance_alerts (aircraft_id, flight_id, alert_type, description, status, triggered_at, created_at, updated_at)
                VALUES (:aircraft_id, :flight_id, '100_hour_inspection', 'Post-flight inspection required due to flight cancellation (100-hour requirement)', 'open', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """),
            {"aircraft_id": aircraft_id, "flight_id": db_flight_id}
        )
        maintenance_alert_id = insert_alert_result.lastrowid

        # 5. Archive associated weight & balance logs
        wb_logs_result = conn.execute(
            text("""
                SELECT id FROM weight_balance_logs 
                WHERE flight_id = :flight_id AND status = 'active'
            """),
            {"flight_id": db_flight_id}
        ).fetchall()
        
        wb_log_ids = [row[0] for row in wb_logs_result]
        
        if wb_log_ids:
            placeholders = ",".join([f":wb_id_{i}" for i in range(len(wb_log_ids))])
            params = {f"wb_id_{i}": log_id for i, log_id in enumerate(wb_log_ids)}
            conn.execute(
                text(f"""
                    UPDATE weight_balance_logs 
                    SET status = 'archived', archived_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP 
                    WHERE id IN ({placeholders})
                """),
                params
            )

        # 6. Archive associated fuel logs
        fuel_logs_result = conn.execute(
            text("""
                SELECT id FROM fuel_logs 
                WHERE flight_id = :flight_id AND status = 'active'
            """),
            {"flight_id": db_flight_id}
        ).fetchall()
        
        fuel_log_ids = [row[0] for row in fuel_logs_result]
        
        if fuel_log_ids:
            placeholders = ",".join([f":fuel_id_{i}" for i in range(len(fuel_log_ids))])
            params = {f"fuel_id_{i}": log_id for i, log_id in enumerate(fuel_log_ids)}
            conn.execute(
                text(f"""
                    UPDATE fuel_logs 
                    SET status = 'archived', archived_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP 
                    WHERE id IN ({placeholders})
                """),
                params
            )

        # 7. Log the operation
        conn.execute(
            text("""
                INSERT INTO flight_operation_logs (flight_id, user_id, action, details, created_at)
                VALUES (:flight_id, :user_id, 'cancel', 'Flight cancelled and closed, crew assignments cancelled, maintenance alert triggered, logs archived.', CURRENT_TIMESTAMP)
            """),
            {"flight_id": db_flight_id, "user_id": current_user_id}
        )

        return CancelFlightResponse(
            flight_id=db_flight_id,
            status='cancelled',
            maintenance_alert_id=maintenance_alert_id,
            weight_balance_logs_archived=wb_log_ids,
            fuel_logs_archived=fuel_log_ids
        )
