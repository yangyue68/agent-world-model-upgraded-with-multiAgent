import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Database configuration
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()

# Pydantic Models for Request/Response

class CancelRecurringGiftRequest(BaseModel):
    reason: str

class CancelRecurringGiftResponse(BaseModel):
    success: bool
    message: str
    cancellation_event_id: int
    updated_engagement_score: int

class EngagementScoreResponse(BaseModel):
    constituent_id: str
    engagement_score: int

class UpdateEngagementScoreRequest(BaseModel):
    change: int

class UpdateEngagementScoreResponse(BaseModel):
    constituent_id: str
    previous_engagement_score: int
    new_engagement_score: int


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.post(
    "/recurring-gifts/{id}/cancel",
    operation_id="cancel_recurring_gift",
    response_model=CancelRecurringGiftResponse
)
def cancel_recurring_gift(
    id: str = Path(..., description="The ID of the recurring gift to cancel"),
    request: CancelRecurringGiftRequest = Body(...)
):
    """
    Process the cancellation of a recurring gift by ID, log the cancellation reason, 
    update the constituent's engagement score by -15 points, and perform a soft-delete 
    on the recurring schedule while preserving historical records. Link the cancellation 
    event to the original pledge.
    """
    db = SessionLocal()
    try:
        # 1. Verify Recurring Gift exists and is active/not deleted
        rg_query = text("""
            SELECT id, pledge_id, constituent_id, status, is_deleted 
            FROM recurring_gifts 
            WHERE id = :rg_id
        """)
        rg_result = db.execute(rg_query, {"rg_id": id}).fetchone()
        
        if not rg_result:
            raise HTTPException(status_code=404, detail="Recurring gift not found")
        
        rg_id, pledge_id, constituent_id, status, is_deleted = rg_result
        
        if is_deleted == 1:
            raise HTTPException(status_code=400, detail="Recurring gift is already deleted")
        
        if status != 'active':
            # Depending on business logic, we might allow cancelling paused gifts too, 
            # but spec says "Process the cancellation... trigger immediate soft-delete".
            # Usually you can only cancel active or paused. Let's assume it must be cancellable.
            # If it's already cancelled/completed, error.
            if status in ['cancelled', 'completed']:
                raise HTTPException(status_code=400, detail=f"Cannot cancel recurring gift with status '{status}'")

        # 2. Get current engagement score
        eng_query = text("""
            SELECT engagement_score 
            FROM constituents 
            WHERE id = :c_id
        """)
        eng_result = db.execute(eng_query, {"c_id": constituent_id}).fetchone()
        
        if not eng_result:
            raise HTTPException(status_code=404, detail="Constituent not found")
            
        current_score = eng_result[0]
        new_score = current_score - 15

        # 3. Update Constituent Engagement Score
        update_eng_query = text("""
            UPDATE constituents 
            SET engagement_score = :new_score, updated_at = CURRENT_TIMESTAMP
            WHERE id = :c_id
        """)
        db.execute(update_eng_query, {"new_score": new_score, "c_id": constituent_id})

        # 4. Soft Delete Recurring Gift (set status to cancelled and is_deleted to 1)
        update_rg_query = text("""
            UPDATE recurring_gifts 
            SET status = 'cancelled', is_deleted = 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = :rg_id
        """)
        db.execute(update_rg_query, {"rg_id": id})

        # 5. Log Cancellation Event
        # Get actor user ID from environment
        actor_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
        if not actor_user_id_str:
            raise HTTPException(status_code=500, detail="Actor user ID not found in environment")
        
        try:
            actor_user_id = int(actor_user_id_str)
        except ValueError:
            raise HTTPException(status_code=500, detail="Invalid Actor user ID format")

        insert_cancel_event_query = text("""
            INSERT INTO cancellation_events (
                recurring_gift_id, 
                constituent_id, 
                pledge_id, 
                reason, 
                engagement_score_change, 
                soft_delete_performed, 
                cancelled_by_user_id
            ) VALUES (
                :rg_id, 
                :c_id, 
                :p_id, 
                :reason, 
                :score_change, 
                :soft_delete, 
                :user_id
            )
        """)
        
        result = db.execute(insert_cancel_event_query, {
            "rg_id": id,
            "c_id": constituent_id,
            "p_id": pledge_id,
            "reason": request.reason,
            "score_change": -15,
            "soft_delete": 1,
            "user_id": actor_user_id
        })
        
        cancellation_event_id = result.lastrowid
        
        db.commit()
        
        return CancelRecurringGiftResponse(
            success=True,
            message="Recurring gift cancelled successfully",
            cancellation_event_id=cancellation_event_id,
            updated_engagement_score=new_score
        )

    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


@app.get(
    "/constituents/{id}/engagement-score",
    operation_id="get_constituent_engagement_score",
    response_model=EngagementScoreResponse
)
def get_constituent_engagement_score(
    id: str = Path(..., description="The ID of the constituent")
):
    """
    Fetch the current engagement score for a specified constituent by ID.
    """
    db = SessionLocal()
    try:
        query = text("""
            SELECT engagement_score 
            FROM constituents 
            WHERE id = :c_id
        """)
        result = db.execute(query, {"c_id": id}).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Constituent not found")
            
        return EngagementScoreResponse(
            constituent_id=id,
            engagement_score=result[0]
        )
    finally:
        db.close()


@app.put(
    "/constituents/{id}/engagement-score",
    operation_id="update_constituent_engagement_score",
    response_model=UpdateEngagementScoreResponse
)
def update_constituent_engagement_score(
    id: str = Path(..., description="The ID of the constituent"),
    request: UpdateEngagementScoreRequest = Body(...)
):
    """
    Update the engagement score for a specified constituent by ID with the given change value.
    """
    db = SessionLocal()
    try:
        # Get current score
        query = text("""
            SELECT engagement_score 
            FROM constituents 
            WHERE id = :c_id
        """)
        result = db.execute(query, {"c_id": id}).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Constituent not found")
            
        previous_score = result[0]
        new_score = previous_score + request.change
        
        # Update score
        update_query = text("""
            UPDATE constituents 
            SET engagement_score = :new_score, updated_at = CURRENT_TIMESTAMP
            WHERE id = :c_id
        """)
        db.execute(update_query, {"new_score": new_score, "c_id": id})
        db.commit()
        
        return UpdateEngagementScoreResponse(
            constituent_id=id,
            previous_engagement_score=previous_score,
            new_engagement_score=new_score
        )
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
