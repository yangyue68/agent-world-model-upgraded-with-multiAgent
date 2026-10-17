import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class CardResponse(BaseModel):
    id: int
    card_name: str
    card_type: str
    last_four: str


class OfferResponse(BaseModel):
    id: int
    offer_name: str
    category: str
    cash_back_rate: float


class MessageResponse(BaseModel):
    message: str


def get_current_user_id() -> int:
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")


@app.get("/users/current/cards", response_model=list[CardResponse], operation_id="find_current_user_cards")
def find_current_user_cards():
    user_id = get_current_user_id()
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT id, card_name, card_type, last_four
                FROM cards
                WHERE user_id = :user_id AND is_active = 1
            """),
            {"user_id": user_id}
        )
        rows = result.fetchall()
    
    return [
        CardResponse(
            id=row.id,
            card_name=row.card_name,
            card_type=row.card_type,
            last_four=row.last_four
        )
        for row in rows
    ]


@app.get("/offers/limited_time", response_model=list[OfferResponse], operation_id="find_limited_time_offers")
def find_limited_time_offers():
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT id, offer_name, category, cash_back_rate
                FROM offers
                WHERE is_limited_time = 1
            """)
        )
        rows = result.fetchall()
    
    return [
        OfferResponse(
            id=row.id,
            offer_name=row.offer_name,
            category=row.category,
            cash_back_rate=row.cash_back_rate
        )
        for row in rows
    ]


@app.post("/cards/{card_id}/offers/{offer_id}/activate", response_model=MessageResponse, operation_id="activate_offer_for_card")
def activate_offer_for_card(card_id: int, offer_id: int):
    user_id = get_current_user_id()
    
    # Verify the card belongs to the current user and is active
    with engine.connect() as conn:
        card_result = conn.execute(
            text("""
                SELECT id FROM cards 
                WHERE id = :card_id AND user_id = :user_id AND is_active = 1
            """),
            {"card_id": card_id, "user_id": user_id}
        )
        if not card_result.fetchone():
            raise HTTPException(status_code=404, detail="Card not found or does not belong to user")
        
        # Verify the offer exists and is limited-time
        offer_result = conn.execute(
            text("""
                SELECT id FROM offers 
                WHERE id = :offer_id AND is_limited_time = 1
            """),
            {"offer_id": offer_id}
        )
        if not offer_result.fetchone():
            raise HTTPException(status_code=404, detail="Limited-time offer not found")
        
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        # Check if a record already exists in card_offers
        existing = conn.execute(
            text("""
                SELECT id, is_activated FROM card_offers 
                WHERE card_id = :card_id AND offer_id = :offer_id
            """),
            {"card_id": card_id, "offer_id": offer_id}
        ).fetchone()
        
        if existing:
            if existing.is_activated == 1:
                return MessageResponse(message="Offer already activated")
            else:
                conn.execute(
                    text("""
                        UPDATE card_offers 
                        SET is_activated = 1, activated_at = :now, updated_at = :now
                        WHERE card_id = :card_id AND offer_id = :offer_id
                    """),
                    {"card_id": card_id, "offer_id": offer_id, "now": now}
                )
        else:
            conn.execute(
                text("""
                    INSERT INTO card_offers (card_id, offer_id, is_activated, activated_at, created_at, updated_at)
                    VALUES (:card_id, :offer_id, 1, :now, :now, :now)
                """),
                {"card_id": card_id, "offer_id": offer_id, "now": now}
            )
        
        conn.commit()
    
    return MessageResponse(message="Offer activated successfully")


@app.put("/users/current/cards/{card_id}/redemption_preference", response_model=MessageResponse, operation_id="update_redemption_preference")
def update_redemption_preference(card_id: int):
    user_id = get_current_user_id()
    
    # Verify the card belongs to the current user and is active
    with engine.connect() as conn:
        card_result = conn.execute(
            text("""
                SELECT id FROM cards 
                WHERE id = :card_id AND user_id = :user_id AND is_active = 1
            """),
            {"card_id": card_id, "user_id": user_id}
        )
        if not card_result.fetchone():
            raise HTTPException(status_code=404, detail="Card not found or does not belong to user")
        
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        # Check if a preference already exists
        existing = conn.execute(
            text("""
                SELECT id FROM redemption_preferences 
                WHERE user_id = :user_id AND card_id = :card_id
            """),
            {"user_id": user_id, "card_id": card_id}
        ).fetchone()
        
        if existing:
            conn.execute(
                text("""
                    UPDATE redemption_preferences 
                    SET redemption_method = 'statement_credit', updated_at = :now
                    WHERE user_id = :user_id AND card_id = :card_id
                """),
                {"user_id": user_id, "card_id": card_id, "now": now}
            )
        else:
            conn.execute(
                text("""
                    INSERT INTO redemption_preferences (user_id, card_id, redemption_method, created_at, updated_at)
                    VALUES (:user_id, :card_id, 'statement_credit', :now, :now)
                """),
                {"user_id": user_id, "card_id": card_id, "now": now}
            )
        
        conn.commit()
    
    return MessageResponse(message="Redemption preference updated to statement credit")
