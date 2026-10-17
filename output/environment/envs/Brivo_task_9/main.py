import os
from datetime import datetime, date
from typing import Optional, List

from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

# Initialize engine with the provided DATABASE_PATH
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class UserBrief(BaseModel):
    id: int
    username: str
    full_name: Optional[str] = None


class CredentialBrief(BaseModel):
    id: int
    credential_type: str
    status: str


class AccessGroupBrief(BaseModel):
    id: int
    name: str


class AuditRecordResponse(BaseModel):
    id: int
    data_center_name: str
    actor_user: UserBrief
    action: str
    target_type: str
    affected_user: Optional[UserBrief] = None
    affected_credential: Optional[CredentialBrief] = None
    affected_access_group: Optional[AccessGroupBrief] = None
    timestamp: str
    details: Optional[str] = None


def parse_datetime(dt_str: Optional[str]) -> Optional[str]:
    """Normalize datetime string to ISO 8601 format."""
    if dt_str is None:
        return None
    # Handle space-separated format 'YYYY-MM-DD HH:MM:SS' or similar
    try:
        # Try parsing as standard ISO first
        dt_obj = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        return dt_obj.isoformat()
    except ValueError:
        pass
    
    # Fallback for common formats like '2025-03-05 09:15:00'
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt_obj = datetime.strptime(dt_str, fmt)
            return dt_obj.isoformat()
        except ValueError:
            continue
            
    return dt_str


@app.get("/audit-records", response_model=List[AuditRecordResponse], operation_id="find_audit_records")
def find_audit_records(
    data_center_name: str = Query(..., description="Name of the data center"),
    start_date: str = Query(..., description="Start date in YYYY-MM-DD format"),
    end_date: str = Query(..., description="End date in YYYY-MM-DD format"),
    actions: Optional[List[str]] = Query(None, description="List of actions to filter by")
):
    """
    Retrieve Brivo audit records for a specific data center within a date range.
    """
    
    # Validate dates
    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        # Set end time to end of day
        end_dt = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    # Build query
    base_query = """
        SELECT 
            ar.id,
            dc.name AS data_center_name,
            au.id AS actor_id,
            au.username AS actor_username,
            au.full_name AS actor_full_name,
            ar.action,
            ar.target_type,
            aff_u.id AS affected_user_id,
            aff_u.username AS affected_user_username,
            aff_u.full_name AS affected_user_full_name,
            aff_c.id AS affected_credential_id,
            aff_c.credential_type AS affected_credential_type,
            aff_c.status AS affected_credential_status,
            aff_ag.id AS affected_access_group_id,
            aff_ag.name AS affected_access_group_name,
            ar.timestamp,
            ar.details
        FROM audit_records ar
        JOIN data_centers dc ON ar.data_center_id = dc.id
        JOIN users au ON ar.actor_user_id = au.id
        LEFT JOIN users aff_u ON ar.affected_user_id = aff_u.id
        LEFT JOIN credentials aff_c ON ar.affected_credential_id = aff_c.id
        LEFT JOIN access_groups aff_ag ON ar.affected_access_group_id = aff_ag.id
        WHERE dc.name = :dc_name
          AND ar.timestamp >= :start_ts
          AND ar.timestamp <= :end_ts
    """
    
    params = {
        "dc_name": data_center_name,
        "start_ts": start_dt.strftime("%Y-%m-%d %H:%M:%S.%f"),
        "end_ts": end_dt.strftime("%Y-%m-%d %H:%M:%S.%f")
    }
    
    if actions:
        placeholders = []
        for i, act in enumerate(actions):
            key = f"act_{i}"
            placeholders.append(f":{key}")
            params[key] = act
        action_clause = ", ".join(placeholders)
        base_query += f" AND ar.action IN ({action_clause})"
        
    base_query += " ORDER BY ar.timestamp ASC"
    
    results = []
    with engine.connect() as conn:
        rows = conn.execute(text(base_query), params).fetchall()
        
        for row in rows:
            actor_user = UserBrief(
                id=row.actor_id,
                username=row.actor_username,
                full_name=row.actor_full_name
            )
            
            affected_user = None
            if row.affected_user_id is not None:
                affected_user = UserBrief(
                    id=row.affected_user_id,
                    username=row.affected_user_username,
                    full_name=row.affected_user_full_name
                )
                
            affected_credential = None
            if row.affected_credential_id is not None:
                affected_credential = CredentialBrief(
                    id=row.affected_credential_id,
                    credential_type=row.affected_credential_type,
                    status=row.affected_credential_status
                )
                
            affected_access_group = None
            if row.affected_access_group_id is not None:
                affected_access_group = AccessGroupBrief(
                    id=row.affected_access_group_id,
                    name=row.affected_access_group_name
                )
                
            ts_normalized = parse_datetime(row.timestamp)
            
            record = AuditRecordResponse(
                id=row.id,
                data_center_name=row.data_center_name,
                actor_user=actor_user,
                action=row.action,
                target_type=row.target_type,
                affected_user=affected_user,
                affected_credential=affected_credential,
                affected_access_group=affected_access_group,
                timestamp=ts_normalized,
                details=row.details
            )
            results.append(record)
            
    return results
