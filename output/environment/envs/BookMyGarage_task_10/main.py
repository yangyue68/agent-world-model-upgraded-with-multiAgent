import os
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, Text, ForeignKey, CheckConstraint, case
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from fastapi import FastAPI, HTTPException, Query

DATABASE_PATH = os.environ.get("DATABASE_PATH")

Base = declarative_base()

# Define models based on the schema
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    full_name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    phone = Column(String)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

class Vehicle(Base):
    __tablename__ = 'vehicles'
    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    vin = Column(String, unique=True, nullable=False)
    make = Column(String)
    model = Column(String)
    year = Column(Integer)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

class Shop(Base):
    __tablename__ = 'shops'
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    address = Column(String)
    phone = Column(String)
    email = Column(String)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

class Service(Base):
    __tablename__ = 'services'
    id = Column(Integer, primary_key=True)
    shop_id = Column(Integer, ForeignKey('shops.id'), nullable=False)
    name = Column(String, nullable=False)
    description = Column(String)
    base_price = Column(Float, nullable=False)
    duration_minutes = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    CheckConstraint("base_price >= 0")
    CheckConstraint("duration_minutes > 0")

class Appointment(Base):
    __tablename__ = 'appointments'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    vehicle_id = Column(Integer, ForeignKey('vehicles.id'), nullable=False)
    shop_id = Column(Integer, ForeignKey('shops.id'), nullable=False)
    service_id = Column(Integer, ForeignKey('services.id'), nullable=False)
    scheduled_at = Column(DateTime, nullable=False)
    status = Column(String, nullable=False, default='upcoming')
    refund_amount = Column(Float)
    refund_status = Column(String, nullable=False, default='none')
    cancelled_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    CheckConstraint("status IN ('upcoming', 'completed', 'cancelled', 'no_show')")
    CheckConstraint("refund_status IN ('none', 'pending', 'processed', 'failed')")
    CheckConstraint("refund_amount >= 0")

