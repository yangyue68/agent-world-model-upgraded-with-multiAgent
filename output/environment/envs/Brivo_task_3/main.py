import os
from datetime import datetime
from typing import Literal

from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text, CheckConstraint, case
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
    full_name = Column(String, nullable=False)
    email = Column(String, unique=True)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Site(Base):
    __tablename__ = "sites"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    address = Column(Text)
    timezone = Column(String, default="UTC")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class AccessGroup(Base):
    __tablename__ = "access_groups"
    id = Column(Integer, primary_key=True, index=True)
    site_id = Column(Integer, ForeignKey("sites.id"), nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    site = relationship("Site")

class Door(Base):
    __tablename__ = "doors"
    id = Column(Integer, primary_key=True, index=True)
    site_id = Column(Integer, ForeignKey("sites.id"), nullable=False)
    name = Column(String, nullable=False)
    location = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    site = relationship("Site")

class AccessGroupDoor(Base):
    __tablename__ = "access_group_doors"
    access_group_id = Column(Integer, ForeignKey("access_groups.id"), primary_key=True)
    door_id = Column(Integer, ForeignKey("doors.id"), primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Schedule(Base):
    __tablename__ = "schedules"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class ScheduleInterval(Base):
    __tablename__ = "schedule_intervals"
    id = Column(Integer, primary_key=True, index=True)
    schedule_id = Column(Integer, ForeignKey("schedules.id"), nullable=False)
    day_of_week = Column(Integer, nullable=False)
    start_time = Column(String, nullable=False)
    end_time = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    schedule = relationship("Schedule")
    __table_args__ = (
        CheckConstraint("day_of_week BETWEEN 0 AND 6", name="check_day_of_week"),
        CheckConstraint("start_time < end_time", name="check_time_order"),
    )

class Employee(Base):
    __tablename__ = "employees"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True)
    site_id = Column(Integer, ForeignKey("sites.id"), nullable=False)
    employee_number = Column(String, unique=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String)
    phone = Column(String)
    access_group_id = Column(Integer, ForeignKey("access_groups.id"))
    schedule_id = Column(Integer, ForeignKey("schedules.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    user = relationship("User")
    site = relationship("Site")
    access_group = relationship("AccessGroup")
    schedule = relationship("Schedule")

# Request Models
class AssignEmployeeToAccessGroupRequest(BaseModel):
    access_group_id: int

class UpdateEmployeeScheduleRequest(BaseModel):
    schedule_id: int

# Response Models
class AssignEmployeeToAccessGroupResponse(BaseModel):
    message: str
    employee_id: int
    access_group_id: int

class UpdateEmployeeScheduleResponse(BaseModel):
    message: str
    employee_id: int
    schedule_id: int

app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.put("/employees/{employee_id}/access_group", response_model=AssignEmployeeToAccessGroupResponse, operation_id="assign_employee_to_access_group")
def assign_employee_to_access_group(employee_id: int, payload: AssignEmployeeToAccessGroupRequest, db = Depends(get_db)):
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    
    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    
    access_group = db.query(AccessGroup).filter(AccessGroup.id == payload.access_group_id).first()
    if not access_group:
        raise HTTPException(status_code=404, detail="Access group not found")
    
    # Check if the employee belongs to the same site as the access group
    if employee.site_id != access_group.site_id:
        raise HTTPException(status_code=400, detail="Employee and access group must belong to the same site")
    
    # Update employee's access group
    employee.access_group_id = payload.access_group_id
    employee.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(employee)
    
    return AssignEmployeeToAccessGroupResponse(
        message="Employee assigned to access group successfully",
        employee_id=employee.id,
        access_group_id=payload.access_group_id
    )

@app.put("/employees/{employee_id}/schedule", response_model=UpdateEmployeeScheduleResponse, operation_id="update_employee_schedule")
def update_employee_schedule(employee_id: int, payload: UpdateEmployeeScheduleRequest, db = Depends(get_db)):
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    
    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    
    schedule = db.query(Schedule).filter(Schedule.id == payload.schedule_id).first()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    
    # Update employee's schedule
    employee.schedule_id = payload.schedule_id
    employee.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(employee)
    
    return UpdateEmployeeScheduleResponse(
        message="Employee schedule updated successfully",
        employee_id=employee.id,
        schedule_id=payload.schedule_id
    )
