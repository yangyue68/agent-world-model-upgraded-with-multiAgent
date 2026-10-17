import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class RemovalRequest(BaseModel):
    removal_reason: str


class DependentResponse(BaseModel):
    id: int
    user_id: int
    full_name: str
    status: str
    removed_at: str
    removal_reason: str


@app.delete("/dependents/{dependent_id}", response_model=DependentResponse, operation_id="remove_dependent")
def remove_dependent(
    dependent_id: int = Path(..., description="Internal ID of the dependent to remove"),
    body: RemovalRequest = Body(...),
):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user context")

    with engine.connect() as conn:
        # Fetch the dependent to verify existence and ownership
        stmt_select = text("""
            SELECT id, user_id, full_name, status 
            FROM dependents 
            WHERE id = :dep_id AND user_id = :uid
        """)
        result = conn.execute(stmt_select, {"dep_id": dependent_id, "uid": current_user_id})
        row = result.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Dependent not found or does not belong to current user")

        dep_id, db_user_id, full_name, status = row
        
        if status != 'active':
            raise HTTPException(status_code=400, detail=f"Cannot remove dependent with status '{status}'. Only active dependents can be removed.")

        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        # Update the dependent record
        stmt_update = text("""
            UPDATE dependents 
            SET status = 'removed', 
                removed_at = :removed_at, 
                removal_reason = :reason,
                updated_at = :updated_at
            WHERE id = :dep_id
        """)
        
        conn.execute(
            stmt_update,
            {
                "dep_id": dep_id,
                "removed_at": now_utc,
                "reason": body.removal_reason,
                "updated_at": now_utc
            }
        )
        conn.commit()

        return DependentResponse(
            id=dep_id,
            user_id=db_user_id,
            full_name=full_name,
            status='removed',
            removed_at=now_utc,
            removal_reason=body.removal_reason
        )
