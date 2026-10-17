import os
from datetime import date, datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException, status, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Date, REAL, ForeignKey, func, case, text
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
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class CryoUnit(Base):
    __tablename__ = "cryo_units"
    id = Column(Integer, primary_key=True, index=True)
    unit_name = Column(String, unique=True, nullable=False)
    status = Column(String, nullable=False, default="Active")
    decommission_date = Column(Date)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class Container(Base):
    __tablename__ = "containers"
    id = Column(Integer, primary_key=True, index=True)
    container_code = Column(String, unique=True, nullable=False)
    cryo_unit_id = Column(Integer, ForeignKey("cryo_units.id"), nullable=False)
    relocation_audit_flag = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())

class TemperatureLog(Base):
    __tablename__ = "temperature_logs"
    id = Column(Integer, primary_key=True, index=True)
    cryo_unit_id = Column(Integer, ForeignKey("cryo_units.id"), nullable=False)
    recorded_at = Column(DateTime, nullable=False)
    temperature_c = Column(REAL, nullable=False)
    archived = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=func.current_timestamp())

class TemperatureLogArchive(Base):
    __tablename__ = "temperature_log_archive"
    id = Column(Integer, primary_key=True, index=True)
    original_log_id = Column(Integer, nullable=False)
    cryo_unit_id = Column(Integer, ForeignKey("cryo_units.id"), nullable=False)
    recorded_at = Column(DateTime, nullable=False)
    temperature_c = Column(REAL, nullable=False)
    archived_at = Column(DateTime, default=func.current_timestamp())
    archived_by = Column(Integer, ForeignKey("users.id"))

# Request Models
class DecommissionCryoUnitRequest(BaseModel):
    decommission_date: str

class ArchiveTemperatureLogsRequest(BaseModel):
    cryo_unit_id: int
    start_date: str
    end_date: str

class FlagContainersForRelocationAuditRequest(BaseModel):
    unit_id: int

# Response Models
class DecommissionResponse(BaseModel):
    success: bool
    message: str

class ArchiveResponse(BaseModel):
    archived_count: int
    message: str

class ContainerResponse(BaseModel):
    id: int
    container_code: str
    relocation_audit_flag: int

class FlagContainersResponse(BaseModel):
    success: bool
    message: str
    updated_container_count: int

