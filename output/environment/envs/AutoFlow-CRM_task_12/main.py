import os
import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, ForeignKey, func, case, and_
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    phone = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class Lead(Base):
    __tablename__ = "leads"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String)
    phone = Column(String)
    company = Column(String)
    status = Column(String, nullable=False, default="new")
    assigned_to = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class FollowUpReminder(Base):
    __tablename__ = "follow_up_reminders"
    
    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    assigned_to = Column(Integer, ForeignKey("users.id"), nullable=False)
    due_date = Column(DateTime, nullable=False)
    notes = Column(Text)
    completed = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

# Pydantic Models
class UserResponse(BaseModel):
    id: int
    username: str
    full_name: str
    email: str
    phone: str
    created_at: datetime.datetime
    updated_at: datetime.datetime
    
    class Config:
        from_attributes = True

class ReassignLeadsResponse(BaseModel):
    reassigned_leads_count: int
    updated_reminders_count: int

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/users/by-username/{username}", response_model=UserResponse, operation_id="get_user_by_username")
def get_user_by_username(username: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

@app.post("/reassign-leads", response_model=ReassignLeadsResponse, operation_id="reassign_leads_and_update_reminders")
def reassign_leads_and_update_reminders(db: Session = Depends(get_db)):
    # Get Alex Turner's user ID
    alex_turner = db.query(User).filter(User.username == "alex.turner").first()
    if not alex_turner:
        raise HTTPException(status_code=404, detail="Alex Turner not found")
    
    # Get Jessica Wong's user ID
    jessica_wong = db.query(User).filter(User.username == "jessica.wong").first()
    if not jessica_wong:
        raise HTTPException(status_code=404, detail="Jessica Wong not found")
    
    # Calculate next Monday's date
    today = datetime.date.today()
    days_until_monday = (7 - today.weekday()) % 7
    if today.weekday() == 0:  # If today is Monday
        next_monday = today
    else:
        next_monday = today + datetime.timedelta(days_until_monday)
    
    # Update leads
    leads_updated = db.query(Lead).filter(Lead.assigned_to == alex_turner.id).update({
        Lead.assigned_to: jessica_wong.id
    })
    
    # Update follow-up reminders
    reminders_updated = db.query(FollowUpReminder).filter(
        FollowUpReminder.assigned_to == alex_turner.id
    ).update({
        FollowUpReminder.assigned_to: jessica_wong.id
    })
    
    # Commit changes
    db.commit()
    
    # Update reminder due dates to next Monday
    next_monday_datetime = datetime.datetime.combine(next_monday, datetime.time.min)
    
    # Update the due dates to next Monday
    db.query(FollowUpReminder).filter(
        FollowUpReminder.assigned_to == jessica_wong.id
    ).update({
        FollowUpReminder.due_date: next_monday_datetime
    })
    
    db.commit()
    
    return ReassignLeadsResponse(
        reassigned_leads_count=leads_updated,
        updated_reminders_count=reminders_updated
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
