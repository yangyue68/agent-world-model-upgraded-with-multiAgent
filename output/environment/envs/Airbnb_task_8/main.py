import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class PropertyResponse(BaseModel):
    id: int
    host_id: int
    title: str
    description: Optional[str] = None
    address: Optional[str] = None
    city: str
    state: str
    country: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    property_type: Optional[str] = None
    max_guests: Optional[int] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[float] = None
    base_price_per_night: float
    cleaning_fee: Optional[float] = 0
    service_fee: Optional[float] = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CancellationPolicyResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    refund_percentage: float
    days_before_checkin: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class BookingDetailsResponse(BaseModel):
    id: int
    user_id: int
    property_id: int
    check_in_date: str
    check_out_date: str
    total_price: float
    status: str
    cancellation_policy_id: Optional[int] = None
    cancellation_date: Optional[datetime] = None
    refund_amount: Optional[float] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    property: PropertyResponse
    cancellation_policy: Optional[CancellationPolicyResponse] = None


class CancelBookingResponse(BaseModel):
    id: int
    user_id: int
    property_id: int
    check_in_date: str
    check_out_date: str
    total_price: float
    status: str
    cancellation_policy_id: Optional[int] = None
    cancellation_date: Optional[datetime] = None
    refund_amount: Optional[float] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


def _get_booking_with_details(booking_id: int):
    with engine.connect() as conn:
        # Fetch booking
        booking_row = conn.execute(
            text("""
                SELECT b.id, b.user_id, b.property_id, b.check_in_date, b.check_out_date,
                       b.total_price, b.status, b.cancellation_policy_id, b.cancellation_date,
                       b.refund_amount, b.created_at, b.updated_at
                FROM bookings b
                WHERE b.id = :booking_id
            """),
            {"booking_id": booking_id}
        ).fetchone()

        if not booking_row:
            raise HTTPException(status_code=404, detail="Booking not found")

        booking_data = dict(booking_row._mapping)

        # Fetch property
        prop_row = conn.execute(
            text("""
                SELECT p.id, p.host_id, p.title, p.description, p.address, p.city, p.state,
                       p.country, p.latitude, p.longitude, p.property_type, p.max_guests,
                       p.bedrooms, p.bathrooms, p.base_price_per_night, p.cleaning_fee,
                       p.service_fee, p.created_at, p.updated_at
                FROM properties p
                WHERE p.id = :property_id
            """),
            {"property_id": booking_data["property_id"]}
        ).fetchone()

        if not prop_row:
            raise HTTPException(status_code=404, detail="Property not found")

        property_data = dict(prop_row._mapping)

        # Fetch cancellation policy if exists
        cancellation_policy_data = None
        if booking_data["cancellation_policy_id"]:
            cp_row = conn.execute(
                text("""
                    SELECT cp.id, cp.name, cp.description, cp.refund_percentage,
                           cp.days_before_checkin, cp.created_at, cp.updated_at
                    FROM cancellation_policies cp
                    WHERE cp.id = :cp_id
                """),
                {"cp_id": booking_data["cancellation_policy_id"]}
            ).fetchone()

            if cp_row:
                cancellation_policy_data = dict(cp_row._mapping)

        return booking_data, property_data, cancellation_policy_data


@app.get("/bookings/{booking_id}", operation_id="get_booking_details", response_model=BookingDetailsResponse)
def get_booking_details(booking_id: int = Path(..., description="Unique identifier for the booking")):
    booking_data, property_data, cancellation_policy_data = _get_booking_with_details(booking_id)

    # Format dates as strings for response
    check_in_str = str(booking_data["check_in_date"]) if booking_data["check_in_date"] else None
    check_out_str = str(booking_data["check_out_date"]) if booking_data["check_out_date"] else None

    response = {
        "id": booking_data["id"],
        "user_id": booking_data["user_id"],
        "property_id": booking_data["property_id"],
        "check_in_date": check_in_str,
        "check_out_date": check_out_str,
        "total_price": booking_data["total_price"],
        "status": booking_data["status"],
        "cancellation_policy_id": booking_data["cancellation_policy_id"],
        "cancellation_date": booking_data["cancellation_date"],
        "refund_amount": booking_data["refund_amount"],
        "created_at": booking_data["created_at"],
        "updated_at": booking_data["updated_at"],
        "property": property_data,
        "cancellation_policy": cancellation_policy_data,
    }

    return response


