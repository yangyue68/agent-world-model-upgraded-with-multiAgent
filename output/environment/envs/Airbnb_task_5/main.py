import os
from datetime import datetime, timedelta, date
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI(title="Airbnb Booking API")


class PropertyResponse(BaseModel):
    id: int
    host_id: int
    location_id: int
    title: str
    description: Optional[str]
    property_type: str
    nightly_rate: float
    max_guests: int
    bedrooms: int
    bathrooms: float
    free_cancellation_hours: Optional[int]
    created_at: str
    updated_at: str


class BookingRequestPayload(BaseModel):
    property_id: int
    check_in_date: str
    check_out_date: str
    max_nightly_rate: float
    required_free_cancellation_hours: int


class BookingResponse(BaseModel):
    id: int
    user_id: int
    property_id: int
    check_in_date: str
    check_out_date: str
    total_price: float
    status: str
    cancellation_deadline: str
    max_nightly_rate: float
    required_free_cancellation_hours: int
    created_at: str
    updated_at: str


def parse_iso_datetime(dt_str: str) -> datetime:
    if dt_str is None:
        return None
    # Handle both T...Z and space-separated forms
    dt_str = dt_str.replace("T", " ").replace("Z", "")
    try:
        return datetime.fromisoformat(dt_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {dt_str}")


@app.get("/properties", response_model=List[PropertyResponse], operation_id="find_cabins_in_asheville")
def find_cabins_in_asheville(
    city: str = Query(...),
    state: str = Query(...),
    property_type: Optional[str] = Query(None),
    max_nightly_rate: Optional[float] = Query(None),
    required_free_cancellation_hours: Optional[int] = Query(None),
):
    with engine.connect() as conn:
        query = """
            SELECT p.id, p.host_id, p.location_id, p.title, p.description, p.property_type,
                   p.nightly_rate, p.max_guests, p.bedrooms, p.bathrooms,
                   p.free_cancellation_hours, p.created_at, p.updated_at
            FROM properties p
            JOIN locations l ON p.location_id = l.id
            WHERE l.city = :city AND l.state = :state
        """
        params = {"city": city, "state": state}

        if property_type:
            query += " AND p.property_type = :property_type"
            params["property_type"] = property_type

        if max_nightly_rate is not None:
            query += " AND p.nightly_rate <= :max_nightly_rate"
            params["max_nightly_rate"] = max_nightly_rate

        if required_free_cancellation_hours is not None:
            query += " AND (p.free_cancellation_hours IS NULL OR p.free_cancellation_hours >= :required_free_cancellation_hours)"
            params["required_free_cancellation_hours"] = required_free_cancellation_hours

        result = conn.execute(text(query), params).fetchall()

        properties = []
        for row in result:
            properties.append(PropertyResponse(
                id=row[0],
                host_id=row[1],
                location_id=row[2],
                title=row[3],
                description=row[4],
                property_type=row[5],
                nightly_rate=float(row[6]),
                max_guests=int(row[7]),
                bedrooms=int(row[8]),
                bathrooms=float(row[9]),
                free_cancellation_hours=int(row[10]) if row[10] is not None else None,
                created_at=str(row[11]),
                updated_at=str(row[12]),
            ))
        return properties


@app.post("/bookings", response_model=BookingResponse, operation_id="submit_booking_request")
def submit_booking_request(payload: BookingRequestPayload):
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id:
        raise HTTPException(status_code=401, detail="No authenticated user")

    user_id = int(current_user_id)

    check_in = payload.check_in_date
    check_out = payload.check_out_date
    property_id = payload.property_id
    max_nightly_rate = payload.max_nightly_rate
    required_free_cancellation_hours = payload.required_free_cancellation_hours

    with engine.connect() as conn:
        # Verify property exists and matches criteria
        prop_query = """
            SELECT p.id, p.nightly_rate, p.free_cancellation_hours, p.property_type,
                   l.city, l.state
            FROM properties p
            JOIN locations l ON p.location_id = l.id
            WHERE p.id = :property_id
        """
        prop_result = conn.execute(text(prop_query), {"property_id": property_id}).fetchone()

        if not prop_result:
            raise HTTPException(status_code=404, detail="Property not found")

        nightly_rate = float(prop_result[1])
        free_cancel_hours = prop_result[2]
        property_type = prop_result[3]
        city = prop_result[4]
        state = prop_result[5]

        # Check constraints from the task: cabin in Asheville, NC
        if property_type != "cabin":
            raise HTTPException(status_code=400, detail="Property must be a cabin")
        if city.lower() != "asheville" or state.upper() != "NC":
            raise HTTPException(status_code=400, detail="Property must be in Asheville, NC")

        # Check max nightly rate constraint
        if nightly_rate > max_nightly_rate:
            raise HTTPException(status_code=400, detail=f"Nightly rate {nightly_rate} exceeds maximum allowed {max_nightly_rate}")

        # Check free cancellation hours constraint
        if free_cancel_hours is None or free_cancel_hours < required_free_cancellation_hours:
            raise HTTPException(status_code=400, detail=f"Free cancellation window {free_cancel_hours} does not meet requirement of {required_free_cancellation_hours} hours")

        # Parse dates
        ci_date = parse_iso_datetime(check_in)
        co_date = parse_iso_datetime(check_out)

        if co_date <= ci_date:
            raise HTTPException(status_code=400, detail="Check-out date must be after check-in date")

        # Calculate number of nights
        num_nights = (co_date - ci_date).days
        if num_nights <= 0:
            raise HTTPException(status_code=400, detail="Invalid date range")

        total_price = nightly_rate * num_nights

        # Check availability: no overlapping pending/confirmed bookings for this property
        overlap_query = """
            SELECT COUNT(*) FROM bookings
            WHERE property_id = :property_id
              AND status IN ('pending', 'confirmed')
              AND NOT (check_out_date <= :check_in_date OR check_in_date >= :check_out_date)
        """
        overlap_count = conn.execute(text(overlap_query), {
            "property_id": property_id,
            "check_in_date": check_in,
            "check_out_date": check_out,
        }).scalar()

        if overlap_count > 0:
            raise HTTPException(status_code=409, detail="Property is not available for the selected dates")

        # Compute cancellation deadline: booking creation time + required_free_cancellation_hours
        now = datetime.utcnow()
        cancellation_deadline = now + timedelta(hours=required_free_cancellation_hours)

        # Insert booking
        insert_query = """
            INSERT INTO bookings (user_id, property_id, check_in_date, check_out_date,
                                  total_price, status, cancellation_deadline,
                                  max_nightly_rate, required_free_cancellation_hours,
                                  created_at, updated_at)
            VALUES (:user_id, :property_id, :check_in_date, :check_out_date,
                    :total_price, :status, :cancellation_deadline,
                    :max_nightly_rate, :required_free_cancellation_hours,
                    :created_at, :updated_at)
        """
        conn.execute(text(insert_query), {
            "user_id": user_id,
            "property_id": property_id,
            "check_in_date": check_in,
            "check_out_date": check_out,
            "total_price": total_price,
            "status": "pending",
            "cancellation_deadline": cancellation_deadline.strftime("%Y-%m-%d %H:%M:%S"),
            "max_nightly_rate": max_nightly_rate,
            "required_free_cancellation_hours": required_free_cancellation_hours,
            "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        })
        conn.commit()

        # Fetch the newly inserted booking to get its ID
        fetch_query = """
            SELECT id, user_id, property_id, check_in_date, check_out_date,
                   total_price, status, cancellation_deadline,
                   max_nightly_rate, required_free_cancellation_hours,
                   created_at, updated_at
            FROM bookings
            WHERE user_id = :user_id AND property_id = :property_id
              AND check_in_date = :check_in_date AND check_out_date = :check_out_date
            ORDER BY id DESC LIMIT 1
        """
        booking_row = conn.execute(text(fetch_query), {
            "user_id": user_id,
            "property_id": property_id,
            "check_in_date": check_in,
            "check_out_date": check_out,
        }).fetchone()

        if not booking_row:
            raise HTTPException(status_code=500, detail="Failed to retrieve created booking")

        return BookingResponse(
            id=int(booking_row[0]),
            user_id=int(booking_row[1]),
            property_id=int(booking_row[2]),
            check_in_date=str(booking_row[3]),
            check_out_date=str(booking_row[4]),
            total_price=float(booking_row[5]),
            status=str(booking_row[6]),
            cancellation_deadline=str(booking_row[7]),
            max_nightly_rate=float(booking_row[8]),
            required_free_cancellation_hours=int(booking_row[9]),
            created_at=str(booking_row[10]),
            updated_at=str(booking_row[11]),
        )
