import os
import re
from datetime import datetime, date
from typing import List, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Path, Query, Depends
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, Integer, String, Date, Float, DateTime, ForeignKey, CheckConstraint, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

# Database setup
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class City(Base):
    __tablename__ = "cities"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    state = Column(String)
    country = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Listing(Base):
    __tablename__ = "listings"
    id = Column(Integer, primary_key=True, index=True)
    host_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    city_id = Column(Integer, ForeignKey("cities.id"), nullable=False)
    title = Column(String, nullable=False)
    description = Column(String)
    address = Column(String)
    price_per_night = Column(Float, nullable=False)
    cleaning_fee = Column(Float, default=0.0)
    max_guests = Column(Integer, nullable=False)
    bedrooms = Column(Integer)
    beds = Column(Integer)
    baths = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Booking(Base):
    __tablename__ = "bookings"
    id = Column(Integer, primary_key=True, index=True)
    listing_id = Column(Integer, ForeignKey("listings.id"), nullable=False)
    guest_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    check_in_date = Column(Date, nullable=False)
    check_out_date = Column(Date, nullable=False)
    total_price = Column(Float, nullable=False)
    status = Column(String, default="confirmed")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic models
class BookingResponse(BaseModel):
    id: int
    listing_id: int
    guest_id: int
    check_in_date: date
    check_out_date: date
    total_price: float
    status: str

class BookingUpdateRequest(BaseModel):
    new_checkout_date: date

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/bookings/upcoming", response_model=List[BookingResponse], operation_id="find_upcoming_bookings")
def find_upcoming_bookings(
    listing_title: str = Query(...),
    city_name: str = Query(...),
    state: str = Query(...),
    db = Depends(get_db)
):
    # Find the city
    city = db.query(City).filter(
        City.name == city_name,
        City.state == state
    ).first()
    
    if not city:
        raise HTTPException(status_code=404, detail="City not found")
    
    # Find the listing
    listing = db.query(Listing).filter(
        Listing.title == listing_title,
        Listing.city_id == city.id
    ).first()
    
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    
    # Find upcoming bookings
    upcoming_bookings = db.query(Booking).filter(
        Booking.listing_id == listing.id,
        Booking.check_in_date >= date.today(),
        Booking.status.in_(["confirmed", "pending"])
    ).all()
    
    return [
        BookingResponse(
            id=b.id,
            listing_id=b.listing_id,
            guest_id=b.guest_id,
            check_in_date=b.check_in_date,
            check_out_date=b.check_out_date,
            total_price=b.total_price,
            status=b.status
        ) for b in upcoming_bookings
    ]

@app.put("/bookings/{booking_id}", response_model=BookingResponse, operation_id="update_booking_checkout_date")
def update_booking_checkout_date(
    booking_id: int = Path(...),
    request: BookingUpdateRequest = None,
    db = Depends(get_db)
):
    # Get current booking
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    
    # Validate new checkout date
    new_checkout_date = request.new_checkout_date
    if new_checkout_date <= booking.check_in_date:
        raise HTTPException(status_code=400, detail="Checkout date must be after check-in date")
    
    # Get listing details
    listing = db.query(Listing).filter(Listing.id == booking.listing_id).first()
    
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    
    # Calculate new total price
    nights = (new_checkout_date - booking.check_in_date).days
    new_total_price = (nights * listing.price_per_night) + listing.cleaning_fee
    
    # Update booking
    booking.check_out_date = new_checkout_date
    booking.total_price = new_total_price
    booking.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(booking)
    
    return BookingResponse(
        id=booking.id,
        listing_id=booking.listing_id,
        guest_id=booking.guest_id,
        check_in_date=booking.check_in_date,
        check_out_date=booking.check_out_date,
        total_price=booking.total_price,
        status=booking.status
    )