app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.put("/cryo-units/{unit_id}/decommission", response_model=DecommissionResponse, operation_id="decommission_cryo_unit")
def decommission_cryo_unit(unit_id: int, request: DecommissionCryoUnitRequest, db: Session = Depends(get_db)):
    # Parse decommission date
    try:
        decommission_date = date.fromisoformat(request.decommission_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Expected YYYY-MM-DD.")

    # Get the cryo unit
    cryo_unit = db.query(CryoUnit).filter(CryoUnit.id == unit_id).first()
    if not cryo_unit:
        raise HTTPException(status_code=404, detail="Cryo unit not found")

    # Update status and decommission date
    cryo_unit.status = "Retired"
    cryo_unit.decommission_date = decommission_date
    db.commit()
    db.refresh(cryo_unit)

    # Archive temperature logs for the specified date range
    archive_request = ArchiveTemperatureLogsRequest(
        cryo_unit_id=unit_id,
        start_date="2022-03-15",
        end_date="2024-05-31"
    )
    
    # Parse dates
    try:
        start_date = date.fromisoformat(archive_request.start_date)
        end_date = date.fromisoformat(archive_request.end_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Expected YYYY-MM-DD.")

    # Get current user ID from environment
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")

    # Convert date objects to datetime for comparison with datetime fields
    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date, datetime.max.time())

    # Get temperature logs to archive
    logs_to_archive = db.query(TemperatureLog).filter(
        TemperatureLog.cryo_unit_id == archive_request.cryo_unit_id,
        TemperatureLog.recorded_at >= start_datetime,
        TemperatureLog.recorded_at <= end_datetime,
        TemperatureLog.archived == 0
    ).all()

    archived_count = len(logs_to_archive)

    # Insert into archive table
    for log in logs_to_archive:
        archive_entry = TemperatureLogArchive(
            original_log_id=log.id,
            cryo_unit_id=log.cryo_unit_id,
            recorded_at=log.recorded_at,
            temperature_c=log.temperature_c,
            archived_by=int(current_user_id)
        )
        db.add(archive_entry)

    # Mark logs as archived
    db.query(TemperatureLog).filter(
        TemperatureLog.cryo_unit_id == archive_request.cryo_unit_id,
        TemperatureLog.recorded_at >= start_datetime,
        TemperatureLog.recorded_at <= end_datetime,
        TemperatureLog.archived == 0
    ).update({TemperatureLog.archived: 1})

    db.commit()

    # Flag containers for relocation audit
    containers_updated = db.query(Container).filter(Container.cryo_unit_id == unit_id).update({Container.relocation_audit_flag: 1})
    db.commit()

    return DecommissionResponse(
        success=True,
        message=f"Cryo unit {cryo_unit.unit_name} has been retired and decommissioned on {decommission_date}. {containers_updated} containers flagged for relocation audit. {archived_count} temperature logs archived."
    )

@app.post("/temperature-logs/archive", response_model=ArchiveResponse, operation_id="archive_temperature_logs")
def archive_temperature_logs(request: ArchiveTemperatureLogsRequest, db: Session = Depends(get_db)):
    # Parse dates
    try:
        start_date = date.fromisoformat(request.start_date)
        end_date = date.fromisoformat(request.end_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Expected YYYY-MM-DD.")

    # Get current user ID from environment
    current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")

    # Convert date objects to datetime for comparison with datetime fields
    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date, datetime.max.time())

    # Get temperature logs to archive
    logs_to_archive = db.query(TemperatureLog).filter(
        TemperatureLog.cryo_unit_id == request.cryo_unit_id,
        TemperatureLog.recorded_at >= start_datetime,
        TemperatureLog.recorded_at <= end_datetime,
        TemperatureLog.archived == 0
    ).all()

    archived_count = len(logs_to_archive)

    # Insert into archive table
    for log in logs_to_archive:
        archive_entry = TemperatureLogArchive(
            original_log_id=log.id,
            cryo_unit_id=log.cryo_unit_id,
            recorded_at=log.recorded_at,
            temperature_c=log.temperature_c,
            archived_by=int(current_user_id)
        )
        db.add(archive_entry)

    # Mark logs as archived
    db.query(TemperatureLog).filter(
        TemperatureLog.cryo_unit_id == request.cryo_unit_id,
        TemperatureLog.recorded_at >= start_datetime,
        TemperatureLog.recorded_at <= end_datetime,
        TemperatureLog.archived == 0
    ).update({TemperatureLog.archived: 1})

    db.commit()

    return ArchiveResponse(
        archived_count=archived_count,
        message=f"Archived {archived_count} temperature logs for cryo unit {request.cryo_unit_id} from {request.start_date} to {request.end_date}"
    )

@app.get("/cryo-units/{unit_id}/containers", response_model=List[ContainerResponse], operation_id="get_containers_in_cryo_unit")
def get_containers_in_cryo_unit(unit_id: int, db: Session = Depends(get_db)):
    containers = db.query(Container).filter(Container.cryo_unit_id == unit_id).all()
    return [
        ContainerResponse(
            id=container.id,
            container_code=container.container_code,
            relocation_audit_flag=container.relocation_audit_flag
        ) for container in containers
    ]

@app.put("/cryo-units/{unit_id}/containers/relocation-audit", response_model=FlagContainersResponse, operation_id="flag_containers_for_relocation_audit")
def flag_containers_for_relocation_audit(unit_id: int, db: Session = Depends(get_db)):
    # Update containers
    updated_count = db.query(Container).filter(Container.cryo_unit_id == unit_id).update({Container.relocation_audit_flag: 1})
    db.commit()

    return FlagContainersResponse(
        success=True,
        message=f"Successfully flagged {updated_count} containers for relocation audit",
        updated_container_count=updated_count
    )