@app.put("/bookings/{booking_id}/cancel", operation_id="cancel_booking", response_model=CancelBookingResponse)
def cancel_booking(booking_id: int = Path(..., description="Unique identifier for the booking to be cancelled")):
    with engine.connect() as conn:
        # Fetch booking
        booking_row = conn.execute(
            text("""
                SELECT b.id, b.user_id, b.property_id, b.check_in_date, b.check_out_date,
                       b.total_price, b.status, b.cancellation_policy_id, b.cancellation_date,
                       b.refund_amount, b.created_at, b.updated_at
                FROM bookings b
                WHERE b.id = :booking_id
            """),
            {"booking_id": booking_id}
        ).fetchone()

        if not booking_row:
            raise HTTPException(status_code=404, detail="Booking not found")

        booking_data = dict(booking_row._mapping)

        # Check if already cancelled
        if booking_data["status"] == "cancelled":
            raise HTTPException(status_code=400, detail="Booking is already cancelled")

        # Only confirmed or pending bookings can be cancelled
        if booking_data["status"] not in ("confirmed", "pending"):
            raise HTTPException(status_code=400, detail=f"Cannot cancel a booking with status '{booking_data['status']}'")

        # Calculate refund based on cancellation policy
        refund_amount = 0.0
        if booking_data["cancellation_policy_id"]:
            cp_row = conn.execute(
                text("""
                    SELECT cp.refund_percentage, cp.days_before_checkin
                    FROM cancellation_policies cp
                    WHERE cp.id = :cp_id
                """),
                {"cp_id": booking_data["cancellation_policy_id"]}
            ).fetchone()

            if cp_row:
                refund_percentage = cp_row[0]
                days_before_checkin = cp_row[1]

                # Determine current date and check-in date
                now = datetime.now(timezone.utc)
                check_in_date = booking_data["check_in_date"]
                if isinstance(check_in_date, str):
                    check_in_dt = datetime.fromisoformat(check_in_date.replace('Z', '+00:00'))
                else:
                    check_in_dt = check_in_date

                # Ensure both are timezone-aware for comparison
                if check_in_dt.tzinfo is None:
                    check_in_dt = check_in_dt.replace(tzinfo=timezone.utc)

                # Calculate days until check-in
                delta = check_in_dt - now
                days_until_checkin = delta.days

                # Apply refund logic based on policy
                if days_until_checkin >= days_before_checkin:
                    refund_amount = round(booking_data["total_price"] * (refund_percentage / 100.0), 2)
                else:
                    # If within the restricted window, no refund per typical policies
                    # But some policies might have partial refunds; here we assume full or none based on threshold
                    refund_amount = 0.0

        # Update booking
        cancellation_date = datetime.now(timezone.utc)
        updated_at = datetime.now(timezone.utc)

        conn.execute(
            text("""
                UPDATE bookings
                SET status = 'cancelled',
                    cancellation_date = :cancellation_date,
                    refund_amount = :refund_amount,
                    updated_at = :updated_at
                WHERE id = :booking_id
            """),
            {
                "cancellation_date": cancellation_date,
                "refund_amount": refund_amount,
                "updated_at": updated_at,
                "booking_id": booking_id,
            }
        )

        # Also update associated payment to refunded if it was completed
        conn.execute(
            text("""
                UPDATE payments
                SET status = 'refunded',
                    updated_at = :updated_at
                WHERE booking_id = :booking_id AND status = 'completed'
            """),
            {
                "updated_at": updated_at,
                "booking_id": booking_id,
            }
        )

        # Create refund record if refund amount > 0
        if refund_amount > 0:
            # Find the payment ID
            pay_row = conn.execute(
                text("""
                    SELECT id FROM payments
                    WHERE booking_id = :booking_id
                """),
                {"booking_id": booking_id}
            ).fetchone()

            if pay_row:
                payment_id = pay_row[0]
                conn.execute(
                    text("""
                        INSERT INTO refunds (payment_id, booking_id, amount, status, refund_date, created_at, updated_at)
                        VALUES (:payment_id, :booking_id, :amount, 'completed', :refund_date, :created_at, :updated_at)
                    """),
                    {
                        "payment_id": payment_id,
                        "booking_id": booking_id,
                        "amount": refund_amount,
                        "refund_date": cancellation_date,
                        "created_at": cancellation_date,
                        "updated_at": cancellation_date,
                    }
                )

        conn.commit()

        # Return updated booking data
        check_in_str = str(booking_data["check_in_date"]) if booking_data["check_in_date"] else None
        check_out_str = str(booking_data["check_out_date"]) if booking_data["check_out_date"] else None

        response = {
            "id": booking_data["id"],
            "user_id": booking_data["user_id"],
            "property_id": booking_data["property_id"],
            "check_in_date": check_in_str,
            "check_out_date": check_out_str,
            "total_price": booking_data["total_price"],
            "status": "cancelled",
            "cancellation_policy_id": booking_data["cancellation_policy_id"],
            "cancellation_date": cancellation_date,
            "refund_amount": refund_amount,
            "created_at": booking_data["created_at"],
            "updated_at": updated_at,
        }

        return response
