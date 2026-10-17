import os
from datetime import datetime, date
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class ApartmentResponse(BaseModel):
    id: int
    title: str
    description: Optional[str]
    city: str
    state: str
    address: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    max_guests: int
    bedrooms: int
    bathrooms: int
    base_price_per_night: float
    cleaning_fee: float
    service_fee: float
    instant_book: bool


class BookingRequest(BaseModel):
    apartment_id: int
    guest_id: int
    check_in_date: str
    check_out_date: str
    num_guests: int


class BookingResponse(BaseModel):
    id: int
    apartment_id: int
    guest_id: int
    check_in_date: str
    check_out_date: str
    num_guests: int
    total_price: float
    status: str


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/available-apartments", response_model=List[ApartmentResponse], operation_id="find_available_apartments")
def find_available_apartments(
    check_in_date: str = Query(..., description="Check-in date in YYYY-MM-DD format"),
    check_out_date: str = Query(..., description="Check-out date in YYYY-MM-DD format"),
    max_guests: int = Query(..., description="Maximum number of guests"),
    max_price_per_night: float = Query(..., description="Maximum price per night"),
):
    # Parse dates to ensure valid format and for comparison
    try:
        ci_date = datetime.strptime(check_in_date, "%Y-%m-%d").date()
        co_date = datetime.strptime(check_out_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    if co_date <= ci_date:
        raise HTTPException(status_code=400, detail="Check-out date must be after check-in date.")

    db = SessionLocal()
    try:
        # Find apartments that are:
        # 1. In Portland, OR
        # 2. Instant book enabled
        # 3. Active
        # 4. Max guests >= requested max_guests (actually, the spec says "Filters by maximum guests", implying the apartment can accommodate at least this many? Or exactly? Usually "max_guests" filter means apartment.max_guests >= query.max_guests. Let's assume apartment can hold at least the requested guests.)
        # Wait, the parameter name is `max_guests` in the query. The description says "Maximum number of guests". 
        # Typically, if I search for a place for 3 guests, I want places with capacity >= 3.
        # However, the field in DB is `max_guests`. If the query param is `max_guests`, it likely filters `apartments.max_guests >= :max_guests`.
        # Also price filter: `base_price_per_night <= :max_price_per_night`.
        
        # Availability check:
        # We need to ensure all nights from check_in_date to check_out_date - 1 day are available.
        # Generate list of dates between check_in and check_out (exclusive of checkout).
        
        current_date = ci_date
        required_dates = []
        while current_date < co_date:
            required_dates.append(current_date.isoformat())
            current_date += __import__('datetime').timedelta(days=1)
            
        if not required_dates:
             return []

        # SQL Query Strategy:
        # Join apartments with apartment_availability.
        # Filter by location, instant_book, active, max_guests, price.
        # Group by apartment ID.
        # Having count of matching availability records == number of required nights AND all are is_available=1.
        
        placeholders = ",".join([f":date_{i}" for i in range(len(required_dates))])
        
        sql = text(f"""
            SELECT 
                a.id,
                a.title,
                a.description,
                a.city,
                a.state,
                a.address,
                a.latitude,
                a.longitude,
                a.max_guests,
                a.bedrooms,
                a.bathrooms,
                a.base_price_per_night,
                a.cleaning_fee,
                a.service_fee,
                a.instant_book
            FROM apartments a
            JOIN apartment_availability av ON a.id = av.apartment_id
            WHERE 
                a.city = 'Portland'
                AND a.state = 'OR'
                AND a.instant_book = 1
                AND a.is_active = 1
                AND a.max_guests >= :max_guests
                AND a.base_price_per_night <= :max_price_per_night
                AND av.date IN ({placeholders})
                AND av.is_available = 1
            GROUP BY a.id
            HAVING COUNT(av.id) = :num_nights
        """)
        
        params = {
            "max_guests": max_guests,
            "max_price_per_night": max_price_per_night,
            "num_nights": len(required_dates)
        }
        
        for i, d in enumerate(required_dates):
            params[f"date_{i}"] = d
            
        result = db.execute(sql, params)
        rows = result.fetchall()
        
        apartments = []
        for row in rows:
            apartments.append(ApartmentResponse(
                id=row.id,
                title=row.title,
                description=row.description,
                city=row.city,
                state=row.state,
                address=row.address,
                latitude=row.latitude,
                longitude=row.longitude,
                max_guests=row.max_guests,
                bedrooms=row.bedrooms,
                bathrooms=row.bathrooms,
                base_price_per_night=row.base_price_per_night,
                cleaning_fee=row.cleaning_fee,
                service_fee=row.service_fee,
                instant_book=bool(row.instant_book)
            ))
            
        return apartments
        
    finally:
        db.close()


@app.post("/bookings", response_model=BookingResponse, operation_id="create_booking")
def create_booking(booking_req: BookingRequest):
    # Validate dates
    try:
        ci_date = datetime.strptime(booking_req.check_in_date, "%Y-%m-%d").date()
        co_date = datetime.strptime(booking_req.check_out_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    if co_date <= ci_date:
        raise HTTPException(status_code=400, detail="Check-out date must be after check-in date.")

    db = SessionLocal()
    try:
        # Get apartment details
        apt_sql = text("""
            SELECT id, host_id, base_price_per_night, cleaning_fee, service_fee, max_guests, instant_book, is_active, city, state
            FROM apartments
            WHERE id = :apt_id
        """)
        apt_result = db.execute(apt_sql, {"apt_id": booking_req.apartment_id}).fetchone()
        
        if not apt_result:
            raise HTTPException(status_code=404, detail="Apartment not found")
        
        apt = apt_result._mapping
        
        if not apt['is_active']:
            raise HTTPException(status_code=400, detail="Apartment is not active")
            
        if not apt['instant_book']:
            raise HTTPException(status_code=400, detail="Apartment does not support instant booking")
            
        if booking_req.num_guests > apt['max_guests']:
            raise HTTPException(status_code=400, detail="Number of guests exceeds apartment capacity")
            
        # Check availability for all nights
        current_date = ci_date
        required_dates = []
        while current_date < co_date:
            required_dates.append(current_date.isoformat())
            current_date += __import__('datetime').timedelta(days=1)
            
        if not required_dates:
             raise HTTPException(status_code=400, detail="No nights selected")

        placeholders = ",".join([f":date_{i}" for i in range(len(required_dates))])
        
        avail_sql = text(f"""
            SELECT date, is_available
            FROM apartment_availability
            WHERE apartment_id = :apt_id
            AND date IN ({placeholders})
        """)
        
        params = {"apt_id": booking_req.apartment_id}
        for i, d in enumerate(required_dates):
            params[f"date_{i}"] = d
            
        avail_results = db.execute(avail_sql, params).fetchall()
        
        # Map date to availability
        avail_map = {r.date: r.is_available for r in avail_results}
        
        for d in required_dates:
            if d not in avail_map or avail_map[d] != 1:
                raise HTTPException(status_code=409, detail=f"Apartment is not available on {d}")
                
        # Calculate total price
        num_nights = len(required_dates)
        base_total = apt['base_price_per_night'] * num_nights
        total_price = base_total + apt['cleaning_fee'] + apt['service_fee']
        
        # Insert booking
        insert_sql = text("""
            INSERT INTO bookings (
                apartment_id, 
                guest_id, 
                check_in_date, 
                check_out_date, 
                num_guests, 
                total_price, 
                status,
                created_at,
                updated_at
            ) VALUES (
                :apt_id,
                :guest_id,
                :ci_date,
                :co_date,
                :num_guests,
                :total_price,
                'confirmed',
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            )
        """)
        
        # Note: The prompt says "The runtime injects the simulated logged-in actor as os.environ["AWM_CURRENT_USER_ID"]."
        # But the API spec for POST /bookings has `guest_id` in the body. 
        # The instruction says: "When a write must record an actor column... read that environment variable... never add an auth header/body field absent from the spec."
        # Here, `guest_id` IS in the spec. So we use the provided `guest_id`.
        # Does `guest_id` refer to the user making the booking? Yes.
        # Should we override it with AWM_CURRENT_USER_ID? 
        # "Use the exact method, path, operation_id, parameter names and response semantics in the API specification."
        # The spec explicitly asks for `guest_id` in the body. 
        # Usually, in these tasks, if the spec provides the ID, we use it. If the spec relies on session/auth, we use env var.
        # Since `guest_id` is a required body param, we use `booking_req.guest_id`.
        
        db.execute(insert_sql, {
            "apt_id": booking_req.apartment_id,
            "guest_id": booking_req.guest_id,
            "ci_date": booking_req.check_in_date,
            "co_date": booking_req.check_out_date,
            "num_guests": booking_req.num_guests,
            "total_price": total_price
        })
        
        # Update availability to unavailable
        update_avail_sql = text(f"""
            UPDATE apartment_availability
            SET is_available = 0, updated_at = CURRENT_TIMESTAMP
            WHERE apartment_id = :apt_id
            AND date IN ({placeholders})
        """)
        
        db.execute(update_avail_sql, params)
        
        db.commit()
        
        # Fetch the created booking to return correct ID and timestamps
        fetch_sql = text("""
            SELECT id, apartment_id, guest_id, check_in_date, check_out_date, num_guests, total_price, status
            FROM bookings
            WHERE id = last_insert_rowid()
        """)
        new_booking = db.execute(fetch_sql).fetchone()
        
        return BookingResponse(
            id=new_booking.id,
            apartment_id=new_booking.apartment_id,
            guest_id=new_booking.guest_id,
            check_in_date=str(new_booking.check_in_date),
            check_out_date=str(new_booking.check_out_date),
            num_guests=new_booking.num_guests,
            total_price=new_booking.total_price,
            status=new_booking.status
        )
        
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
