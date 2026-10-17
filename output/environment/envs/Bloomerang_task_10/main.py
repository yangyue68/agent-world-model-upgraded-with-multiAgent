import os
import sqlite3
from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Date, DateTime, ForeignKey, func, case, and_, or_
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Organization(Base):
    __tablename__ = "organizations"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Donor(Base):
    __tablename__ = "donors"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String)
    last_gift_date = Column(Date)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Event(Base):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    name = Column(String, nullable=False)
    event_date = Column(Date, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class EventAttendance(Base):
    __tablename__ = "event_attendances"
    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    donor_id = Column(Integer, ForeignKey("donors.id"), nullable=False)
    attended_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Campaign(Base):
    __tablename__ = "campaigns"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    donor_id = Column(Integer, ForeignKey("donors.id"))
    campaign_id = Column(Integer, ForeignKey("campaigns.id"))
    assigned_to_user_id = Column(Integer, ForeignKey("users.id"))
    subject = Column(String, nullable=False)
    due_date = Column(Date, nullable=False)
    status = Column(String, default="pending", check_constraint="status IN ('pending', 'completed', 'cancelled')")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic models
class CreateBatchFollowupTasksRequest(BaseModel):
    organization_id: int
    event_name: str
    campaign_name: str
    start_date: str
    end_date: str

class CreateBatchFollowupTasksResponse(BaseModel):
    created_task_count: int
    failed_donors: List[dict]

class CampaignSearchResult(BaseModel):
    id: int
    name: str

class EventSearchResult(BaseModel):
    id: int
    name: str

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def is_business_day(date):
    """Check if a date is a business day (Monday-Friday)"""
    return date.weekday() < 5

def add_business_days(start_date, days):
    """Add business days to a date"""
    current_date = start_date
    added = 0
    while added < days:
        current_date += timedelta(days=1)
        if is_business_day(current_date):
            added += 1
    return current_date

@app.post("/batch_followup_tasks", response_model=CreateBatchFollowupTasksResponse, operation_id="create_batch_followup_tasks")
def create_batch_followup_tasks(
    organization_id: int = Query(...),
    event_name: str = Query(...),
    campaign_name: str = Query(...),
    start_date: str = Query(...),
    end_date: str = Query(...),
    db = Depends(get_db)
):
    # Parse dates
    try:
        start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    # Get campaign ID
    campaign = db.query(Campaign).filter(
        Campaign.organization_id == organization_id,
        Campaign.name == campaign_name
    ).first()
    
    if not campaign:
        raise HTTPException(status_code=404, detail=f"Campaign '{campaign_name}' not found for organization {organization_id}")

    # Get event ID
    event = db.query(Event).filter(
        Event.organization_id == organization_id,
        Event.name == event_name,
        Event.event_date >= start_date_obj,
        Event.event_date <= end_date_obj
    ).first()
    
    if not event:
        raise HTTPException(status_code=404, detail=f"Event '{event_name}' not found for organization {organization_id} within the specified date range")

    # Find lapsed donors who attended the event
    # Lapsed donors: last gift more than 18 months ago
    eighteen_months_ago = datetime.utcnow().date() - timedelta(days=18*30)
    
    # Get donors who attended the event and haven't given in the last 18 months
    donors_query = db.query(Donor).join(
        EventAttendance, Donor.id == EventAttendance.donor_id
    ).filter(
        EventAttendance.event_id == event.id,
        Donor.last_gift_date < eighteen_months_ago
    )
    
    donors = donors_query.all()
    
    # Calculate due date (5 business days from now)
    today = datetime.utcnow().date()
    due_date = add_business_days(today, 5)
    
    # Prepare response data
    created_task_count = 0
    failed_donors = []
    
    # Create tasks for each donor
    for donor in donors:
        try:
            # Create task
            task = Task(
                organization_id=organization_id,
                donor_id=donor.id,
                campaign_id=campaign.id,
                subject="We miss you — here's what's new at [Org Name]!",
                due_date=due_date,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            
            db.add(task)
            db.commit()
            db.refresh(task)
            created_task_count += 1
            
        except Exception as e:
            failed_donors.append({
                "donor_id": donor.id,
                "reason": str(e)
            })
    
    return {
        "created_task_count": created_task_count,
        "failed_donors": failed_donors
    }

@app.get("/campaigns/search", response_model=List[CampaignSearchResult], operation_id="search_campaigns_by_name")
def search_campaigns_by_name(
    name: str = Query(...),
    organization_id: int = Query(...),
    db = Depends(get_db)
):
    campaigns = db.query(Campaign).filter(
        Campaign.organization_id == organization_id,
        Campaign.name.like(f"%{name}%")
    ).all()
    
    return [{"id": c.id, "name": c.name} for c in campaigns]

@app.get("/events/search", response_model=List[EventSearchResult], operation_id="search_events_by_name")
def search_events_by_name(
    name: str = Query(...),
    organization_id: int = Query(...),
    db = Depends(get_db)
):
    events = db.query(Event).filter(
        Event.organization_id == organization_id,
        Event.name.like(f"%{name}%")
    ).all()
    
    return [{"id": e.id, "name": e.name} for e in events]
