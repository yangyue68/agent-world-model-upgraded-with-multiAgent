from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
import os

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class PropertyResponse(BaseModel):
    id: int
    title: str
    description: str | None
    property_type: str
    city: str
    state: str
    country: str
    latitude: float | None
    longitude: float | None
    max_guests: int
    bedrooms: int
    bathrooms: float
    base_price_per_night: float
    cleaning_fee: float
    service_fee_percent: float
    pet_friendly: int
    total_cost: float


class AvailabilityResponse(BaseModel):
    available: bool


class BookingRequest(BaseModel):
    property_id: int
    guest_id: int
    check_in_date: str
    check_out_date: str
    num_adults: int
    num_pets: int = 0


class BookingResponse(BaseModel):
    id: int
    property_id: int
    guest_id: int
    check_in_date: str
    check_out_date: str
    num_adults: int
    num_children: int
    num_infants: int
    num_pets: int
    total_nights: int
    nightly_rate: float
    subtotal: float
    cleaning_fee: float
    service_fee: float
    total_cost: float
    status: str


def calculate_total_cost(base_price_per_night: float, cleaning_fee: float, service_fee_percent: float, nights: int) -> float:
    subtotal = base_price_per_night * nights
    service_fee = subtotal * (service_fee_percent / 100.0)
    total_cost = subtotal + cleaning_fee + service_fee
    return round(total_cost, 2)


def is_property_available(conn, property_id: int, check_in_date: str, check_out_date: str) -> bool:
    query = text("""
        SELECT COUNT(*) as cnt FROM bookings 
        WHERE property_id = :property_id 
        AND status IN ('pending', 'confirmed')
        AND NOT (check_out_date <= :check_in_date OR check_in_date >= :check_out_date)
    """)
    result = conn.execute(query, {"property_id": property_id, "check_in_date": check_in_date, "check_out_date": check_out_date})
    row = result.fetchone()
    return row[0] == 0 if row else True


@app.get("/properties", response_model=list[PropertyResponse], operation_id="find_pet_friendly_villas_in_sedona")
def find_pet_friendly_villas_in_sedona(
    city: str = Query(...),
    state: str = Query(...),
    property_type: str = Query(...),
    pet_friendly: int = Query(...),
    check_in_date: str = Query(...),
    check_out_date: str = Query(...),
    max_adults: int = Query(...),
    max_budget: float = Query(...)
):
    with engine.connect() as conn:
        # Parse dates to compute nights
        from datetime import datetime
        ci = datetime.strptime(check_in_date, "%Y-%m-%d")
        co = datetime.strptime(check_out_date, "%Y-%m-%d")
        nights = (co - ci).days
        if nights <= 0:
            raise HTTPException(status_code=400, detail="Check-out date must be after check-in date")

        query = text("""
            SELECT id, title, description, property_type, city, state, country, latitude, longitude,
                   max_guests, bedrooms, bathrooms, base_price_per_night, cleaning_fee, service_fee_percent, pet_friendly
            FROM properties
            WHERE city = :city
              AND state = :state
              AND property_type = :property_type
              AND pet_friendly = :pet_friendly
              AND is_active = 1
              AND max_guests >= :max_adults
        """)
        params = {
            "city": city,
            "state": state,
            "property_type": property_type,
            "pet_friendly": pet_friendly,
            "max_adults": max_adults
        }
        results = conn.execute(query, params).fetchall()

        filtered_properties = []
        for row in results:
            prop_id = row.id
            base_price = row.base_price_per_night
            cleaning = row.cleaning_fee or 0
            svc_pct = row.service_fee_percent or 0

            total_cost = calculate_total_cost(base_price, cleaning, svc_pct, nights)

            if total_cost > max_budget:
                continue

            # Check availability
            if not is_property_available(conn, prop_id, check_in_date, check_out_date):
                continue

            filtered_properties.append(PropertyResponse(
                id=row.id,
                title=row.title,
                description=row.description,
                property_type=row.property_type,
                city=row.city,
                state=row.state,
                country=row.country,
                latitude=row.latitude,
                longitude=row.longitude,
                max_guests=row.max_guests,
                bedrooms=row.bedrooms,
                bathrooms=row.bathrooms,
                base_price_per_night=row.base_price_per_night,
                cleaning_fee=row.cleaning_fee,
                service_fee_percent=row.service_fee_percent,
                pet_friendly=row.pet_friendly,
                total_cost=total_cost
            ))

        return filtered_properties


