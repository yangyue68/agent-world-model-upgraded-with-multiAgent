import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class AlertItem(BaseModel):
    alert_type: str = Field(..., description="Type of alert: 'amount_threshold' or 'international'")
    threshold_amount: Optional[float] = Field(None, description="Threshold amount for amount_threshold alerts")


class LockCardResponse(BaseModel):
    message: str
    card_id: int


class CreatedAlert(BaseModel):
    id: int
    alert_type: str
    threshold_amount: Optional[float] = None


class CreateTransactionAlertResponse(BaseModel):
    message: str
    created_alerts: List[CreatedAlert]


def get_current_user_id() -> int:
    """Retrieve the current user ID from the environment."""
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")


@app.put("/cards/{card_id}/lock", response_model=LockCardResponse, operation_id="lock_card")
def lock_card(card_id: int = Path(..., description="Internal ID of the card to lock")):
    """
    Lock the card identified by card_id. This operation requires the user to be the owner of the card.
    """
    current_user_id = get_current_user_id()

    with SessionLocal() as db:
        # Check if card exists and belongs to the current user
        result = db.execute(
            text("SELECT id, user_id FROM cards WHERE id = :card_id"),
            {"card_id": card_id}
        ).fetchone()

        if not result:
            raise HTTPException(status_code=404, detail="Card not found")

        card_db_id, card_owner_id = result
        
        if card_owner_id != current_user_id:
            raise HTTPException(status_code=403, detail="Forbidden: You do not own this card")

        # Update the card to locked state
        db.execute(
            text("UPDATE cards SET is_locked = 1, updated_at = CURRENT_TIMESTAMP WHERE id = :card_id"),
            {"card_id": card_id}
        )
        db.commit()

        return LockCardResponse(message=f"Card {card_id} has been locked.", card_id=card_id)


@app.post("/cards/{card_id}/alerts", response_model=CreateTransactionAlertResponse, operation_id="create_transaction_alert")
def create_transaction_alert(
    card_id: int = Path(..., description="Internal ID of the card for which to create alerts"),
    alerts: List[AlertItem] = ...
):
    """
    Set up transaction alerts for a specific card. Supports two alert types: amount_threshold (with threshold_amount) and international. Only one alert per type can exist per card.
    """
    current_user_id = get_current_user_id()

    with SessionLocal() as db:
        # Check if card exists and belongs to the current user
        result = db.execute(
            text("SELECT id, user_id FROM cards WHERE id = :card_id"),
            {"card_id": card_id}
        ).fetchone()

        if not result:
            raise HTTPException(status_code=404, detail="Card not found")

        card_db_id, card_owner_id = result
        
        if card_owner_id != current_user_id:
            raise HTTPException(status_code=403, detail="Forbidden: You do not own this card")

        created_alerts_list = []

        for alert_item in alerts:
            alert_type = alert_item.alert_type
            
            # Validate alert_type against allowed values based on schema CHECK constraint
            if alert_type not in ['amount_threshold', 'international']:
                raise HTTPException(status_code=422, detail=f"Invalid alert_type: {alert_type}")

            # For amount_threshold, ensure threshold_amount is provided
            if alert_type == 'amount_threshold':
                if alert_item.threshold_amount is None:
                    raise HTTPException(status_code=422, detail="threshold_amount is required for amount_threshold alerts")
            
            # Try to insert or update existing alert (UNIQUE constraint on card_id, alert_type)
            # Using INSERT OR REPLACE might delete other columns if not careful, but here we want to ensure existence.
            # Since there's a unique constraint, we can check first or use ON CONFLICT DO UPDATE.
            # Let's check if it exists first to handle logic cleanly.
            
            existing_alert = db.execute(
                text("SELECT id FROM transaction_alerts WHERE card_id = :card_id AND alert_type = :alert_type"),
                {"card_id": card_id, "alert_type": alert_type}
            ).fetchone()

            if existing_alert:
                # If it exists, update it (e.g., re-enable or change threshold)
                new_id = existing_alert[0]
                db.execute(
                    text("""
                        UPDATE transaction_alerts 
                        SET threshold_amount = :threshold_amount, 
                            is_enabled = 1, 
                            updated_at = CURRENT_TIMESTAMP 
                        WHERE id = :id
                    """),
                    {
                        "id": new_id,
                        "threshold_amount": alert_item.threshold_amount
                    }
                )
            else:
                # Insert new alert
                res_insert = db.execute(
                    text("""
                        INSERT INTO transaction_alerts (card_id, alert_type, threshold_amount, is_enabled, created_at, updated_at)
                        VALUES (:card_id, :alert_type, :threshold_amount, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """),
                    {
                        "card_id": card_id,
                        "alert_type": alert_type,
                        "threshold_amount": alert_item.threshold_amount
                    }
                )
                new_id = res_insert.lastrowid

            created_alerts_list.append(CreatedAlert(
                id=new_id,
                alert_type=alert_type,
                threshold_amount=alert_item.threshold_amount
            ))

        db.commit()

        return CreateTransactionAlertResponse(
            message=f"Successfully processed {len(created_alerts_list)} alerts for card {card_id}.",
            created_alerts=created_alerts_list
        )
