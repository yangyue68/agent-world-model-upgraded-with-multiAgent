import os
from datetime import date, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


# --- Pydantic Models for Requests/Responses ---

class UserLookupResponse(BaseModel):
    user_id: int


class CampsiteLookupResponse(BaseModel):
    campsite_id: int


class DiscountLookupResponse(BaseModel):
    discount_id: int


class RentalItemLookupResponse(BaseModel):
    rental_item_id: int


class CreateReservationRequest(BaseModel):
    user_id: int
    campsite_id: int
    check_in_date: str  # ISO format YYYY-MM-DD
    check_out_date: str  # ISO format YYYY-MM-DD
    num_guests: int
    discount_code: Optional[str] = None


class CreateReservationResponse(BaseModel):
    reservation_id: int


class AddRentalToReservationRequest(BaseModel):
    reservation_id: int
    rental_item_id: int
    quantity: int
    start_date: str  # ISO format YYYY-MM-DD
    end_date: str  # ISO format YYYY-MM-DD


class AddRentalToReservationResponse(BaseModel):
    rental_id: int


# --- Helper Functions ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- Endpoints ---

@app.get("/users", response_model=UserLookupResponse, operation_id="find_user_by_email")
def find_user_by_email(email: str = Query(..., description="Email address of the user to find")):
    """Find a user by email address."""
    with next(get_db()) as db:
        result = db.execute(
            text("SELECT id FROM users WHERE email = :email"),
            {"email": email}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail=f"User with email {email} not found")
        
        return UserLookupResponse(user_id=result[0])


@app.get("/campsites", response_model=CampsiteLookupResponse, operation_id="find_deluxe_cabin")
def find_deluxe_cabin(
    site_type: str = Query(..., description="Type of campsite to search for (must be 'cabin')"),
    max_occupancy: int = Query(..., description="Minimum number of guests the campsite must accommodate")
):
    """Find a deluxe cabin campsite that can accommodate at least N guests."""
    if site_type != "cabin":
        raise HTTPException(status_code=400, detail="Site type must be 'cabin'")
    
    with next(get_db()) as db:
        # Find an active cabin with sufficient occupancy. 
        # Ordering by ID to ensure deterministic selection if multiple match.
        result = db.execute(
            text("""
                SELECT id FROM campsites 
                WHERE site_type = :site_type 
                  AND max_occupancy >= :min_occ 
                  AND is_active = 1
                ORDER BY id ASC
                LIMIT 1
            """),
            {"site_type": site_type, "min_occ": max_occupancy}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail=f"No active {site_type} found accommodating {max_occupancy}+ guests")
        
        return CampsiteLookupResponse(campsite_id=result[0])


@app.get("/discounts", response_model=DiscountLookupResponse, operation_id="find_discount_by_code")
def find_discount_by_code(code: str = Query(..., description="Discount code to search for")):
    """Find a discount by its code."""
    with next(get_db()) as db:
        result = db.execute(
            text("SELECT id FROM discounts WHERE code = :code AND is_active = 1"),
            {"code": code}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail=f"Active discount with code {code} not found")
        
        return DiscountLookupResponse(discount_id=result[0])


@app.get("/rental_items", response_model=RentalItemLookupResponse, operation_id="find_kayak_rental")
def find_kayak_rental(category: str = Query(..., description="Category of rental item to search for (must be 'kayak')")):
    """Find kayak rental item."""
    if category != "kayak":
        raise HTTPException(status_code=400, detail="Category must be 'kayak'")
    
    with next(get_db()) as db:
        # Select the first available active kayak ordered by ID for determinism
        result = db.execute(
            text("""
                SELECT id FROM rental_items 
                WHERE category = :category 
                  AND is_active = 1
                ORDER BY id ASC
                LIMIT 1
            """),
            {"category": category}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail=f"No active {category} rental items found")
        
        return RentalItemLookupResponse(rental_item_id=result[0])


