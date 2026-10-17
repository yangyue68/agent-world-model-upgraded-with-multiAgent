import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Path, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text, func, case
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
    email = Column(String, nullable=False)
    full_name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Part(Base):
    __tablename__ = "parts"
    id = Column(Integer, primary_key=True, index=True)
    part_number = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    reorder_point = Column(Integer, nullable=False, default=0)
    quantity_on_hand = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class NotificationSetting(Base):
    __tablename__ = "notification_settings"
    id = Column(Integer, primary_key=True, index=True)
    setting_key = Column(String, unique=True, nullable=False)
    setting_value = Column(String, nullable=False)
    updated_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class CategoryDetails(BaseModel):
    id: int
    name: str

class NotificationSettingItem(BaseModel):
    id: int
    setting_key: str
    setting_value: str

class UserItem(BaseModel):
    id: int
    username: str
    email: str
    full_name: str

class UpdateCategoryReorderPointRequest(BaseModel):
    reorder_point: int

class UpdateNotificationSettingsRequest(BaseModel):
    settings: List[NotificationSettingItem]

class UpdateCategoryReorderPointResponse(BaseModel):
    message: str
    affected_parts_count: int

class UpdateNotificationSettingsResponse(BaseModel):
    message: str

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.put("/categories/{category_id}/reorder-point", response_model=UpdateCategoryReorderPointResponse, operation_id="update_category_reorder_point")
def update_category_reorder_point(
    category_id: int,
    reorder_point: UpdateCategoryReorderPointRequest,
    db: Session = Depends(get_db)
):
    # Verify category exists
    category = db.query(Category).filter(Category.id == category_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    # Update all parts in this category
    affected_parts_count = db.query(Part).filter(Part.category_id == category_id).update({
        Part.reorder_point: reorder_point.reorder_point
    })
    
    db.commit()
    
    return UpdateCategoryReorderPointResponse(
        message=f"Successfully updated reorder point for {affected_parts_count} parts in category '{category.name}'",
        affected_parts_count=affected_parts_count
    )

@app.get("/categories/{category_id}", response_model=CategoryDetails, operation_id="get_category_details")
def get_category_details(
    category_id: int,
    db: Session = Depends(get_db)
):
    category = db.query(Category).filter(Category.id == category_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    
    return CategoryDetails(id=category.id, name=category.name)

@app.get("/notification-settings", response_model=List[NotificationSettingItem], operation_id="get_notification_settings")
def get_notification_settings(db: Session = Depends(get_db)):
    settings = db.query(NotificationSetting).all()
    return [
        NotificationSettingItem(
            id=s.id,
            setting_key=s.setting_key,
            setting_value=s.setting_value
        ) for s in settings
    ]

@app.put("/notification-settings", response_model=UpdateNotificationSettingsResponse, operation_id="update_notification_settings")
def update_notification_settings(
    settings: UpdateNotificationSettingsRequest,
    db: Session = Depends(get_db)
):
    # Get current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    for setting_data in settings.settings:
        setting = db.query(NotificationSetting).filter(
            NotificationSetting.setting_key == setting_data.setting_key
        ).first()
        
        if setting:
            setting.setting_value = setting_data.setting_value
            setting.updated_by = current_user_id
        else:
            new_setting = NotificationSetting(
                setting_key=setting_data.setting_key,
                setting_value=setting_data.setting_value,
                updated_by=current_user_id
            )
            db.add(new_setting)
    
    db.commit()
    
    return UpdateNotificationSettingsResponse(message="Notification settings updated successfully")

@app.get("/users", response_model=List[UserItem], operation_id="list_users")
def list_users(db: Session = Depends(get_db)):
    users = db.query(User).all()
    return [
        UserItem(
            id=u.id,
            username=u.username,
            email=u.email,
            full_name=u.full_name
        ) for u in users
    ]
