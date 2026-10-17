import os
from datetime import datetime
from typing import List, Optional, Union

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, Text, ForeignKey, func, case
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
    role = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class StorageUnit(Base):
    __tablename__ = "storage_units"
    
    id = Column(Integer, primary_key=True, index=True)
    unit_code = Column(String, unique=True, nullable=False)
    unit_type = Column(String, nullable=False)
    parent_unit_id = Column(Integer, ForeignKey("storage_units.id"))
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Vial(Base):
    __tablename__ = "vials"
    
    id = Column(Integer, primary_key=True, index=True)
    vial_code = Column(String, unique=True, nullable=False)
    parent_vial_id = Column(Integer, ForeignKey("vials.id"))
    storage_unit_id = Column(Integer, ForeignKey("storage_units.id"))
    volume_ul = Column(Float)
    concentration = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class AliquotingEvent(Base):
    __tablename__ = "aliquoting_events"
    
    id = Column(Integer, primary_key=True, index=True)
    parent_vial_id = Column(Integer, ForeignKey("vials.id"), nullable=False)
    child_vial_id = Column(Integer, ForeignKey("vials.id"), nullable=False)
    operator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    event_timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    volume_ul = Column(Float)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Transfer(Base):
    __tablename__ = "transfers"
    
    id = Column(Integer, primary_key=True, index=True)
    vial_id = Column(Integer, ForeignKey("vials.id"), nullable=False)
    from_storage_unit_id = Column(Integer, ForeignKey("storage_units.id"))
    to_storage_unit_id = Column(Integer, ForeignKey("storage_units.id"), nullable=False)
    operator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    transfer_timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    reason = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class QCTest(Base):
    __tablename__ = "qc_tests"
    
    id = Column(Integer, primary_key=True, index=True)
    vial_id = Column(Integer, ForeignKey("vials.id"), nullable=False)
    test_type = Column(String, nullable=False)
    result_value = Column(Float)
    result_text = Column(Text)
    result_status = Column(String)
    operator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    test_timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Consent(Base):
    __tablename__ = "consents"
    
    id = Column(Integer, primary_key=True, index=True)
    vial_id = Column(Integer, ForeignKey("vials.id"), nullable=False)
    restriction_code = Column(String, nullable=False)
    restriction_description = Column(Text)
    effective_from = Column(DateTime, nullable=False, default=datetime.utcnow)
    effective_until = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic models
class VialInfo(BaseModel):
    id: int
    vial_code: str
    parent_vial_id: Optional[int]
    storage_unit_id: int
    volume_ul: float
    concentration: float
    created_at: datetime
    updated_at: datetime

class AliquotingEventResponse(BaseModel):
    id: int
    parent_vial_id: int
    child_vial_id: int
    operator_id: int
    event_timestamp: datetime
    volume_ul: float
    notes: str

class TransferResponse(BaseModel):
    id: int
    vial_id: int
    from_storage_unit_id: int
    to_storage_unit_id: int
    operator_id: int
    transfer_timestamp: datetime
    reason: str

class QCTestResponse(BaseModel):
    id: int
    vial_id: int
    test_type: str
    result_value: Optional[float]
    result_text: str
    result_status: str
    operator_id: int
    test_timestamp: datetime
    notes: str

class ConsentResponse(BaseModel):
    id: int
    vial_id: int
    restriction_code: str
    restriction_description: str
    effective_from: datetime
    effective_until: Optional[datetime]

class CustodyHistoryResponse(BaseModel):
    vial_info: VialInfo
    aliquoting_events: List[AliquotingEventResponse]
    transfers: List[TransferResponse]
    qc_tests: List[QCTestResponse]
    consents: List[ConsentResponse]

# FastAPI app
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/vials/{vial_code}/custody-history", response_model=CustodyHistoryResponse, operation_id="get_vial_custody_history")
def get_vial_custody_history(
    vial_code: str = Path(..., description="Unique identifier for the vial (e.g., VIAL-BIO-558129)")
):
    db = next(get_db())
    
    # Get the main vial
    vial = db.query(Vial).filter(Vial.vial_code == vial_code).first()
    if not vial:
        raise HTTPException(status_code=404, detail="Vial not found")
    
    # Get aliquoting events where this vial is the parent
    aliquoting_events = db.query(AliquotingEvent).filter(
        AliquotingEvent.parent_vial_id == vial.id
    ).order_by(AliquotingEvent.event_timestamp).all()
    
    # Get aliquoting events where this vial is the child
    child_aliquoting_events = db.query(AliquotingEvent).filter(
        AliquotingEvent.child_vial_id == vial.id
    ).order_by(AliquotingEvent.event_timestamp).all()
    
    # Combine both sets of aliquoting events
    all_aliquoting_events = aliquoting_events + child_aliquoting_events
    
    # Get transfers involving this vial
    transfers = db.query(Transfer).filter(
        Transfer.vial_id == vial.id
    ).order_by(Transfer.transfer_timestamp).all()
    
    # Get QC tests for this vial
    qc_tests = db.query(QCTest).filter(
        QCTest.vial_id == vial.id
    ).order_by(QCTest.test_timestamp).all()
    
    # Get consents for this vial
    consents = db.query(Consent).filter(
        Consent.vial_id == vial.id
    ).order_by(Consent.effective_from).all()
    
    # Prepare response data
    vial_info = VialInfo(
        id=vial.id,
        vial_code=vial.vial_code,
        parent_vial_id=vial.parent_vial_id,
        storage_unit_id=vial.storage_unit_id,
        volume_ul=vial.volume_ul,
        concentration=vial.concentration,
        created_at=vial.created_at,
        updated_at=vial.updated_at
    )
    
    # Process aliquoting events
    processed_aliquoting_events = []
    for event in all_aliquoting_events:
        processed_event = AliquotingEventResponse(
            id=event.id,
            parent_vial_id=event.parent_vial_id,
            child_vial_id=event.child_vial_id,
            operator_id=event.operator_id,
            event_timestamp=event.event_timestamp,
            volume_ul=event.volume_ul,
            notes=event.notes
        )
        processed_aliquoting_events.append(processed_event)
    
    # Process transfers
    processed_transfers = []
    for transfer in transfers:
        processed_transfer = TransferResponse(
            id=transfer.id,
            vial_id=transfer.vial_id,
            from_storage_unit_id=transfer.from_storage_unit_id,
            to_storage_unit_id=transfer.to_storage_unit_id,
            operator_id=transfer.operator_id,
            transfer_timestamp=transfer.transfer_timestamp,
            reason=transfer.reason
        )
        processed_transfers.append(processed_transfer)
    
    # Process QC tests
    processed_qc_tests = []
    for test in qc_tests:
        processed_test = QCTestResponse(
            id=test.id,
            vial_id=test.vial_id,
            test_type=test.test_type,
            result_value=test.result_value,
            result_text=test.result_text,
            result_status=test.result_status,
            operator_id=test.operator_id,
            test_timestamp=test.test_timestamp,
            notes=test.notes
        )
        processed_qc_tests.append(processed_test)
    
    # Process consents
    processed_consents = []
    for consent in consents:
        processed_consent = ConsentResponse(
            id=consent.id,
            vial_id=consent.vial_id,
            restriction_code=consent.restriction_code,
            restriction_description=consent.restriction_description,
            effective_from=consent.effective_from,
            effective_until=consent.effective_until
        )
        processed_consents.append(processed_consent)
    
    return CustodyHistoryResponse(
        vial_info=vial_info,
        aliquoting_events=processed_aliquoting_events,
        transfers=processed_transfers,
        qc_tests=processed_qc_tests,
        consents=processed_consents
    )
