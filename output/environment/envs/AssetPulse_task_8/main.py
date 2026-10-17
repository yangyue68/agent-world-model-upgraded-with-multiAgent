import os
import datetime
from typing import Optional
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, status, Depends
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text, CheckConstraint, case
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
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

class AssetType(Base):
    __tablename__ = "asset_types"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

class CustomAttribute(Base):
    __tablename__ = "custom_attributes"
    id = Column(Integer, primary_key=True, index=True)
    asset_type_id = Column(Integer, ForeignKey("asset_types.id"))
    name = Column(String, nullable=False)
    data_type = Column(String, nullable=False)
    validation_rule = Column(Text)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)
    __table_args__ = (CheckConstraint("data_type IN ('TEXT','INTEGER','REAL','DATE','DATETIME','BOOLEAN')"),)

class Asset(Base):
    __tablename__ = "assets"
    id = Column(Integer, primary_key=True, index=True)
    asset_type_id = Column(Integer, ForeignKey("asset_types.id"))
    name = Column(String, nullable=False)
    custodian_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

class AssetAttributeValue(Base):
    __tablename__ = "asset_attribute_values"
    id = Column(Integer, primary_key=True, index=True)
    asset_id = Column(Integer, ForeignKey("assets.id"))
    custom_attribute_id = Column(Integer, ForeignKey("custom_attributes.id"))
    value = Column(Text)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

class Alert(Base):
    __tablename__ = "alerts"
    id = Column(Integer, primary_key=True, index=True)
    asset_id = Column(Integer, ForeignKey("assets.id"))
    custom_attribute_id = Column(Integer, ForeignKey("custom_attributes.id"))
    alert_type = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    scheduled_for = Column(DateTime, nullable=False)
    sent_at = Column(DateTime)
    status = Column(String, default="pending")
    recipient_user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)
    __table_args__ = (CheckConstraint("status IN ('pending','sent','cancelled','failed')"),)

# Pydantic Models
class UpdateCustomAttributeValueRequest(BaseModel):
    value: str

class UpdateCustomAttributeValueResponse(BaseModel):
    success: bool
    message: str

class GetCustomAttributeValueResponse(BaseModel):
    value: str

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def validate_date_format(date_str: str) -> datetime.date:
    try:
        return datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid date format. Expected YYYY-MM-DD."
        )

def check_calibration_due_date_validation(db: Session, custom_attribute_id: int, new_value: str) -> None:
    # Check if this is the Calibration Due Date attribute
    attr = db.query(CustomAttribute).filter(CustomAttribute.id == custom_attribute_id).first()
    if not attr or attr.name != "Calibration Due Date":
        return
    
    # Validate that the new date is not earlier than today
    new_date = validate_date_format(new_value)
    today = datetime.date.today()
    
    if new_date < today:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Calibration due date cannot be earlier than today."
        )

def schedule_alert_for_custodian(db: Session, asset_id: int, custom_attribute_id: int, new_expiry_date: datetime.date) -> None:
    # Find the asset and its custodian
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset or not asset.custodian_id:
        return
    
    # Calculate alert date (7 days before expiry)
    alert_date = new_expiry_date - datetime.timedelta(days=7)
    
    # Create alert message
    attr = db.query(CustomAttribute).filter(CustomAttribute.id == custom_attribute_id).first()
    if not attr:
        return
        
    message = f"Calibration for {asset.name} is due on {new_expiry_date.strftime('%Y-%m-%d')}."
    
    # Create alert entry
    alert = Alert(
        asset_id=asset_id,
        custom_attribute_id=custom_attribute_id,
        alert_type="calibration_reminder",
        message=message,
        scheduled_for=datetime.datetime.combine(alert_date, datetime.time(8, 0)),
        recipient_user_id=asset.custodian_id,
        status="pending"
    )
    
    db.add(alert)
    db.commit()

@app.put("/assets/{asset_id}/custom_attributes/{custom_attribute_id}", response_model=UpdateCustomAttributeValueResponse, operation_id="update_asset_custom_attribute_value")
def update_asset_custom_attribute_value(
    asset_id: int,
    custom_attribute_id: int,
    payload: UpdateCustomAttributeValueRequest,
    db: Session = Depends(get_db)
):
    # Validate date format
    new_value = payload.value
    try:
        new_date = validate_date_format(new_value)
    except HTTPException:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid date format. Expected YYYY-MM-DD."
        )
    
    # Check if the attribute exists
    attr = db.query(CustomAttribute).filter(CustomAttribute.id == custom_attribute_id).first()
    if not attr:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Custom attribute not found."
        )
    
    # Check if the asset exists
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found."
        )
    
    # Validate the date for Calibration Due Date
    check_calibration_due_date_validation(db, custom_attribute_id, new_value)
    
    # Update the attribute value
    asset_attr_value = db.query(AssetAttributeValue).filter(
        AssetAttributeValue.asset_id == asset_id,
        AssetAttributeValue.custom_attribute_id == custom_attribute_id
    ).first()
    
    if not asset_attr_value:
        # Create new attribute value
        asset_attr_value = AssetAttributeValue(
            asset_id=asset_id,
            custom_attribute_id=custom_attribute_id,
            value=new_value
        )
        db.add(asset_attr_value)
    else:
        # Update existing attribute value
        asset_attr_value.value = new_value
        asset_attr_value.updated_at = datetime.datetime.utcnow()
    
    db.commit()
    db.refresh(asset_attr_value)
    
    # Schedule alert for custodian if it's a calibration due date
    if attr.name == "Calibration Due Date":
        schedule_alert_for_custodian(db, asset_id, custom_attribute_id, new_date)
    
    return UpdateCustomAttributeValueResponse(
        success=True,
        message="Custom attribute value updated successfully."
    )

@app.get("/assets/{asset_id}/custom_attributes/{custom_attribute_id}", response_model=GetCustomAttributeValueResponse, operation_id="get_asset_custom_attribute_value")
def get_asset_custom_attribute_value(
    asset_id: int,
    custom_attribute_id: int,
    db: Session = Depends(get_db)
):
    # Check if the asset exists
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found."
        )
    
    # Check if the attribute exists
    attr = db.query(CustomAttribute).filter(CustomAttribute.id == custom_attribute_id).first()
    if not attr:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Custom attribute not found."
        )
    
    # Get the attribute value
    asset_attr_value = db.query(AssetAttributeValue).filter(
        AssetAttributeValue.asset_id == asset_id,
        AssetAttributeValue.custom_attribute_id == custom_attribute_id
    ).first()
    
    if not asset_attr_value:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Custom attribute value not found for this asset."
        )
    
    return GetCustomAttributeValueResponse(value=asset_attr_value.value)
