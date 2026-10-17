import os
import datetime
from typing import Optional, List
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, Column, Integer, String, Date, DateTime, ForeignKey, CheckConstraint, case
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
    username = Column(String, nullable=False, unique=True)
    email = Column(String, nullable=False, unique=True)
    full_name = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

class Asset(Base):
    __tablename__ = "assets"
    
    id = Column(Integer, primary_key=True, index=True)
    asset_tag = Column(String, nullable=False, unique=True)
    name = Column(String, nullable=False)
    description = Column(String)
    asset_type = Column(String, nullable=False)
    calibration_expiry_date = Column(Date)
    custodian_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

class Workflow(Base):
    __tablename__ = "workflows"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String)
    trigger_type = Column(String, nullable=False)
    trigger_asset_tag = Column(String)
    trigger_condition = Column(String)
    is_active = Column(Integer, nullable=False, default=1)
    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"))
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    # Add check constraint for is_active
    __table_args__ = (
        CheckConstraint("is_active IN (0,1)", name="chk_is_active"),
    )

class WorkflowAction(Base):
    __tablename__ = "workflow_actions"
    
    id = Column(Integer, primary_key=True, index=True)
    workflow_id = Column(Integer, ForeignKey("workflows.id", ondelete="CASCADE"))
    action_type = Column(String, nullable=False)
    action_config = Column(String)
    execution_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

class MaintenanceTicket(Base):
    __tablename__ = "maintenance_tickets"
    
    id = Column(Integer, primary_key=True, index=True)
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="CASCADE"))
    workflow_id = Column(Integer, ForeignKey("workflows.id", ondelete="SET NULL"))
    title = Column(String, nullable=False)
    description = Column(String)
    priority = Column(String, nullable=False)
    status = Column(String, nullable=False, default="open")
    external_ticket_id = Column(String)
    external_system = Column(String)
    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"))
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    # Add check constraints
    __table_args__ = (
        CheckConstraint("priority IN ('low','medium','high','critical')", name="chk_priority"),
        CheckConstraint("status IN ('open','in_progress','resolved','closed')", name="chk_status"),
    )

class EmailNotification(Base):
    __tablename__ = "email_notifications"
    
    id = Column(Integer, primary_key=True, index=True)
    workflow_id = Column(Integer, ForeignKey("workflows.id", ondelete="SET NULL"))
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="SET NULL"))
    recipient_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    recipient_email = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    body = Column(String)
    status = Column(String, nullable=False, default="pending")
    sent_at = Column(DateTime)
    error_message = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    # Add check constraint
    __table_args__ = (
        CheckConstraint("status IN ('pending','sent','failed')", name="chk_status"),
    )

class WorkflowExecutionLog(Base):
    __tablename__ = "workflow_execution_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    workflow_id = Column(Integer, ForeignKey("workflows.id", ondelete="CASCADE"))
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="SET NULL"))
    triggered_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    status = Column(String, nullable=False)
    details = Column(String)
    executed_at = Column(DateTime, default=datetime.datetime.utcnow)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    # Add check constraint
    __table_args__ = (
        CheckConstraint("status IN ('success','partial_failure','failure')", name="chk_status"),
    )

# Pydantic models
class WorkflowCreate(BaseModel):
    name: str
    description: Optional[str] = None
    trigger_asset_tag: str
    trigger_condition: str

class WorkflowActionCreate(BaseModel):
    action_type: str
    action_config: str

class AssetResponse(BaseModel):
    id: int
    asset_tag: str
    name: str
    calibration_expiry_date: Optional[datetime.date]
    custodian_user_id: Optional[int]

class UserResponse(BaseModel):
    id: int
    email: str
    full_name: str

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/workflows", response_model=dict, operation_id="create_workflow")
def create_workflow(workflow: WorkflowCreate, db):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    
    db_workflow = Workflow(
        name=workflow.name,
        description=workflow.description,
        trigger_type="calibration_expiry",
        trigger_asset_tag=workflow.trigger_asset_tag,
        trigger_condition=workflow.trigger_condition,
        created_by_user_id=current_user_id
    )
    
    db.add(db_workflow)
    db.commit()
    db.refresh(db_workflow)
    
    return {
        "id": db_workflow.id,
        "name": db_workflow.name,
        "trigger_asset_tag": db_workflow.trigger_asset_tag,
        "trigger_condition": db_workflow.trigger_condition,
        "is_active": bool(db_workflow.is_active)
    }

@app.post("/workflows/{workflow_id}/actions", response_model=dict, operation_id="add_workflow_action")
def add_workflow_action(workflow_id: int, action: WorkflowActionCreate, db):
    db_workflow_action = WorkflowAction(
        workflow_id=workflow_id,
        action_type=action.action_type,
        action_config=action.action_config
    )
    
    db.add(db_workflow_action)
    db.commit()
    db.refresh(db_workflow_action)
    
    return {
        "id": db_workflow_action.id,
        "workflow_id": db_workflow_action.workflow_id,
        "action_type": db_workflow_action.action_type,
        "action_config": db_workflow_action.action_config
    }

@app.get("/assets/{asset_tag}", response_model=AssetResponse, operation_id="get_asset_by_tag")
def get_asset_by_tag(asset_tag: str, db):
    db_asset = db.query(Asset).filter(Asset.asset_tag == asset_tag).first()
    if not db_asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    
    return {
        "id": db_asset.id,
        "asset_tag": db_asset.asset_tag,
        "name": db_asset.name,
        "calibration_expiry_date": db_asset.calibration_expiry_date,
        "custodian_user_id": db_asset.custodian_user_id
    }

@app.get("/users/{user_id}", response_model=UserResponse, operation_id="get_user_by_id")
def get_user_by_id(user_id: int, db):
    db_user = db.query(User).filter(User.id == user_id).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return {
        "id": db_user.id,
        "email": db_user.email,
        "full_name": db_user.full_name
    }