@app.get("/properties/{property_id}/availability", response_model=AvailabilityResponse, operation_id="check_property_availability")
def check_property_availability(
    property_id: int,
    check_in_date: str = Query(...),
    check_out_date: str = Query(...)
):
    with engine.connect() as conn:
        # Verify property exists
        prop_query = text("SELECT id FROM properties WHERE id = :property_id AND is_active = 1")
        prop_result = conn.execute(prop_query, {"property_id": property_id}).fetchone()
        if not prop_result:
            raise HTTPException(status_code=404, detail="Property not found or inactive")

        available = is_property_available(conn, property_id, check_in_date, check_out_date)
        return AvailabilityResponse(available=available)


@app.post("/bookings", response_model=BookingResponse, operation_id="request_stay_booking")
def request_stay_booking(body: BookingRequest):
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id:
        raise HTTPException(status_code=400, detail="No authenticated user")

    try:
        guest_id = int(current_user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    with engine.connect() as conn:
        # Fetch property details
        prop_query = text("""
            SELECT id, base_price_per_night, cleaning_fee, service_fee_percent, max_guests, pet_friendly, is_active
            FROM properties WHERE id = :property_id
        """)
        prop = conn.execute(prop_query, {"property_id": body.property_id}).fetchone()
        if not prop:
            raise HTTPException(status_code=404, detail="Property not found")
        if not prop.is_active:
            raise HTTPException(status_code=400, detail="Property is not active")
        if prop.max_guests < body.num_adults:
            raise HTTPException(status_code=400, detail="Property cannot accommodate the requested number of adults")
        if body.num_pets > 0 and prop.pet_friendly != 1:
            raise HTTPException(status_code=400, detail="Property is not pet-friendly")

        # Calculate nights
        from datetime import datetime
        ci = datetime.strptime(body.check_in_date, "%Y-%m-%d")
        co = datetime.strptime(body.check_out_date, "%Y-%m-%d")
        nights = (co - ci).days
        if nights <= 0:
            raise HTTPException(status_code=400, detail="Check-out date must be after check-in date")

        # Check availability
        if not is_property_available(conn, body.property_id, body.check_in_date, body.check_out_date):
            raise HTTPException(status_code=409, detail="Property is not available for the selected dates")

        base_price = prop.base_price_per_night
        cleaning_fee = prop.cleaning_fee or 0
        svc_pct = prop.service_fee_percent or 0

        subtotal = base_price * nights
        service_fee = subtotal * (svc_pct / 100.0)
        total_cost = subtotal + cleaning_fee + service_fee

        # Insert booking
        insert_query = text("""
            INSERT INTO bookings (property_id, guest_id, check_in_date, check_out_date, num_adults, num_children, num_infants, num_pets,
                                  total_nights, nightly_rate, subtotal, cleaning_fee, service_fee, total_cost, status, created_at, updated_at)
            VALUES (:property_id, :guest_id, :check_in_date, :check_out_date, :num_adults, :num_children, :num_infants, :num_pets,
                    :total_nights, :nightly_rate, :subtotal, :cleaning_fee, :service_fee, :total_cost, :status, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """)
        params = {
            "property_id": body.property_id,
            "guest_id": guest_id,
            "check_in_date": body.check_in_date,
            "check_out_date": body.check_out_date,
            "num_adults": body.num_adults,
            "num_children": 0,
            "num_infants": 0,
            "num_pets": body.num_pets,
            "total_nights": nights,
            "nightly_rate": base_price,
            "subtotal": round(subtotal, 2),
            "cleaning_fee": round(cleaning_fee, 2),
            "service_fee": round(service_fee, 2),
            "total_cost": round(total_cost, 2),
            "status": "pending"
        }
        result = conn.execute(insert_query, params)
        conn.commit()

        booking_id = result.lastrowid

        # Fetch the inserted booking to return
        fetch_query = text("""
            SELECT id, property_id, guest_id, check_in_date, check_out_date, num_adults, num_children, num_infants, num_pets,
                   total_nights, nightly_rate, subtotal, cleaning_fee, service_fee, total_cost, status
            FROM bookings WHERE id = :booking_id
        """)
        booking_row = conn.execute(fetch_query, {"booking_id": booking_id}).fetchone()

        return BookingResponse(
            id=booking_row.id,
            property_id=booking_row.property_id,
            guest_id=booking_row.guest_id,
            check_in_date=str(booking_row.check_in_date),
            check_out_date=str(booking_row.check_out_date),
            num_adults=booking_row.num_adults,
            num_children=booking_row.num_children,
            num_infants=booking_row.num_infants,
            num_pets=booking_row.num_pets,
            total_nights=booking_row.total_nights,
            nightly_rate=booking_row.nightly_rate,
            subtotal=booking_row.subtotal,
            cleaning_fee=booking_row.cleaning_fee,
            service_fee=booking_row.service_fee,
            total_cost=booking_row.total_cost,
            status=booking_row.status
        )
