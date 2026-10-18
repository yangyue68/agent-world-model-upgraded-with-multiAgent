import os
from datetime import datetime, date
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query
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


class CampaignCreate(BaseModel):
    name: str
    description: Optional[str] = None
    start_date: str  # YYYY-MM-DD
    end_date: str    # YYYY-MM-DD
    points_multiplier: float
    target_segment_name: str
    auto_apply: bool


class SegmentResponse(BaseModel):
    id: int
    name: str


class CampaignResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    start_date: str
    end_date: str
    points_multiplier: float
    target_segment_id: int
    auto_apply: bool
    status: str
    created_at: str
    updated_at: str


def parse_iso_datetime(dt_str: str) -> datetime:
    """Parse ISO-8601 string handling both 'T...Z' and space-separated formats."""
    if dt_str.endswith('Z'):
        dt_str = dt_str[:-1] + '+00:00'
    try:
        return datetime.fromisoformat(dt_str)
    except ValueError:
        # Fallback for specific formats like "YYYY-MM-DD HH:MM:SS"
        return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")


@app.post("/campaigns", response_model=CampaignResponse, operation_id="create_campaign")
def create_campaign(payload: CampaignCreate):
    db = SessionLocal()
    try:
        # 1. Find the segment ID based on target_segment_name
        seg_res = db.execute(text("SELECT id FROM segments WHERE name = :name"), {"name": payload.target_segment_name})
        segment_row = seg_res.fetchone()
        
        if not segment_row:
            raise HTTPException(status_code=404, detail=f"Segment '{payload.target_segment_name}' not found")
        
        target_segment_id = segment_row[0]

        # 2. Validate dates
        try:
            start_dt = date.fromisoformat(payload.start_date)
            end_dt = date.fromisoformat(payload.end_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

        if end_dt < start_dt:
            raise HTTPException(status_code=400, detail="End date must be greater than or equal to start date.")

        # 3. Determine Status
        today = date.today()
        if start_dt > today:
            status = "Scheduled"
        elif end_dt < today:
            status = "Expired"
        else:
            status = "Active"

        # 4. Insert into campaigns table
        now_ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        
        insert_sql = text("""
            INSERT INTO campaigns (
                name, 
                description, 
                start_date, 
                end_date, 
                points_multiplier, 
                target_segment_id, 
                auto_apply, 
                status, 
                created_at, 
                updated_at
            ) VALUES (
                :name, 
                :description, 
                :start_date, 
                :end_date, 
                :points_multiplier, 
                :target_segment_id, 
                :auto_apply, 
                :status, 
                :now_ts, 
                :now_ts
            ) RETURNING id
        """)
        
        res = db.execute(insert_sql, {
            "name": payload.name,
            "description": payload.description,
            "start_date": payload.start_date,
            "end_date": payload.end_date,
            "points_multiplier": payload.points_multiplier,
            "target_segment_id": target_segment_id,
            "auto_apply": 1 if payload.auto_apply else 0,
            "status": status,
            "now_ts": now_ts
        })
        
        campaign_id = res.scalar_one_or_none()
        db.commit()

        if campaign_id is None:
             raise HTTPException(status_code=500, detail="Failed to create campaign")

        # 5. If auto_apply is True, apply to all existing eligible subscribers in that segment
        if payload.auto_apply:
            # Eligible subscribers are those in the target segment with status 'Active'
            # Note: The spec says "automatically applied to all existing and new subscribers".
            # For existing ones, we link them here. New ones would typically be handled by triggers or background jobs,
            # but since we can't modify schema/triggers per instructions ("Never create/drop/alter..."),
            # we only handle existing subscribers in this request context as implied by typical API behavior for such specs.
            
            select_subs_sql = text("""
                SELECT s.id 
                FROM subscribers s
                JOIN segments sg ON s.segment_id = sg.id
                WHERE sg.name = :seg_name AND s.status = 'Active'
            """)
            
            subs_res = db.execute(select_subs_sql, {"seg_name": payload.target_segment_name})
            subscriber_ids = [row[0] for row in subs_res.fetchall()]
            
            if subscriber_ids:
                # Batch insert into campaign_subscribers
                # We need to avoid duplicates if any exist (though unlikely for a new campaign)
                # Using INSERT OR IGNORE logic via unique constraint
                
                insert_cs_sql = text("""
                    INSERT OR IGNORE INTO campaign_subscribers (
                        campaign_id, 
                        subscriber_id, 
                        applied_at, 
                        created_at, 
                        updated_at
                    ) VALUES (
                        :cid, 
                        :sid, 
                        :ts, 
                        :ts, 
                        :ts
                    )
                """)
                
                params_list = []
                for sid in subscriber_ids:
                    params_list.append({
                        "cid": campaign_id,
                        "sid": sid,
                        "ts": now_ts
                    })
                
                if params_list:
                    db.execute(insert_cs_sql, params_list)
                    db.commit()

        # 6. Fetch the created campaign to return full details
        fetch_sql = text("""
            SELECT 
                id, 
                name, 
                description, 
                start_date, 
                end_date, 
                points_multiplier, 
                target_segment_id, 
                auto_apply, 
                status, 
                created_at, 
                updated_at
            FROM campaigns 
            WHERE id = :id
        """)
        
        final_res = db.execute(fetch_sql, {"id": campaign_id})
        row = final_res.fetchone()
        
        if not row:
            raise HTTPException(status_code=500, detail="Campaign created but could not be retrieved")

        return CampaignResponse(
            id=row[0],
            name=row[1],
            description=row[2],
            start_date=str(row[3]),
            end_date=str(row[4]),
            points_multiplier=float(row[5]),
            target_segment_id=row[6],
            auto_apply=bool(row[7]),
            status=row[8],
            created_at=str(row[9]),
            updated_at=str(row[10])
        )

    except Exception as e:
        db.rollback()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.get("/segments", response_model=List[SegmentResponse], operation_id="find_segments")
def find_segments(name: str = Query(..., description="Segment name")):
    db = SessionLocal()
    try:
        sql = text("SELECT id, name FROM segments WHERE name LIKE :pattern")
        # Case-insensitive partial match usually expected for "Find by name" unless exact match specified.
        # Spec says "Filter segments by the supplied name". Given example policy illustrative, 
        # let's assume exact match first? Or partial? 
        # Usually "find_segments" implies search. Let's do case-insensitive contains.
        pattern = f"%{name}%"
        
        res = db.execute(sql, {"pattern": pattern})
        rows = res.fetchall()
        
        return [SegmentResponse(id=r[0], name=r[1]) for r in rows]
    finally:
        db.close()
