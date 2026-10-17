import os
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text, func, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

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
    email = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Location(Base):
    __tablename__ = "locations"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Asset(Base):
    __tablename__ = "assets"
    id = Column(Integer, primary_key=True, index=True)
    asset_tag = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    asset_type = Column(String, nullable=False)
    current_location_id = Column(Integer, ForeignKey("locations.id"))
    last_checkout_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    current_location = relationship("Location")

class AssetMovement(Base):
    __tablename__ = "asset_movements"
    id = Column(Integer, primary_key=True, index=True)
    asset_id = Column(Integer, ForeignKey("assets.id"), nullable=False)
    from_location_id = Column(Integer, ForeignKey("locations.id"))
    to_location_id = Column(Integer, ForeignKey("locations.id"), nullable=False)
    moved_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    moved_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    asset = relationship("Asset")
    from_location = relationship("Location", foreign_keys=[from_location_id])
    to_location = relationship("Location", foreign_keys=[to_location_id])
    moved_by_user = relationship("User")

# Request/Response Models
class AssetMovementRequestItem(BaseModel):
    asset_tag: str
    from_location_name: str
    to_location_name: str
    moved_at: str

class BulkUpdateAssetMovementsRequest(BaseModel):
    assets: List[AssetMovementRequestItem]

class BulkUpdateAssetMovementsResponse(BaseModel):
    success_count: int
    failed_updates: List[dict]

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/locations", tags=["PrimaryResource"], operation_id="find_locations")
def find_locations(name: Optional[str] = None):
    db = next(get_db())
    query = db.query(Location)
    if name:
        query = query.filter(Location.name.contains(name))
    locations = query.all()
    return [
        {
            "id": loc.id,
            "name": loc.name,
            "description": loc.description
        }
        for loc in locations
    ]

@app.post("/asset-movements/bulk", tags=["PrimaryResource"], response_model=BulkUpdateAssetMovementsResponse, operation_id="bulk_update_asset_movements")
def bulk_update_asset_movements(request: BulkUpdateAssetMovementsRequest):
    db = next(get_db())
    success_count = 0
    failed_updates = []
    
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    for item in request.assets:
        try:
            # Find asset by tag
            asset = db.query(Asset).filter(Asset.asset_tag == item.asset_tag).first()
            if not asset:
                failed_updates.append({
                    "asset_tag": item.asset_tag,
                    "error": f"Asset with tag {item.asset_tag} not found"
                })
                continue
            
            # Find from location
            from_location = db.query(Location).filter(Location.name == item.from_location_name).first()
            if not from_location:
                failed_updates.append({
                    "asset_tag": item.asset_tag,
                    "error": f"From location '{item.from_location_name}' not found"
                })
                continue
                
            # Find to location
            to_location = db.query(Location).filter(Location.name == item.to_location_name).first()
            if not to_location:
                failed_updates.append({
                    "asset_tag": item.asset_tag,
                    "error": f"To location '{item.to_location_name}' not found"
                })
                continue
            
            # Parse moved_at timestamp
            moved_at = datetime.fromisoformat(item.moved_at.replace('Z', '+00:00'))
            
            # Create new asset movement record
            new_movement = AssetMovement(
                asset_id=asset.id,
                from_location_id=from_location.id,
                to_location_id=to_location.id,
                moved_by_user_id=current_user_id,
                moved_at=moved_at
            )
            db.add(new_movement)
            
            # Update asset's current location and last checkout time
            asset.current_location_id = to_location.id
            asset.last_checkout_at = moved_at
            
            success_count += 1
            
        except Exception as e:
            failed_updates.append({
                "asset_tag": item.asset_tag,
                "error": str(e)
            })
    
    db.commit()
    
    return BulkUpdateAssetMovementsResponse(
        success_count=success_count,
        failed_updates=failed_updates
    )
