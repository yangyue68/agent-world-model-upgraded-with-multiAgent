import os
from datetime import date, datetime
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


# --- Pydantic Models for Request/Response ---

class ReservationCreate(BaseModel):
    user_id: int
    campsite_id: int
    check_in_date: str  # ISO format date string e.g. "2024-06-01"
    check_out_date: str
    party_size: int


class ReservationResponse(BaseModel):
    id: int
    user_id: int
    campsite_id: int
    check_in_date: str
    check_out_date: str
    party_size: int
    status: str
    total_price: Optional[float] = None
    notes: Optional[str] = None


class AddonCreate(BaseModel):
    reservation_id: int
    addon_id: int
    quantity: Optional[int] = 1
    price_at_booking: float


class AddonResponse(BaseModel):
    id: int
    reservation_id: int
    addon_id: int
    quantity: int
    price_at_booking: float


class UserResponse(BaseModel):
    id: int
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None


class CampsiteResponse(BaseModel):
    id: int
    name: str
    site_type: str
    max_occupancy: int
    base_price_per_night: float


class AddonItemResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    price: float
    unit: str


# --- Helper Functions ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def parse_iso_date(date_str: str) -> date:
    """Parse ISO-8601 date strings, handling both 'YYYY-MM-DD' and potentially other formats if needed."""
    try:
        return datetime.fromisoformat(date_str.replace('Z', '+00:00')).date()
    except ValueError:
        # Fallback for simple YYYY-MM-DD
        parts = date_str.split('-')
        if len(parts) == 3:
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
        raise HTTPException(status_code=400, detail=f"Invalid date format: {date_str}")


# --- Endpoints ---

@app.post("/reservations", response_model=ReservationResponse, operation_id="create_reservation")
def create_reservation(reservation_data: ReservationCreate):
    db = next(get_db())
    try:
        # Validate dates
        check_in = parse_iso_date(reservation_data.check_in_date)
        check_out = parse_iso_date(reservation_data.check_out_date)

        if check_out <= check_in:
            raise HTTPException(status_code=400, detail="Check-out date must be after check-in date.")

        # Fetch campsite to calculate price and validate occupancy
        stmt_campsite = text("""
            SELECT id, name, site_type, max_occupancy, base_price_per_night 
            FROM campsites 
            WHERE id = :campsite_id
        """)
        res_campsite = db.execute(stmt_campsite, {"campsite_id": reservation_data.campsite_id}).fetchone()
        
        if not res_campsite:
            raise HTTPException(status_code=404, detail="Campsite not found.")
        
        campsite_id, _, _, max_occ, base_price = res_campsite
        
        if reservation_data.party_size > max_occ:
            raise HTTPException(status_code=400, detail=f"Party size exceeds maximum occupancy of {max_occ}.")

        # Calculate nights
        num_nights = (check_out - check_in).days
        total_price = round(num_nights * base_price, 2)

        # Insert reservation
        stmt_insert = text("""
            INSERT INTO reservations (user_id, campsite_id, check_in_date, check_out_date, party_size, status, total_price, notes)
            VALUES (:user_id, :campsite_id, :check_in_date, :check_out_date, :party_size, 'confirmed', :total_price, :notes)
        """)
        
        result = db.execute(stmt_insert, {
            "user_id": reservation_data.user_id,
            "campsite_id": reservation_data.campsite_id,
            "check_in_date": check_in.isoformat(),
            "check_out_date": check_out.isoformat(),
            "party_size": reservation_data.party_size,
            "total_price": total_price,
            "notes": None
        })
        db.commit()
        
        new_res_id = result.lastrowid
        
        # Fetch back to ensure consistency with defaults/triggers if any
        stmt_fetch = text("""
            SELECT id, user_id, campsite_id, check_in_date, check_out_date, party_size, status, total_price, notes
            FROM reservations
            WHERE id = :id
        """)
        final_res = db.execute(stmt_fetch, {"id": new_res_id}).fetchone()
        
        if not final_res:
             raise HTTPException(status_code=500, detail="Failed to retrieve created reservation.")

        return ReservationResponse(
            id=final_res.id,
            user_id=final_res.user_id,
            campsite_id=final_res.campsite_id,
            check_in_date=str(final_res.check_in_date),
            check_out_date=str(final_res.check_out_date),
            party_size=final_res.party_size,
            status=final_res.status,
            total_price=float(final_res.total_price) if final_res.total_price is not None else None,
            notes=final_res.notes
        )
    except Exception as e:
        db.rollback()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.post("/reservation_addons", response_model=AddonResponse, operation_id="add_addon_to_reservation")
