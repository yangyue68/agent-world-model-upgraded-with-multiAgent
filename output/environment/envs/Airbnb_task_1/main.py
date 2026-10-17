import os
from datetime import date
from typing import List, Optional

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime, ForeignKey, CheckConstraint, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
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
    bio = Column(String)
    avatar_url = Column(String)
    created_at = Column(DateTime, default="CURRENT_TIMESTAMP")
    updated_at = Column(DateTime, default="CURRENT_TIMESTAMP")

class City(Base):
    __tablename__ = "cities"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    country = Column(String, nullable=False)
    latitude = Column(Float)
    longitude = Column(Float)
    created_at = Column(DateTime, default="CURRENT_TIMESTAMP")
    updated_at = Column(DateTime, default="CURRENT_TIMESTAMP")

class Listing(Base):
    __tablename__ = "listings"
    id = Column(Integer, primary_key=True, index=True)
    host_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    city_id = Column(Integer, ForeignKey("cities.id"), nullable=False)
    title = Column(String, nullable=False)
    description = Column(String)
    property_type = Column(String, CheckConstraint("property_type IN ('entire_home', 'private_room', 'shared_room')"))
    bedrooms = Column(Integer, CheckConstraint("bedrooms >= 0"))
    bathrooms = Column(Float, CheckConstraint("bathrooms >= 0"))
    max_guests = Column(Integer, CheckConstraint("max_guests > 0"))
    price_per_night = Column(Float, CheckConstraint("price_per_night >= 0"))
    currency = Column(String, default="USD")
    latitude = Column(Float)
    longitude = Column(Float)
    is_active = Column(Integer, CheckConstraint("is_active IN (0,1)"), default=1)
    created_at = Column(DateTime, default="CURRENT_TIMESTAMP")
    updated_at = Column(DateTime, default="CURRENT_TIMESTAMP")
    
    # Relationships
    host = relationship("User")
    city = relationship("City")

class Amenity(Base):
    __tablename__ = "amenities"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    icon = Column(String)
    created_at = Column(DateTime, default="CURRENT_TIMESTAMP")
    updated_at = Column(DateTime, default="CURRENT_TIMESTAMP")

class ListingAmenity(Base):
    __tablename__ = "listing_amenities"
    listing_id = Column(Integer, ForeignKey("listings.id"), primary_key=True)
    amenity_id = Column(Integer, ForeignKey("amenities.id"), primary_key=True)
    created_at = Column(DateTime, default="CURRENT_TIMESTAMP")

class Booking(Base):
    __tablename__ = "bookings"
    id = Column(Integer, primary_key=True, index=True)
    listing_id = Column(Integer, ForeignKey("listings.id"), nullable=False)
    guest_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    check_in = Column(Date, nullable=False)
    check_out = Column(Date, nullable=False)
    guests = Column(Integer, CheckConstraint("guests > 0"))
    total_price = Column(Float, CheckConstraint("total_price >= 0"))
    status = Column(String, CheckConstraint("status IN ('pending', 'confirmed', 'cancelled', 'completed')"), default="pending")
    created_at = Column(DateTime, default="CURRENT_TIMESTAMP")
    updated_at = Column(DateTime, default="CURRENT_TIMESTAMP")

# Pydantic models
class ListingResponse(BaseModel):
    id: int
    title: str
    description: str
    property_type: str
    bedrooms: int
    bathrooms: float
    max_guests: int
    price_per_night: float
    currency: str
    host_id: int
    city_id: int
    latitude: float
    longitude: float
    amenities: List[str]

    class Config:
        orm_mode = True

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/listings/search", response_model=List[ListingResponse], operation_id="search_listings")
def search_listings(
    location: str = Query(...),
    check_in: date = Query(...),
    check_out: date = Query(...),
    min_bedrooms: Optional[int] = Query(2),
    min_price: Optional[float] = Query(120),
    max_price: Optional[float] = Query(350),
    amenities: Optional[List[str]] = Query(None)
):
    db = next(get_db())
    
    # Find city
    city = db.query(City).filter(
        City.name == location.split(",")[0].strip(),
        City.country == location.split(",")[1].strip()
    ).first()
    
    if not city:
        return []
    
    # Build query
    query = db.query(Listing).filter(
        Listing.city_id == city.id,
        Listing.property_type == "entire_home",
        Listing.bedrooms >= min_bedrooms,
        Listing.price_per_night >= min_price,
        Listing.price_per_night <= max_price,
        Listing.is_active == 1
    )
    
    # Filter by amenities
    if amenities:
        for amenity_name in amenities:
            amenity = db.query(Amenity).filter(Amenity.name == amenity_name).first()
            if amenity:
                subquery = db.query(ListingAmenity.listing_id).filter(
                    ListingAmenity.amenity_id == amenity.id
                ).subquery()
                query = query.join(subquery, Listing.id == subquery.c.listing_id)
    
    # Check availability
    available_listings = []
    for listing in query.all():
        # Check if listing has any conflicting bookings
        conflicting_bookings = db.query(Booking).filter(
            Booking.listing_id == listing.id,
            Booking.status.in_(["confirmed", "pending", "completed"]),
            ~((Booking.check_out <= check_in) | (Booking.check_in >= check_out))
        ).count()
        
        if conflicting_bookings == 0:
            # Get amenities for listing
            listing_amenities = db.query(Amenity.name).join(
                ListingAmenity, ListingAmenity.amenity_id == Amenity.id
            ).filter(ListingAmenity.listing_id == listing.id).all()
            
            amenity_names = [a[0] for a in listing_amenities]
            
            # Add to results
            available_listings.append({
                "id": listing.id,
                "title": listing.title,
                "description": listing.description,
                "property_type": listing.property_type,
                "bedrooms": listing.bedrooms,
                "bathrooms": listing.bathrooms,
                "max_guests": listing.max_guests,
                "price_per_night": listing.price_per_night,
                "currency": listing.currency,
                "host_id": listing.host_id,
                "city_id": listing.city_id,
                "latitude": listing.latitude,
                "longitude": listing.longitude,
                "amenities": amenity_names
            })
    
    return available_listings
