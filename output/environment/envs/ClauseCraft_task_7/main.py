import os
from datetime import datetime, date
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

# Initialize FastAPI app
app = FastAPI(title="ClauseCraft API")

# Database configuration
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

# Pydantic Models for Request Body
class AddClauseRequest(BaseModel):
    clause_title: str = Field(..., description="The title of the clause to be added")
    jurisdiction: str = Field(..., description="The jurisdiction associated with the clause")
    risk_level: Optional[str] = Field(None, description="The risk level of the clause (low, medium, high, critical)")
    last_reviewed: Optional[str] = Field(None, description="The date the clause was last reviewed (YYYY-MM-DD)")
    position: int = Field(..., description="The position where the clause should be inserted in the draft")

class AddClauseResponse(BaseModel):
    success: bool
    message: str

def get_current_user_id() -> int:
    """Retrieve the current user ID from environment variable."""
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="Missing authenticated user context")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=500, detail="Invalid user ID format")

@app.post(
    "/drafts/{draft_id}/clauses",
    operation_id="add_clause_to_draft",
    response_model=AddClauseResponse,
    tags=["PrimaryResource"]
)
def add_clause_to_draft(
    draft_id: str,
    version_id: str = Query(..., description="The version identifier for the draft"),
    request_body: AddClauseRequest = None
):
    """
    Inserts a clause into a draft contract at the specified position.
    The clause is identified by its title and jurisdiction, and the draft is identified by its ID.
    The version_id must also be provided to ensure correct versioning.
    Optional fields risk_level and last_reviewed can be included to fully specify the clause.
    """
    
    # Validate input
    if request_body is None:
        raise HTTPException(status_code=422, detail="Request body is required")
    
    # Get current user ID
    current_user_id = get_current_user_id()
    
    # Parse last_reviewed date if provided
    parsed_last_reviewed = None
    if request_body.last_reviewed:
        try:
            # Handle both ISO 8601 formats: YYYY-MM-DD and potentially others
            # SQLite stores dates as TEXT, so we keep it as string or convert carefully
            # For simplicity and compatibility with existing data, we'll store as YYYY-MM-DD string
            d = datetime.strptime(request_body.last_reviewed, "%Y-%m-%d").date()
            parsed_last_reviewed = d.isoformat()
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid date format for last_reviewed. Expected YYYY-MM-DD")

    # Validate risk_level against allowed values in schema
    allowed_risk_levels = ['low', 'medium', 'high', 'critical']
    if request_body.risk_level and request_body.risk_level not in allowed_risk_levels:
        raise HTTPException(status_code=422, detail=f"Invalid risk_level. Must be one of {allowed_risk_levels}")

    with engine.connect() as conn:
        # Start transaction implicitly
        
        # 1. Check if the draft contract exists
        result = conn.execute(text("SELECT id FROM contracts WHERE id = :id"), {"id": draft_id})
        if not result.fetchone():
            raise HTTPException(status_code=404, detail=f"Draft contract '{draft_id}' not found")
        
        # 2. Find or Create the Clause
        # Look for existing clause with same title and jurisdiction
        clause_result = conn.execute(
            text("""
                SELECT id, risk_level, last_reviewed 
                FROM clauses 
                WHERE title = :title AND jurisdiction = :jurisdiction
            """),
            {"title": request_body.clause_title, "jurisdiction": request_body.jurisdiction}
        )
        existing_clause = clause_result.fetchone()
        
        clause_id = None
        
        if existing_clause:
            clause_id = existing_clause[0]
            # Update clause details if new info is provided and differs? 
            # The spec says "Insert the clause...". Usually, if it exists, we might just link it.
            # However, if risk_level or last_reviewed are provided, they might need updating on the master clause record.
            # Let's update the master clause record if new values are provided.
            updates = []
            params = {"id": clause_id}
            
            if request_body.risk_level:
                updates.append("risk_level = :risk_level")
                params["risk_level"] = request_body.risk_level
            
            if parsed_last_reviewed:
                updates.append("last_reviewed = :last_reviewed")
                params["last_reviewed"] = parsed_last_reviewed
                
            if updates:
                updates.append("updated_at = CURRENT_TIMESTAMP")
                sql_update = f"UPDATE clauses SET {', '.join(updates)} WHERE id = :id"
                conn.execute(text(sql_update), params)
        else:
            # Create new clause
            # Default risk_level if not provided? Schema requires NOT NULL.
            # Spec says risk_level is optional in request. If missing, what do we do?
            # Looking at initial snapshot, all clauses have a risk_level.
            # If not provided, we might default to 'low' or fail. 
            # Given "Optional fields... can be included to fully specify", if omitted, 
            # we probably shouldn't invent one unless there's a business rule.
            # But DB constraint is NOT NULL. 
            # Let's assume if not provided, we cannot insert a *new* clause without a risk level.
            # Or perhaps we default to 'low'? 
            # Safer: Require it for new clauses? The prompt doesn't say that.
            # Let's look at the task: "Insert the clause titled ... risk level: high ...". It IS provided.
            # If it weren't provided for a NEW clause, we'd hit a constraint error.
            # I will set a default of 'low' if not provided for new creation to satisfy NOT NULL, 
            # but log/return warning? No, just proceed.
            final_risk_level = request_body.risk_level if request_body.risk_level else 'low'
            
            insert_clause_sql = text("""
                INSERT INTO clauses (title, jurisdiction, risk_level, last_reviewed, created_at, updated_at)
                VALUES (:title, :jurisdiction, :risk_level, :last_reviewed, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """)
            conn.execute(insert_clause_sql, {
                "title": request_body.clause_title,
                "jurisdiction": request_body.jurisdiction,
                "risk_level": final_risk_level,
                "last_reviewed": parsed_last_reviewed
            })
            # Get the last inserted row id
            clause_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()

        # 3. Handle Position Shifting
        # We need to insert at `position`. Existing clauses at >= position need to shift up by 1.
        # First, check if position is valid (> 0)
        if request_body.position <= 0:
            raise HTTPException(status_code=422, detail="Position must be greater than 0")

        # Shift existing clauses at or after the target position
        shift_sql = text("""
            UPDATE draft_clauses 
            SET position = position + 1, updated_at = CURRENT_TIMESTAMP
            WHERE draft_id = :draft_id AND position >= :target_position
        """)
        conn.execute(shift_sql, {
            "draft_id": draft_id,
            "target_position": request_body.position
        })

        # 4. Insert the new draft-clause association
        insert_draft_clause_sql = text("""
            INSERT INTO draft_clauses (draft_id, clause_id, position, version_id, added_by, created_at, updated_at)
            VALUES (:draft_id, :clause_id, :position, :version_id, :added_by, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """)
        conn.execute(insert_draft_clause_sql, {
            "draft_id": draft_id,
            "clause_id": clause_id,
            "position": request_body.position,
            "version_id": version_id,
            "added_by": current_user_id
        })
        
        conn.commit()

    return AddClauseResponse(
        success=True,
        message=f"Clause '{request_body.clause_title}' added to draft '{draft_id}' at position {request_body.position}"
    )