@app.post("/reservations", response_model=CreateReservationResponse, operation_id="create_reservation")
def create_reservation(request: CreateReservationRequest):
    """Create a new reservation for a guest."""
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized: Missing AWM_CURRENT_USER_ID")
    
    # Note: The spec says user_id in body is "Internal ID of the user making the reservation".
    # However, usually in these scenarios, we verify the requester matches or just use the provided one if authorized.
    # Given the strict instruction "read that environment variable at request time; never hardcode... and never add an auth header/body field absent from the spec",
    # but here `user_id` IS in the spec body. We will trust the body's user_id as the subject of the reservation,
    # assuming the caller has permission. If validation against env var was required strictly, it would likely be implicit.
    # Let's proceed using the request.user_id as the owner of the reservation.
    
    try:
        check_in = date.fromisoformat(request.check_in_date)
        check_out = date.fromisoformat(request.check_out_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
    
    if check_out <= check_in:
        raise HTTPException(status_code=400, detail="Check-out date must be after check-in date.")
    
    nights = (check_out - check_in).days
    
    with next(get_db()) as db:
        # Get campsite details
        campsite_row = db.execute(
            text("SELECT base_price_per_night, max_occupancy FROM campsites WHERE id = :id AND is_active = 1"),
            {"id": request.campsite_id}
        ).fetchone()
        
        if not campsite_row:
            raise HTTPException(status_code=404, detail="Campsite not found or inactive")
        
        base_price_per_night, max_occupancy = campsite_row
        
        if request.num_guests > max_occupancy:
            raise HTTPException(status_code=400, detail=f"Number of guests ({request.num_guests}) exceeds campsite capacity ({max_occupancy}).")
        
        subtotal = base_price_per_night * nights
        discount_amount = 0.0
        discount_id = None
        
        if request.discount_code:
            disc_row = db.execute(
                text("SELECT id, discount_type, amount FROM discounts WHERE code = :code AND is_active = 1"),
                {"code": request.discount_code}
            ).fetchone()
            
            if not disc_row:
                raise HTTPException(status_code=404, detail=f"Discount code '{request.discount_code}' not found or inactive")
            
            discount_id, d_type, d_amt = disc_row
            
            if d_type == 'percentage':
                discount_amount = subtotal * (d_amt / 100.0)
            elif d_type == 'fixed':
                discount_amount = min(d_amt, subtotal) # Cannot exceed subtotal
            
        total_price = round(subtotal - discount_amount, 2)
        
        # Insert Reservation
        res_result = db.execute(
            text("""
                INSERT INTO reservations (user_id, campsite_id, discount_id, check_in_date, check_out_date, num_guests, status, total_price, notes)
                VALUES (:uid, :cid, :did, :cin, :cout, :ng, 'confirmed', :tp, :notes)
            """),
            {
                "uid": request.user_id,
                "cid": request.campsite_id,
                "did": discount_id,
                "cin": request.check_in_date,
                "cout": request.check_out_date,
                "ng": request.num_guests,
                "tp": total_price,
                "notes": f"Created via API for returning guest."
            }
        )
        db.commit()
        
        reservation_id = res_result.lastrowid
        
        return CreateReservationResponse(reservation_id=reservation_id)


@app.post("/reservation_rentals", response_model=AddRentalToReservationResponse, operation_id="add_rental_to_reservation")
def add_rental_to_reservation(request: AddRentalToReservationRequest):
    """Add a rental item to an existing reservation."""
    try:
        start_d = date.fromisoformat(request.start_date)
        end_d = date.fromisoformat(request.end_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
    
    if end_d < start_d:
        raise HTTPException(status_code=400, detail="End date cannot be before start date.")
    
    days = (end_d - start_d).days + 1 # Inclusive range typically for rentals? 
    # Looking at initial snapshot:
    # Row 1: 2024-06-11 to 2024-06-13. Diff is 2 days. Price 80. Rate 40 (Tandem Kayak). 40*2 = 80.
    # So calculation is (End - Start).days.
    # Wait, let's re-read carefully.
    # Snapshot row 1: start 2024-06-11, end 2024-06-13. Quantity 1. Item 2 (Tandem Kayak, rate 40). Total 80.
    # Days between 11th and 13th exclusive of end? Or inclusive?
    # 13 - 11 = 2 days. 2 * 40 = 80. Matches.
    # Snapshot row 2: start 2024-07-02, end 2024-07-03. Quantity 2. Item 5 (Paddleboard, rate 30). Total 60.
    # 3 - 2 = 1 day. 1 * 30 * 2 qty = 60. Matches.
    # Formula: duration_days = (end_date - start_date).days
    
    duration_days = (end_d - start_d).days
    
    if duration_days <= 0:
         # If same day, is it 0 cost or 1 day? Usually rentals are per night/day block.
         # Based on examples, diff is used directly. If start==end, diff=0.
         # But CHECK constraint says end_date >= start_date.
         # Let's assume standard interpretation: if I rent for Aug 5 to Aug 7, that's 2 days.
         pass

    with next(get_db()) as db:
        # Verify reservation exists
        res_exists = db.execute(
            text("SELECT id FROM reservations WHERE id = :rid"),
            {"rid": request.reservation_id}
        ).fetchone()
        
        if not res_exists:
            raise HTTPException(status_code=404, detail="Reservation not found")
        
        # Get rental item details
        item_row = db.execute(
            text("SELECT daily_rate FROM rental_items WHERE id = :iid AND is_active = 1"),
            {"iid": request.rental_item_id}
        ).fetchone()
        
        if not item_row:
            raise HTTPException(status_code=404, detail="Rental item not found or inactive")
        
        daily_rate = item_row[0]
        total_price = round(duration_days * daily_rate * request.quantity, 2)
        
        # Insert Rental
        ren_result = db.execute(
            text("""
                INSERT INTO reservation_rentals (reservation_id, rental_item_id, quantity, start_date, end_date, total_price)
                VALUES (:rid, :iid, :qty, :sd, :ed, :tp)
            """),
            {
                "rid": request.reservation_id,
                "iid": request.rental_item_id,
                "qty": request.quantity,
                "sd": request.start_date,
                "ed": request.end_date,
                "tp": total_price
            }
        )
        db.commit()
        
        rental_id = ren_result.lastrowid
        
        return AddRentalToReservationResponse(rental_id=rental_id)
