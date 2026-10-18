import os
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    Text,
    create_engine,
    select,
    update,
    insert,
    case,
)
from sqlalchemy.orm import declarative_base, sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

app = FastAPI(title="CarePulse API", version="1.0.0")


# --- SQLAlchemy Models for Schema Reflection/Usage ---

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    full_name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    phone = Column(String)
    role = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class Appointment(Base):
    __tablename__ = "appointments"
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, nullable=False)
    provider_id = Column(Integer, nullable=False)
    scheduled_at = Column(DateTime, nullable=False)
    duration_minutes = Column(Integer, default=30)
    status = Column(String, default="requested")
    reason = Column(Text)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True)
    sender_id = Column(Integer, nullable=False)
    recipient_id = Column(Integer, nullable=False)
    appointment_id = Column(Integer)
    subject = Column(Text)
    body = Column(Text, nullable=False)
    sent_at = Column(DateTime, default=datetime.utcnow)
    read_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class ProviderAvailability(Base):
    __tablename__ = "provider_availability"
    id = Column(Integer, primary_key=True)
    provider_id = Column(Integer, nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    is_booked = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


# --- Pydantic Schemas ---

class AppointmentResponse(BaseModel):
    id: int
    patient_id: int
    provider_id: int
    scheduled_at: str  # ISO format string
    duration_minutes: int
    status: str
    reason: Optional[str] = None
    notes: Optional[str] = None

    class Config:
        from_attributes = True


class DeclineAppointmentRequest(BaseModel):
    reason: str


class DeclineAppointmentResponse(BaseModel):
    id: int
    status: str
    reason: str


class SendMessageRequest(BaseModel):
    sender_id: int
    recipient_id: int
    appointment_id: Optional[int] = None
    subject: str
    body: str


class SendMessageResponse(BaseModel):
    id: int
    sender_id: int
    recipient_id: int
    appointment_id: Optional[int] = None
    subject: str
    body: str
    sent_at: str

    class Config:
        from_attributes = True


class AvailabilitySlot(BaseModel):
    id: int
    provider_id: int
    start_time: str
    end_time: str

    class Config:
        from_attributes = True


# --- Helper Functions ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def parse_datetime(dt_str: str) -> datetime:
    """Parse ISO-8601 datetime string, handling both 'T' and space separators."""
    dt_str = dt_str.replace("Z", "+00:00").replace(" ", "T")
    # Simple parsing for standard formats without timezone offsets for simplicity in this context
    # Assuming UTC or naive datetimes stored as such.
    try:
        return datetime.fromisoformat(dt_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid datetime format")


def get_tomorrow_range():
    """Get the start and end of tomorrow's date range."""
    now = datetime.utcnow()
    tomorrow_start = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    tomorrow_end = tomorrow_start + timedelta(days=1)
    return tomorrow_start, tomorrow_end


# --- Endpoints ---

@app.get("/appointments/patient/{patient_name}/upcoming", 
         operation_id="find_upcoming_appointments_by_patient",
         response_model=List[AppointmentResponse],
         tags=["PrimaryResource"])
def find_upcoming_appointments_by_patient(patient_name: str):
    """Find upcoming appointments for a specific patient"""
    db = SessionLocal()
    try:
        tomorrow_start, tomorrow_end = get_tomorrow_range()
        
        stmt = (
            select(Appointment)
            .join(User, Appointment.patient_id == User.id)
            .where(
                User.full_name == patient_name,
                Appointment.scheduled_at >= tomorrow_start,
                Appointment.scheduled_at < tomorrow_end,
                Appointment.status == "requested"
            )
        )
        
        results = db.execute(stmt).scalars().all()
        return results
    finally:
        db.close()


@app.get("/appointments/find-by-patient-and-time", 
         operation_id="find_appointment_by_patient_and_time",
         response_model=dict,
         tags=["PrimaryResource"])
def find_appointment_by_patient_and_time(
    patient_name: str = Query(..., description="Full name of the patient"),
    scheduled_at: str = Query(..., description="Scheduled datetime of the appointment (ISO 8601 format)")
):
    """Find appointment ID by patient name and scheduled time"""
    db = SessionLocal()
    try:
        target_dt = parse_datetime(scheduled_at)
        
        stmt = (
            select(Appointment.id)
            .join(User, Appointment.patient_id == User.id)
            .where(
                User.full_name == patient_name,
                Appointment.scheduled_at == target_dt
            )
        )
        
        result = db.execute(stmt).scalar_one_or_none()
        
        if result is None:
            raise HTTPException(status_code=404, detail="Appointment not found")
            
        return {"id": result}
    finally:
        db.close()


@app.put("/appointments/{appointment_id}/decline", 
         operation_id="decline_appointment",
         response_model=DeclineAppointmentResponse,
         tags=["PrimaryResource"])
def decline_appointment(appointment_id: int, request: DeclineAppointmentRequest):
    """Decline an appointment request"""
    db = SessionLocal()
    try:
        # Get current user ID from environment
        current_user_id = os.environ.get("AWM_CURRENT_USER_ID")
        if not current_user_id:
            raise HTTPException(status_code=500, detail="User identity not available")
        
        # Fetch appointment
        stmt = select(Appointment).where(Appointment.id == appointment_id)
        appointment = db.execute(stmt).scalar_one_or_none()
        
        if not appointment:
            raise HTTPException(status_code=404, detail="Appointment not found")
        
        if appointment.status != "requested":
            raise HTTPException(status_code=400, detail="Only requested appointments can be declined")
        
        # Update appointment status
        update_stmt = (
            update(Appointment)
            .where(Appointment.id == appointment_id)
            .values(
                status="declined",
                reason=request.reason,
                updated_at=datetime.utcnow()
            )
        )
        db.execute(update_stmt)
        db.commit()
        
        # Refresh to get updated values
        updated_appointment = db.execute(select(Appointment).where(Appointment.id == appointment_id)).scalar_one()
        
        return {
            "id": updated_appointment.id,
            "status": updated_appointment.status,
            "reason": updated_appointment.reason
        }
    finally:
        db.close()


@app.post("/messages/send", 
          operation_id="send_message",
          response_model=SendMessageResponse,
          tags=["PrimaryResource"])
def send_message(request: SendMessageRequest):
    """Send a message to a patient"""
    db = SessionLocal()
    try:
        # Validate sender and recipient exist
        user_stmt = select(User).where(User.id.in_([request.sender_id, request.recipient_id]))
        users = db.execute(user_stmt).scalars().all()
        
        if len(users) != 2:
            raise HTTPException(status_code=404, detail="Sender or Recipient not found")
            
        # Create message
        new_message = Message(
            sender_id=request.sender_id,
            recipient_id=request.recipient_id,
            appointment_id=request.appointment_id,
            subject=request.subject,
            body=request.body,
            sent_at=datetime.utcnow()
        )
        
        db.add(new_message)
        db.commit()
        db.refresh(new_message)
        
        return {
            "id": new_message.id,
            "sender_id": new_message.sender_id,
            "recipient_id": new_message.recipient_id,
            "appointment_id": new_message.appointment_id,
            "subject": new_message.subject,
            "body": new_message.body,
            "sent_at": new_message.sent_at.isoformat()
        }
    finally:
        db.close()


@app.get("/providers/{provider_id}/availability/tomorrow", 
         operation_id="get_tomorrows_available_slots",
         response_model=List[AvailabilitySlot],
         tags=["PrimaryResource"])
def get_tomorrows_available_slots(provider_id: int):
    """Get available time slots for a provider tomorrow"""
    db = SessionLocal()
    try:
        tomorrow_start, tomorrow_end = get_tomorrow_range()
        
        stmt = (
            select(ProviderAvailability)
            .where(
                ProviderAvailability.provider_id == provider_id,
                ProviderAvailability.start_time >= tomorrow_start,
                ProviderAvailability.start_time < tomorrow_end,
                ProviderAvailability.is_booked == 0
            )
            .order_by(ProviderAvailability.start_time)
        )
        
        results = db.execute(stmt).scalars().all()
        return results
    finally:
        db.close()