class Waitlist(Base):
    __tablename__ = 'waitlist'
    id = Column(Integer, primary_key=True)
    shop_id = Column(Integer, ForeignKey('shops.id'), nullable=False)
    service_id = Column(Integer, ForeignKey('services.id'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    vehicle_id = Column(Integer, ForeignKey('vehicles.id'))
    desired_date = Column(String)
    status = Column(String, nullable=False, default='waiting')
    promoted_appointment_id = Column(Integer, ForeignKey('appointments.id'))
    promoted_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    CheckConstraint("status IN ('waiting', 'promoted', 'expired', 'cancelled')")

class EmailNotification(Base):
    __tablename__ = 'email_notifications'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    appointment_id = Column(Integer, ForeignKey('appointments.id'))
    waitlist_id = Column(Integer, ForeignKey('waitlist.id'))
    email_type = Column(String, nullable=False)
    recipient_email = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    body = Column(Text)
    status = Column(String, nullable=False, default='pending')
    sent_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    CheckConstraint("email_type IN ('cancellation', 'waitlist_promotion', 'refund', 'reminder')")
    CheckConstraint("status IN ('pending', 'sent', 'failed')")

# Create the engine and session
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# FastAPI app
app = FastAPI()

# Pydantic models for responses
class CancelledAppointment(BaseModel):
    id: int
    refund_amount: float
    refund_status: str
    shop_name: str
    service_name: str
    scheduled_at: datetime

class PromotedWaitlistEntry(BaseModel):
    id: int
    user_id: int
    promoted_appointment_id: int

class EmailNotificationSent(BaseModel):
    id: int
    user_id: int
    email_type: str
    recipient_email: str

class CancelAppointmentsResponse(BaseModel):
    cancelled_appointments: List[CancelledAppointment]
    promoted_waitlist_entries: List[PromotedWaitlistEntry]
    email_notifications_sent: List[EmailNotificationSent]

# Helper function to get current user ID
def get_current_user_id():
    return int(os.environ.get("AWM_CURRENT_USER_ID", 0))

# Endpoint implementation
@app.post("/cancel-appointments-by-vin", response_model=CancelAppointmentsResponse, operation_id="cancel_appointments_by_vin")
def cancel_appointments_by_vin(vin: str = Query(..., description="Vehicle Identification Number of the vehicle whose appointments need to be cancelled")):
    db = SessionLocal()
    try:
        # Get vehicle by VIN
        vehicle = db.query(Vehicle).filter(Vehicle.vin == vin).first()
        if not vehicle:
            raise HTTPException(status_code=404, detail="Vehicle not found")
        
        # Get all upcoming appointments for this vehicle
        appointments = db.query(Appointment).filter(
            Appointment.vehicle_id == vehicle.id,
            Appointment.status == 'upcoming'
        ).all()
        
        cancelled_appointments = []
        promoted_waitlist_entries = []
        email_notifications_sent = []
        
        # Process each appointment
        for appointment in appointments:
            # Update appointment status and set cancelled_at
            appointment.status = 'cancelled'
            appointment.cancelled_at = datetime.utcnow()
            
            # Set refund details
            service = db.query(Service).filter(Service.id == appointment.service_id).first()
            if service:
                appointment.refund_amount = service.base_price
                appointment.refund_status = 'pending'
            
            # Record cancelled appointment
            shop = db.query(Shop).filter(Shop.id == appointment.shop_id).first()
            service = db.query(Service).filter(Service.id == appointment.service_id).first()
            
            cancelled_appointments.append(CancelledAppointment(
                id=appointment.id,
                refund_amount=appointment.refund_amount or 0.0,
                refund_status=appointment.refund_status,
                shop_name=shop.name if shop else "",
                service_name=service.name if service else "",
                scheduled_at=appointment.scheduled_at
            ))
            
            # Find waitlist entries for this service and shop
            waitlist_entries = db.query(Waitlist).filter(
                Waitlist.shop_id == appointment.shop_id,
                Waitlist.service_id == appointment.service_id,
                Waitlist.status == 'waiting'
            ).order_by(Waitlist.created_at).all()
            
            # Promote first waitlisted user to this appointment
            if waitlist_entries:
                waitlist_entry = waitlist_entries[0]
                
                # Update waitlist entry to promoted
                waitlist_entry.status = 'promoted'
                waitlist_entry.promoted_appointment_id = appointment.id
                waitlist_entry.promoted_at = datetime.utcnow()
                
                # Create email notification for promotion
                user = db.query(User).filter(User.id == waitlist_entry.user_id).first()
                if user:
                    email_notification = EmailNotification(
                        user_id=user.id,
                        waitlist_id=waitlist_entry.id,
                        email_type='waitlist_promotion',
                        recipient_email=user.email,
                        subject='Your Waitlist Entry Has Been Promoted',
                        body=f'You have been promoted to an appointment for {service.name} at {shop.name}.',
                        status='pending'
                    )
                    db.add(email_notification)
                    db.flush()  # Ensure the email notification gets an ID before we use it
                    email_notifications_sent.append(EmailNotificationSent(
                        id=email_notification.id,
                        user_id=user.id,
                        email_type=email_notification.email_type,
                        recipient_email=user.email
                    ))
                
                promoted_waitlist_entries.append(PromotedWaitlistEntry(
                    id=waitlist_entry.id,
                    user_id=waitlist_entry.user_id,
                    promoted_appointment_id=appointment.id
                ))
            
            # Create email notification for cancellation
            user = db.query(User).filter(User.id == appointment.user_id).first()
            if user:
                email_notification = EmailNotification(
                    user_id=user.id,
                    appointment_id=appointment.id,
                    email_type='cancellation',
                    recipient_email=user.email,
                    subject='Appointment Cancelled',
                    body=f'Your appointment for {service.name} at {shop.name} has been cancelled.',
                    status='pending'
                )
                db.add(email_notification)
                db.flush()  # Ensure the email notification gets an ID before we use it
                email_notifications_sent.append(EmailNotificationSent(
                    id=email_notification.id,
                    user_id=user.id,
                    email_type=email_notification.email_type,
                    recipient_email=user.email
                ))
                
                # Create refund email notification
                if service:
                    refund_notification = EmailNotification(
                        user_id=user.id,
                        appointment_id=appointment.id,
                        email_type='refund',
                        recipient_email=user.email,
                        subject='Refund Processed',
                        body=f'A refund of ${appointment.refund_amount} has been processed for your cancelled appointment.',
                        status='pending'
                    )
                    db.add(refund_notification)
                    db.flush()  # Ensure the email notification gets an ID before we use it
                    email_notifications_sent.append(EmailNotificationSent(
                        id=refund_notification.id,
                        user_id=user.id,
                        email_type=refund_notification.email_type,
                        recipient_email=user.email
                    ))
        
        db.commit()
        return CancelAppointmentsResponse(
            cancelled_appointments=cancelled_appointments,
            promoted_waitlist_entries=promoted_waitlist_entries,
            email_notifications_sent=email_notifications_sent
        )
    
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
