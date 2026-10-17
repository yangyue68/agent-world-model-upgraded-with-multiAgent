import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException, status, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, ForeignKey, CheckConstraint, case
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
    email = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

class Listing(Base):
    __tablename__ = "listings"
    
    id = Column(Integer, primary_key=True, index=True)
    seller_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text)
    price = Column(Float, nullable=False)
    status = Column(String, nullable=False, default="active")
    handling_time_days = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

class ShippingOption(Base):
    __tablename__ = "shipping_options"
    
    id = Column(Integer, primary_key=True, index=True)
    listing_id = Column(Integer, ForeignKey("listings.id"), nullable=False)
    shipping_type = Column(String, nullable=False)
    carrier_name = Column(String)
    service_name = Column(String)
    cost = Column(Float, nullable=False, default=0.0)
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

# Pydantic Models
class ShippingOptionRequest(BaseModel):
    shipping_type: str
    carrier_name: Optional[str] = None
    service_name: Optional[str] = None
    cost: float

class UpdateListingShippingRequest(BaseModel):
    handling_time_days: int
    shipping_options: List[ShippingOptionRequest]

class ShippingOptionResponse(BaseModel):
    id: int
    shipping_type: str
    carrier_name: Optional[str] = None
    service_name: Optional[str] = None
    cost: float

class UpdatedListingResponse(BaseModel):
    id: int
    title: str
    handling_time_days: int

class UpdateListingShippingResponse(BaseModel):
    message: str
    updated_listing: UpdatedListingResponse
    updated_shipping_options: List[ShippingOptionResponse]

class ListingDetailsResponse(BaseModel):
    id: int
    title: str
    description: str
    price: float
    status: str
    handling_time_days: int
    shipping_options: List[ShippingOptionResponse]

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.put("/listings/{listing_id}/shipping", response_model=UpdateListingShippingResponse, operation_id="update_listing_shipping")
def update_listing_shipping(listing_id: int, shipping_update: UpdateListingShippingRequest, db: Session = Depends(get_db)):
    # Get the current user ID from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Verify the listing exists and belongs to the current user
    listing = db.query(Listing).filter(Listing.id == listing_id, Listing.seller_id == current_user_id).first()
    if not listing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Listing not found or you don't have permission to update it"
        )
    
    # Update handling time
    listing.handling_time_days = shipping_update.handling_time_days
    
    # Delete existing shipping options for this listing
    db.query(ShippingOption).filter(ShippingOption.listing_id == listing_id).delete()
    
    # Create new shipping options
    new_shipping_options = []
    for option_data in shipping_update.shipping_options:
        shipping_option = ShippingOption(
            listing_id=listing_id,
            shipping_type=option_data.shipping_type,
            carrier_name=option_data.carrier_name,
            service_name=option_data.service_name,
            cost=option_data.cost
        )
        db.add(shipping_option)
        db.flush()  # Get the ID without committing
        new_shipping_options.append(shipping_option)
    
    db.commit()
    
    # Prepare response
    shipping_response = [
        ShippingOptionResponse(
            id=opt.id,
            shipping_type=opt.shipping_type,
            carrier_name=opt.carrier_name,
            service_name=opt.service_name,
            cost=opt.cost
        ) for opt in new_shipping_options
    ]
    
    updated_listing = UpdatedListingResponse(
        id=listing.id,
        title=listing.title,
        handling_time_days=listing.handling_time_days
    )
    
    return UpdateListingShippingResponse(
        message="Shipping options updated successfully",
        updated_listing=updated_listing,
        updated_shipping_options=shipping_response
    )

@app.get("/listings/{listing_id}", response_model=ListingDetailsResponse, operation_id="get_listing_details")
def get_listing_details(listing_id: int, db: Session = Depends(get_db)):
    # Get the current user ID from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    # Verify the listing exists and belongs to the current user
    listing = db.query(Listing).filter(Listing.id == listing_id, Listing.seller_id == current_user_id).first()
    if not listing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Listing not found or you don't have permission to view it"
        )
    
    # Get shipping options for this listing
    shipping_options = db.query(ShippingOption).filter(ShippingOption.listing_id == listing_id).all()
    
    shipping_response = [
        ShippingOptionResponse(
            id=opt.id,
            shipping_type=opt.shipping_type,
            carrier_name=opt.carrier_name,
            service_name=opt.service_name,
            cost=opt.cost
        ) for opt in shipping_options
    ]
    
    return ListingDetailsResponse(
        id=listing.id,
        title=listing.title,
        description=listing.description,
        price=listing.price,
        status=listing.status,
        handling_time_days=listing.handling_time_days,
        shipping_options=shipping_response
    )
