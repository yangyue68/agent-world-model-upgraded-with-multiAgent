import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Date, DateTime, ForeignKey, CheckConstraint, case
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
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Listing(Base):
    __tablename__ = "listings"
    
    id = Column(Integer, primary_key=True, index=True)
    host_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    city = Column(String, nullable=False)
    state = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Booking(Base):
    __tablename__ = "bookings"
    
    id = Column(Integer, primary_key=True, index=True)
    listing_id = Column(Integer, ForeignKey("listings.id"), nullable=False)
    guest_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    check_in_date = Column(Date, nullable=False)
    check_out_date = Column(Date, nullable=False)
    status = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'confirmed', 'cancelled', 'completed')"),
        CheckConstraint("check_out_date > check_in_date"),
    )

class SpecialRequest(Base):
    __tablename__ = "special_requests"
    
    id = Column(Integer, primary_key=True, index=True)
    booking_id = Column(Integer, ForeignKey("bookings.id"), nullable=False)
    message = Column(String, nullable=False)
    status = Column(String, default="sent", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        CheckConstraint("status IN ('sent', 'delivered', 'read', 'failed')"),
    )

# Pydantic Models
class ListingSearchResponse(BaseModel):
    id: int
    title: str
    city: str
    state: str

class BookingResponse(BaseModel):
    id: int
    listing_id: int
    guest_id: int
    check_in_date: str
    check_out_date: str
    status: str

class SpecialRequestCreate(BaseModel):
    booking_id: int
    message: str

class SpecialRequestResponse(BaseModel):
    id: int
    booking_id: int
    message: str
    status: str

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/listings/search", response_model=List[ListingSearchResponse], operation_id="search_listings")
def search_listings(title: str, city: str, state: str, db: Session = Depends(get_db)):
    listings = db.query(Listing).filter(
        Listing.title == title,
        Listing.city == city,
        Listing.state == state
    ).all()
    return listings

@app.get("/bookings/find_by_dates_and_listing", response_model=List[BookingResponse], operation_id="find_bookings_by_dates_and_listing")
def find_bookings_by_dates_and_listing(listing_id: int, check_in_date: str, check_out_date: str, db: Session = Depends(get_db)):
    bookings = db.query(Booking).filter(
        Booking.listing_id == listing_id,
        Booking.check_in_date == check_in_date,
        Booking.check_out_date == check_out_date,
        Booking.status == "confirmed"
    ).all()
    return bookings

@app.post("/special_requests", response_model=SpecialRequestResponse, operation_id="create_special_request")
def create_special_request(request: SpecialRequestCreate, db: Session = Depends(get_db)):
    # Verify booking exists and is confirmed
    booking = db.query(Booking).filter(
        Booking.id == request.booking_id,
        Booking.status == "confirmed"
    ).first()
    
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found or not confirmed")
    
    # Create special request
    special_request = SpecialRequest(
        booking_id=request.booking_id,
        message=request.message,
        status="sent"
    )
    
    db.add(special_request)
    db.commit()
    db.refresh(special_request)
    
    return special_request
