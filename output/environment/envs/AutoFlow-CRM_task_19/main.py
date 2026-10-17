import os
from datetime import datetime, date
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Query, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Date, DateTime, Text, ForeignKey, CheckConstraint, case
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
    full_name = Column(String, nullable=False)
    email = Column(String, unique=True)
    phone = Column(String)
    role = Column(String, nullable=False, default="salesperson")
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

class Customer(Base):
    __tablename__ = "customers"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    company = Column(String)
    email = Column(String)
    phone = Column(String)
    address = Column(String)
    notes = Column(Text)
    owner_user_id = Column(Integer, ForeignKey("users.id"))
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

class FollowUpTask(Base):
    __tablename__ = "follow_up_tasks"
    
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, nullable=False)
    assigned_to_user_id = Column(Integer, nullable=False)
    created_by_user_id = Column(Integer, nullable=False)
    subject = Column(String, nullable=False)
    description = Column(Text)
    task_type = Column(String, nullable=False, default="call")
    due_date = Column(Date, nullable=False)
    due_time = Column(String)
    status = Column(String, nullable=False, default="pending")
    priority = Column(String, nullable=False, default="normal")
    completed_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

# Pydantic Models
class CustomerSearchResult(BaseModel):
    id: int
    name: str

class UserSearchResult(BaseModel):
    id: int
    full_name: str

class FollowUpTaskCreate(BaseModel):
    customer_id: int
    assigned_to_user_id: int
    subject: str
    description: Optional[str] = None
    task_type: Optional[str] = "call"
    due_date: str
    due_time: Optional[str] = None
    priority: Optional[str] = "normal"

class FollowUpTaskResponse(FollowUpTaskCreate):
    id: int
    created_by_user_id: int
    status: str
    completed_at: Optional[str] = None
    created_at: str
    updated_at: str

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/customers/search", response_model=List[CustomerSearchResult], operation_id="search_customers")
def search_customers(name: str, db: Session = Depends(get_db)):
    customers = db.query(Customer).filter(Customer.name.contains(name)).all()
    return [{"id": c.id, "name": c.name} for c in customers]

@app.get("/users/search", response_model=List[UserSearchResult], operation_id="search_users")
def search_users(username: str, db: Session = Depends(get_db)):
    users = db.query(User).filter(User.username.contains(username)).all()
    return [{"id": u.id, "full_name": u.full_name} for u in users]

@app.post("/follow_up_tasks", response_model=FollowUpTaskResponse, operation_id="create_follow_up_task")
def create_follow_up_task(task: FollowUpTaskCreate, db: Session = Depends(get_db)):
    # Validate customer exists
    customer = db.query(Customer).filter(Customer.id == task.customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    # Validate user exists
    user = db.query(User).filter(User.id == task.assigned_to_user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Assigned user not found")
    
    # Get current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    if current_user_id == 0:
        raise HTTPException(status_code=400, detail="Current user not set")
    
    # Parse date string to date object
    due_date_obj = datetime.strptime(task.due_date, "%Y-%m-%d").date()
    
    # Create task
    db_task = FollowUpTask(
        customer_id=task.customer_id,
        assigned_to_user_id=task.assigned_to_user_id,
        created_by_user_id=current_user_id,
        subject=task.subject,
        description=task.description,
        task_type=task.task_type,
        due_date=due_date_obj,
        due_time=task.due_time,
        priority=task.priority
    )
    
    db.add(db_task)
    db.commit()
    db.refresh(db_task)
    
    return {
        "id": db_task.id,
        "customer_id": db_task.customer_id,
        "assigned_to_user_id": db_task.assigned_to_user_id,
        "created_by_user_id": db_task.created_by_user_id,
        "subject": db_task.subject,
        "description": db_task.description,
        "task_type": db_task.task_type,
        "due_date": str(db_task.due_date),
        "due_time": db_task.due_time,
        "status": db_task.status,
        "priority": db_task.priority,
        "completed_at": db_task.completed_at,
        "created_at": db_task.created_at.isoformat(),
        "updated_at": db_task.updated_at.isoformat()
    }