def add_addon_to_reservation(addon_data: AddonCreate):
    db = next(get_db())
    try:
        # Verify reservation exists
        stmt_check_res = text("SELECT id FROM reservations WHERE id = :rid")
        res_exists = db.execute(stmt_check_res, {"rid": addon_data.reservation_id}).fetchone()
        if not res_exists:
            raise HTTPException(status_code=404, detail="Reservation not found.")

        # Verify addon exists
        stmt_check_addon = text("SELECT id, price FROM addons WHERE id = :aid")
        addon_info = db.execute(stmt_check_addon, {"aid": addon_data.addon_id}).fetchone()
        if not addon_info:
            raise HTTPException(status_code=404, detail="Addon not found.")

        qty = addon_data.quantity if addon_data.quantity is not None else 1
        if qty <= 0:
            raise HTTPException(status_code=400, detail="Quantity must be positive.")

        # Check unique constraint manually or rely on DB? Relying on DB error might be messy.
        # Let's just insert. If it fails due to UNIQUE constraint, we catch it.
        stmt_insert = text("""
            INSERT INTO reservation_addons (reservation_id, addon_id, quantity, price_at_booking)
            VALUES (:rid, :aid, :qty, :price)
        """)
        
        try:
            result = db.execute(stmt_insert, {
                "rid": addon_data.reservation_id,
                "aid": addon_data.addon_id,
                "qty": qty,
                "price": addon_data.price_at_booking
            })
            db.commit()
            
            new_id = result.lastrowid
            
            # Fetch back
            stmt_fetch = text("""
                SELECT id, reservation_id, addon_id, quantity, price_at_booking
                FROM reservation_addons
                WHERE id = :id
            """)
            final_row = db.execute(stmt_fetch, {"id": new_id}).fetchone()
            
            return AddonResponse(
                id=final_row.id,
                reservation_id=final_row.reservation_id,
                addon_id=final_row.addon_id,
                quantity=final_row.quantity,
                price_at_booking=float(final_row.price_at_booking)
            )
        except Exception as e:
            db.rollback()
            # Check if it was a unique constraint violation
            if "UNIQUE constraint failed" in str(e):
                 raise HTTPException(status_code=409, detail="This addon is already associated with this reservation.")
            raise e

    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.get("/users", response_model=UserResponse, operation_id="find_user_by_email")
def find_user_by_email(email: str = Query(..., description="Email address of the user to find")):
    db = next(get_db())
    try:
        stmt = text("""
            SELECT id, email, first_name, last_name, phone
            FROM users
            WHERE email = :email
        """)
        row = db.execute(stmt, {"email": email}).fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="User not found.")
            
        return UserResponse(
            id=row.id,
            email=row.email,
            first_name=row.first_name,
            last_name=row.last_name,
            phone=row.phone
        )
    finally:
        db.close()


@app.get("/campsites", response_model=List[CampsiteResponse], operation_id="find_campsite_by_type")
def find_campsite_by_type(site_type: str = Query(..., description="Type of campsite to search for")):
    db = next(get_db())
    try:
        stmt = text("""
            SELECT id, name, site_type, max_occupancy, base_price_per_night
            FROM campsites
            WHERE site_type = :st
        """)
        rows = db.execute(stmt, {"st": site_type}).fetchall()
        
        results = []
        for r in rows:
            results.append(CampsiteResponse(
                id=r.id,
                name=r.name,
                site_type=r.site_type,
                max_occupancy=r.max_occupancy,
                base_price_per_night=float(r.base_price_per_night)
            ))
        return results
    finally:
        db.close()


@app.get("/addons", response_model=AddonItemResponse, operation_id="find_addon_by_name")
def find_addon_by_name(name: str = Query(..., description="Name of the addon to find")):
    db = next(get_db())
    try:
        stmt = text("""
            SELECT id, name, description, price, unit
            FROM addons
            WHERE name = :name
        """)
        row = db.execute(stmt, {"name": name}).fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Addon not found.")
            
        return AddonItemResponse(
            id=row.id,
            name=row.name,
            description=row.description,
            price=float(row.price),
            unit=row.unit
        )
    finally:
        db.close()
