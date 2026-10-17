import os
from datetime import date, datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="CampCommander API")


# --- Pydantic Models ---

class GuestCreate(BaseModel):
    full_name: str
    email: str
    phone: str


class GuestResponse(BaseModel):
    id: int


class SiteResponse(BaseModel):
    id: int
    site_number: str
    site_type: str
    hookup_type: str
    max_occupancy: int
    base_rate_per_night: float


class ReservationCreate(BaseModel):
    guest_id: int
    site_id: int
    check_in_date: str  # YYYY-MM-DD
    check_out_date: str  # YYYY-MM-DD
    adults: int
    children: int


class ReservationResponse(BaseModel):
    id: int


class AddonResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    price: float
    unit: str


class ReservationAddonCreate(BaseModel):
    reservation_id: int
    addon_id: int
    quantity: int


class ReservationAddonResponse(BaseModel):
    id: int


# --- Helper Functions ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def parse_iso_date(date_str: str) -> date:
    """Parse ISO-8601 date string (YYYY-MM-DD)."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {date_str}. Expected YYYY-MM-DD.")


# --- Endpoints ---

@app.post("/guests", response_model=GuestResponse, operation_id="create_guest")
def create_guest(guest_data: GuestCreate):
    db = next(get_db())
    try:
        # Check if email already exists
        existing = db.execute(text("SELECT id FROM guests WHERE email = :email"), {"email": guest_data.email}).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Guest with this email already exists.")

        result = db.execute(
            text("""
                INSERT INTO guests (full_name, email, phone)
                VALUES (:full_name, :email, :phone)
            """),
            {
                "full_name": guest_data.full_name,
                "email": guest_data.email,
                "phone": guest_data.phone,
            }
        )
        db.commit()
        guest_id = result.lastrowid
        return GuestResponse(id=guest_id)
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


@app.get("/sites", response_model=list[SiteResponse], operation_id="find_sites")
def find_sites(
    check_in_date: str = Query(..., description="Check-in date in YYYY-MM-DD format"),
    check_out_date: str = Query(..., description="Check-out date in YYYY-MM-DD format"),
    adults: int = Query(..., ge=1, description="Number of adults"),
    children: int = Query(..., ge=0, description="Number of children"),
):
    db = next(get_db())
    try:
        ci_date = parse_iso_date(check_in_date)
        co_date = parse_iso_date(check_out_date)

        if co_date <= ci_date:
            raise HTTPException(status_code=400, detail="Check-out date must be after check-in date.")

        total_occupancy = adults + children

        # Find sites that are rv_30amp with full hookup and sufficient capacity
        # Also ensure they are not booked for overlapping dates
        query = text("""
            SELECT s.id, s.site_number, s.site_type, s.hookup_type, s.max_occupancy, s.base_rate_per_night
            FROM sites s
            WHERE s.site_type = 'rv_30amp'
              AND s.hookup_type = 'full'
              AND s.max_occupancy >= :total_occupancy
              AND NOT EXISTS (
                  SELECT 1 FROM reservations r
                  WHERE r.site_id = s.id
                    AND r.status IN ('pending', 'confirmed')
                    AND r.check_in_date < :check_out_date
                    AND r.check_out_date > :check_in_date
              )
        """)

        results = db.execute(query, {
            "total_occupancy": total_occupancy,
            "check_in_date": ci_date.isoformat(),
            "check_out_date": co_date.isoformat(),
        }).fetchall()

        sites = []
        for row in results:
            sites.append(SiteResponse(
                id=row[0],
                site_number=row[1],
                site_type=row[2],
                hookup_type=row[3],
                max_occupancy=row[4],
                base_rate_per_night=row[5],
            ))
        
        return sites
    except Exception as e:
        raise e
    finally:
        db.close()


@app.post("/reservations", response_model=ReservationResponse, operation_id="create_reservation")
def create_reservation(reservation_data: ReservationCreate):
    db = next(get_db())
    try:
        ci_date = parse_iso_date(reservation_data.check_in_date)
        co_date = parse_iso_date(reservation_data.check_out_date)

        if co_date <= ci_date:
            raise HTTPException(status_code=400, detail="Check-out date must be after check-in date.")

        # Verify guest exists
        guest_exists = db.execute(text("SELECT id FROM guests WHERE id = :gid"), {"gid": reservation_data.guest_id}).fetchone()
        if not guest_exists:
            raise HTTPException(status_code=404, detail="Guest not found.")

        # Verify site exists and meets criteria
        site_row = db.execute(
            text("SELECT site_type, hookup_type, max_occupancy, base_rate_per_night FROM sites WHERE id = :sid"),
            {"sid": reservation_data.site_id}
        ).fetchone()
        
        if not site_row:
            raise HTTPException(status_code=404, detail="Site not found.")

        site_type, hookup_type, max_occ, rate = site_row
        
        if site_type != 'rv_30amp':
             raise HTTPException(status_code=400, detail="Selected site is not a 30-amp RV site.")
        if hookup_type != 'full':
             raise HTTPException(status_code=400, detail="Selected site does not have full hookups.")
        
        total_people = reservation_data.adults + reservation_data.children
        if total_people > max_occ:
            raise HTTPException(status_code=400, detail="Occupancy exceeds site maximum.")

        # Check availability again to prevent race conditions (though single-threaded here mostly)
        conflict = db.execute(
            text("""
                SELECT id FROM reservations 
                WHERE site_id = :sid 
                  AND status IN ('pending', 'confirmed')
                  AND check_in_date < :co_date
                  AND check_out_date > :ci_date
            """),
            {
                "sid": reservation_data.site_id,
                "ci_date": ci_date.isoformat(),
                "co_date": co_date.isoformat(),
            }
        ).fetchone()

        if conflict:
            raise HTTPException(status_code=409, detail="Site is not available for the selected dates.")

        # Calculate nights and total amount
        num_nights = (co_date - ci_date).days
        total_amount = num_nights * rate

        # Get current user ID from environment
        created_by_user_id = os.environ.get("AWM_CURRENT_USER_ID")
        if not created_by_user_id:
            # Fallback or error? Spec says read env var. If missing, maybe default to 1 or error.
            # Assuming runtime always provides it. If not, we might need a fallback.
            # Let's assume it's present. If not, we'll cast to int which will fail if None.
            pass
        
        try:
            creator_id = int(created_by_user_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=500, detail="Current user context missing or invalid.")

        result = db.execute(
            text("""
                INSERT INTO reservations (guest_id, site_id, check_in_date, check_out_date, adults, children, status, total_amount, created_by)
                VALUES (:guest_id, :site_id, :check_in_date, :check_out_date, :adults, :children, 'confirmed', :total_amount, :created_by)
            """),
            {
                "guest_id": reservation_data.guest_id,
                "site_id": reservation_data.site_id,
                "check_in_date": ci_date.isoformat(),
                "check_out_date": co_date.isoformat(),
                "adults": reservation_data.adults,
                "children": reservation_data.children,
                "total_amount": total_amount,
                "created_by": creator_id,
            }
        )
        db.commit()
        res_id = result.lastrowid
        return ReservationResponse(id=res_id)
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


@app.get("/addons", response_model=list[AddonResponse], operation_id="find_addons")
def find_addons():
    db = next(get_db())
    try:
        rows = db.execute(
            text("SELECT id, name, description, price, unit FROM addons WHERE is_active = 1")
        ).fetchall()
        
        addons = []
        for row in rows:
            addons.append(AddonResponse(
                id=row[0],
                name=row[1],
                description=row[2],
                price=row[3],
                unit=row[4],
            ))
        return addons
    except Exception as e:
        raise e
    finally:
        db.close()


@app.post("/reservation_addons", response_model=ReservationAddonResponse, operation_id="add_addon_to_reservation")
def add_addon_to_reservation(addon_data: ReservationAddonCreate):
    db = next(get_db())
    try:
        # Verify reservation exists
        res_exists = db.execute(text("SELECT id FROM reservations WHERE id = :rid"), {"rid": addon_data.reservation_id}).fetchone()
        if not res_exists:
            raise HTTPException(status_code=404, detail="Reservation not found.")

        # Verify addon exists and is active
        addon_row = db.execute(
            text("SELECT id, price, unit FROM addons WHERE id = :aid AND is_active = 1"),
            {"aid": addon_data.addon_id}
        ).fetchone()
        
        if not addon_row:
            raise HTTPException(status_code=404, detail="Addon not found or inactive.")

        _, unit_price, _ = addon_row

        # Check if this addon is already added to this reservation (UNIQUE constraint)
        existing_ra = db.execute(
            text("SELECT id FROM reservation_addons WHERE reservation_id = :rid AND addon_id = :aid"),
            {"rid": addon_data.reservation_id, "aid": addon_data.addon_id}
        ).fetchone()
        
        if existing_ra:
            raise HTTPException(status_code=409, detail="This addon has already been added to the reservation.")

        total_price = unit_price * addon_data.quantity

        result = db.execute(
            text("""
                INSERT INTO reservation_addons (reservation_id, addon_id, quantity, unit_price, total_price)
                VALUES (:rid, :aid, :qty, :uprice, :tprice)
            """),
            {
                "rid": addon_data.reservation_id,
                "aid": addon_data.addon_id,
                "qty": addon_data.quantity,
                "uprice": unit_price,
                "tprice": total_price,
            }
        )
        db.commit()
        ra_id = result.lastrowid
        return ReservationAddonResponse(id=ra_id)
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
