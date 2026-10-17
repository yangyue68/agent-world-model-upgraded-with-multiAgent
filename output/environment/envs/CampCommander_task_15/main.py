import os
from datetime import date, datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="CampCommander API")


# --- Pydantic Models for Responses ---

class GuestResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ReservationResponse(BaseModel):
    id: int
    confirmation_number: str
    guest_id: int
    campsite_id: int
    check_in_date: date
    check_out_date: date
    total_amount: float
    discount_amount: float
    discount_reason: Optional[str] = None
    status: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class DiscountResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    discount_type: str
    amount: float
    is_active: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ApplyDiscountResponse(BaseModel):
    reservation_id: int
    applied_discount_amount: float
    new_total_amount: float
    discount_reason: str


# --- Helper Functions ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- Endpoints ---

@app.get("/guests/{guest_id}", response_model=GuestResponse, operation_id="get_guest_details")
def get_guest_details(guest_id: int = Path(..., description="Internal identifier for the guest")):
    """Fetch detailed information about a specific guest using their internal ID."""
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT id, first_name, last_name, email, phone, created_at, updated_at
                FROM guests
                WHERE id = :guest_id
            """),
            {"guest_id": guest_id}
        ).fetchone()

    if not result:
        raise HTTPException(status_code=404, detail="Guest not found")

    return GuestResponse(**result._asdict())


@app.get("/reservations/{confirmation_number}", response_model=ReservationResponse, operation_id="get_reservation_by_confirmation")
def get_reservation_by_confirmation(confirmation_number: str = Path(..., description="Unique confirmation number for the reservation")):
    """Fetch reservation details for a specific confirmation number including guest information and campsite details."""
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT id, confirmation_number, guest_id, campsite_id, check_in_date, check_out_date, 
                       total_amount, discount_amount, discount_reason, status, created_at, updated_at
                FROM reservations
                WHERE confirmation_number = :confirmation_number
            """),
            {"confirmation_number": confirmation_number}
        ).fetchone()

    if not result:
        raise HTTPException(status_code=404, detail="Reservation not found")

    row_dict = result._asdict()
    
    # Convert string dates to date objects if necessary (SQLite returns strings usually unless configured otherwise)
    # However, SQLAlchemy often handles this. If they come back as strings, we might need parsing.
    # Given the schema uses DATE type, let's ensure proper handling.
    if isinstance(row_dict['check_in_date'], str):
        row_dict['check_in_date'] = datetime.strptime(row_dict['check_in_date'], "%Y-%m-%d").date()
    if isinstance(row_dict['check_out_date'], str):
        row_dict['check_out_date'] = datetime.strptime(row_dict['check_out_date'], "%Y-%m-%d").date()
        
    return ReservationResponse(**row_dict)


