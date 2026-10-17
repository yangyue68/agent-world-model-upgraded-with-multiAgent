import os
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Date, DateTime, REAL, TEXT, ForeignKey, CheckConstraint, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

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
    bio = Column(TEXT)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Listing(Base):
    __tablename__ = "listings"
    id = Column(Integer, primary_key=True, index=True)
    host_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    description = Column(TEXT)
    city = Column(String, nullable=False)
    state = Column(String, nullable=False)
    country = Column(String, nullable=False)
    address = Column(String)
    latitude = Column(REAL)
    longitude = Column(REAL)
    price_per_night = Column(REAL, nullable=False)
    max_guests = Column(Integer, nullable=False)
    bedrooms = Column(Integer, nullable=False)
    bathrooms = Column(REAL, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Booking(Base):
    __tablename__ = "bookings"
    id = Column(Integer, primary_key=True, index=True)
    listing_id = Column(Integer, ForeignKey("listings.id"), nullable=False)
    guest_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    check_in_date = Column(Date, nullable=False)
    check_out_date = Column(Date, nullable=False)
    total_price = Column(REAL, nullable=False)
    status = Column(String, nullable=False, default="confirmed")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    recipient_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    listing_id = Column(Integer, ForeignKey("listings.id"))
    booking_id = Column(Integer, ForeignKey("bookings.id"))
    body = Column(TEXT, nullable=False)
    read_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class ListingSearchRequest(BaseModel):
    title: str
    city: str
    state: str

class SendMessageRequest(BaseModel):
    sender_id: int
    recipient_id: int
    listing_id: Optional[int] = None
    booking_id: Optional[int] = None
    body: str

class MessageResponse(BaseModel):
    id: int
    sender_id: int
    recipient_id: int
    listing_id: Optional[int] = None
    booking_id: Optional[int] = None
    body: str
    read_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/listings/search", response_model=list[dict], operation_id="search_listings")
def search_listings(title: str, city: str, state: str, db=None):
    if db is None:
        db = next(get_db())
    
    results = db.query(Listing.id, Listing.title, Listing.host_id).filter(
        Listing.title == title,
        Listing.city == city,
        Listing.state == state
    ).all()
    
    return [{"id": r[0], "title": r[1], "host_id": r[2]} for r in results]

@app.post("/messages", response_model=MessageResponse, operation_id="send_message")
def send_message(message: SendMessageRequest, db=None):
    if db is None:
        db = next(get_db())
    
    # Get current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    # Validate that sender is current user
    if message.sender_id != current_user_id:
        raise HTTPException(status_code=403, detail="Sender ID does not match current user")
    
    # Create new message
    db_message = Message(
        sender_id=message.sender_id,
        recipient_id=message.recipient_id,
        listing_id=message.listing_id,
        booking_id=message.booking_id,
        body=message.body
    )
    
    db.add(db_message)
    db.commit()
    db.refresh(db_message)
    
    return {
        "id": db_message.id,
        "sender_id": db_message.sender_id,
        "recipient_id": db_message.recipient_id,
        "listing_id": db_message.listing_id,
        "booking_id": db_message.booking_id,
        "body": db_message.body,
        "read_at": db_message.read_at,
        "created_at": db_message.created_at,
        "updated_at": db_message.updated_at
    }

# Task execution endpoint
@app.post("/task_10_execute", response_model=MessageResponse)
def execute_task_10(db=None):
    if db is None:
        db = next(get_db())
    
    # Find the listing
    listing = db.query(Listing).filter(
        Listing.title == "Downtown Loft with City View",
        Listing.city == "Portland",
        Listing.state == "OR"
    ).first()
    
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    
    # Find the booking
    booking = db.query(Booking).filter(
        Booking.listing_id == listing.id,
        Booking.check_in_date == "2024-06-12",
        Booking.check_out_date == "2024-06-15"
    ).first()
    
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    
    # Prepare message content
    message_body = "Hi, I'm wondering if early check-in at 10 AM is possible for my stay from June 12–15, 2024."
    
    # Send message
    message_request = SendMessageRequest(
        sender_id=int(os.environ.get("AWM_CURRENT_USER_ID")),
        recipient_id=listing.host_id,
        listing_id=listing.id,
        booking_id=booking.id,
        body=message_body
    )
    
    return send_message(message_request, db)