@app.get("/discounts/military", response_model=DiscountResponse, operation_id="get_military_discount")
def get_military_discount():
    """Fetch the active military discount record used for applying discounts to eligible guests."""
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT id, name, description, discount_type, amount, is_active, created_at, updated_at
                FROM discounts
                WHERE name = 'Military Discount' AND is_active = 1
                LIMIT 1
            """)
        ).fetchone()

    if not result:
        raise HTTPException(status_code=404, detail="Active Military Discount not found")

    row_dict = result._asdict()
    # Ensure is_active is boolean
    row_dict['is_active'] = bool(row_dict['is_active'])
    
    return DiscountResponse(**row_dict)


@app.put("/reservations/{reservation_id}/apply_discount", response_model=ApplyDiscountResponse, operation_id="apply_military_discount")
def apply_military_discount(reservation_id: int = Path(..., description="Internal identifier for the reservation")):
    """Apply a 10% military discount to a specified reservation if the guest qualifies. Updates the discount amount and reason on the reservation."""
    
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
         # In some contexts, this might be optional or handled differently, but spec says read env var at request time for writes recording actor.
         # Here, the table doesn't have an actor column explicitly mentioned in DDL for updates other than timestamps, 
         # but typically audit logs might exist. The prompt says "When a write must record an actor column...".
         # Looking at `reservations` table, there is no `updated_by` column. So we just perform the update.
         pass

    with engine.begin() as conn:
        # 1. Get Reservation Details
        res_row = conn.execute(
            text("""
                SELECT r.id, r.guest_id, r.total_amount, r.discount_amount, g.first_name, g.last_name
                FROM reservations r
                JOIN guests g ON r.guest_id = g.id
                WHERE r.id = :reservation_id
            """),
            {"reservation_id": reservation_id}
        ).fetchone()

        if not res_row:
            raise HTTPException(status_code=404, detail="Reservation not found")

        guest_first = res_row.first_name
        guest_last = res_row.last_name
        
        # Task Specific Logic: Verify it is John Doe staying Oct 1 - Oct 5 2024?
        # The endpoint is generic "Apply military discount". The task description provides context for testing.
        # We should implement the logic generally based on the discount definition.
        
        # 2. Get Military Discount Definition
        disc_row = conn.execute(
            text("""
                SELECT id, amount, discount_type, name
                FROM discounts
                WHERE name = 'Military Discount' AND is_active = 1
                LIMIT 1
            """)
        ).fetchone()

        if not disc_row:
            raise HTTPException(status_code=404, detail="Military discount configuration not found")

        discount_id = disc_row.id
        discount_percentage = disc_row.amount # e.g., 10.0
        discount_type = disc_row.discount_type # 'percentage'
        discount_name = disc_row.name

        # 3. Calculate New Discount Amount
        # Current total_amount in DB seems to be the base price before any discounts? 
        # Or is it the final price? 
        # Schema: total_amount REAL NOT NULL CHECK(total_amount >= 0).
        # Usually, total_amount represents the gross amount or net? 
        # Let's look at initial data: Res 1 has total 140.0, discount 0.0. Campsite 1 rate 35. Days 4. 35*4=140.
        # So total_amount appears to be the Base Price * Nights.
        # When we apply discount, do we change total_amount or keep it and store discount_amount separately?
        # Response model asks for `new_total_amount`. This implies `total_amount` might represent the final payable amount OR we calculate it.
        # Standard pattern: `total_amount` is the subtotal. `discount_amount` is subtracted. Final = Total - Discount.
        # HOWEVER, looking at the PUT response: `new_total_amount`. And the GET response includes `total_amount` and `discount_amount`.
        # If I update `reservations.total_amount`, does it mean the final bill?
        # Let's assume `total_amount` in the DB stores the **final** chargeable amount after discounts, OR the base amount.
        # Initial Data: Res 4 (JKL012) has total 90.0, discount 0.0. But wait, Res 4 has a linked discount in `reservation_discounts` (ID 1, Res 4, Disc 2 Senior 15%).
        # Wait, `reservation_discounts` table exists.
        # Res 4: Campsite 5 (Rate 30). Dates Sep 20-23 (3 days). Base = 90.
        # It has a senior discount (15%) of 13.5 recorded in `reservation_discounts`.
        # BUT `reservations.discount_amount` is 0.0 for Res 4 in the snapshot provided?
        # Snapshot: `[4, "JKL012", 4, 5, "2024-09-20", "2024-09-23", 90.0, 0.0, null, "completed"...]`
        # This suggests `reservations.discount_amount` might not be automatically synced with `reservation_discounts` table, or the snapshot shows state BEFORE sync?
        # Actually, looking closely at the task: "Apply a 10% military discount... Updates the discount amount and reason on the reservation."
        # This implies we update the `reservations` table columns directly.
        
        # Calculation Strategy:
        # If `total_amount` currently holds the BASE price (pre-discount), then:
        #   New Discount = Base * Percentage / 100
        #   New Total Payable = Base - New Discount
        # Does `reservations.total_amount` hold Base or Net?
        # In many systems, `total_amount` is the grand total.
        # Let's check Res 1: ABC123. Base 140. Discount 0. Total 140.
        # If we apply 10% discount:
        # Discount Amount = 14.0
        # New Total Amount = 126.0 ? Or does Total Amount stay 140 and Discount becomes 14?
        # The response field is `new_total_amount`.
        # If I update `reservations.total_amount` to 126.0, then `discount_amount` is 14.0.
        # If I leave `reservations.total_amount` as 140.0, then `new_total_amount` returned would be 126.0 calculated on fly?
        # The instruction says "Updates the discount amount and reason on the reservation". It doesn't explicitly say update total_amount column.
        # HOWEVER, standard practice for such APIs where `total_amount` is present alongside `discount_amount`:
        # Often `total_amount` is the SUBTOTAL.
        # Let's look at the constraint: `CHECK(total_amount >= 0)`.
        # If I treat `total_amount` as Subtotal:
        #   Applied Discount = Subtotal * Rate.
        #   Update `discount_amount` = Applied Discount.
        #   Update `discount_reason` = Name.
        #   Return `new_total_amount` = Subtotal - Applied Discount.
        #   
        # Alternative: `total_amount` is Final Price.
        #   Then updating discount requires recalculating total.
        #   Old Total = 140. Old Discount = 0.
        #   New Discount = 14.
        #   New Total = 126.
        #   Update both columns.
        
        # Which one fits "Retrieve reservation details"?
        # If I fetch Res 1 later, will `total_amount` be 140 or 126?
        # Usually, invoices show Subtotal, Tax, Discount, Total.
        # Here we only have `total_amount` and `discount_amount`.
        # If `total_amount` was final, why store `discount_amount`? To know how much was taken off.
        # If `total_amount` was subtotal, `discount_amount` tells you what was taken off.
        # Let's assume `total_amount` is the **Base/Subtotal** because it matches `campsite.base_rate * nights` exactly in the seed data (Res 1: 35*4=140. Res 2: 120*3=360. Res 3: 65*3=195. Res 4: 30*3=90).
        # Therefore, `total_amount` is the pre-discount price.
        # The "New Total Amount" requested in the response is likely the **Final Payable Amount**.
        # We should probably also update the `reservations` table to reflect this state if required, but the prompt specifically says "Updates the discount amount and reason". It does NOT say "Update total amount".
        # However, if we don't update `total_amount`, it remains the subtotal.
        # Let's stick to modifying `discount_amount` and `discount_reason` in the DB.
        # And calculating the `new_total_amount` for the response.
        
        # Also, we should insert into `reservation_discounts` table to maintain history/linkage?
        # The table `reservation_discounts` exists. The prompt mentions "External effects are simulated by DB state".
        # Applying a discount logically involves linking the reservation to the discount catalog item.
        # I will update the `reservations` table AND insert/update the `reservation_discounts` table.

        # Check if discount already applied?
        # Unique constraint on (reservation_id, discount_id).
        
        existing_link = conn.execute(
            text("""
                SELECT id, applied_amount FROM reservation_discounts
                WHERE reservation_id = :rid AND discount_id = :did
            """),
            {"rid": reservation_id, "did": discount_id}
        ).fetchone()

        base_price = res_row.total_amount
        
        if discount_type == 'percentage':
            calc_discount = round(base_price * (discount_percentage / 100.0), 2)
        else:
            calc_discount = min(discount_percentage, base_price) # Fixed cap at base price
            
        # Determine final amounts
        # If there was an existing link, maybe we replace it? Or add?
        # Usually "Apply discount" sets the state.
        
        if existing_link:
            # Update existing link
            conn.execute(
                text("""
                    UPDATE reservation_discounts
                    SET applied_amount = :amt, updated_at = CURRENT_TIMESTAMP
                    WHERE id = :lid
                """),
                {"amt": calc_discount, "lid": existing_link.id}
            )
        else:
            # Insert new link
            conn.execute(
                text("""
                    INSERT INTO reservation_discounts (reservation_id, discount_id, applied_amount, created_at, updated_at)
                    VALUES (:rid, :did, :amt, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """),
                {"rid": reservation_id, "did": discount_id, "amt": calc_discount}
            )

        # Update Reservation Table
        # Set discount_amount and reason.
        # Note: If multiple discounts could apply, summing them up in `reservations.discount_amount` is complex.
        # For this single action, we set the discount amount associated with THIS application?
        # Or the TOTAL discount?
        # Given the simplicity, and that `reservations` has a single `discount_amount` field,
        # It likely tracks the primary/last applied discount or the total.
        # Since we are managing via `reservation_discounts` too, let's update `reservations` to reflect this specific discount application's impact on the summary fields.
        # Actually, safer bet: Update `reservations.discount_amount` to the calculated value for this transaction, assuming this is the sole relevant discount for the view, or overwrite.
        # Let's overwrite `discount_amount` and `discount_reason` in `reservations` table as per instructions "Updates the discount amount and reason on the reservation".
        
        conn.execute(
            text("""
                UPDATE reservations
                SET discount_amount = :disc_amt,
                    discount_reason = :reason,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :rid
            """),
            {
                "disc_amt": calc_discount,
                "reason": f"{discount_name}: {guest_first} {guest_last}", # Custom reason format or just name? Spec says "discount_reason". Example didn't specify format. Using descriptive string.
                "rid": reservation_id
            }
        )

        final_payable = base_price - calc_discount

        return ApplyDiscountResponse(
            reservation_id=reservation_id,
            applied_discount_amount=calc_discount,
            new_total_amount=round(final_payable, 2),
            discount_reason=f"{discount_name}"
        )
