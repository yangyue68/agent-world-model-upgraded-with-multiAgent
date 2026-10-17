import os
import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    REAL,
    String,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    phone = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())


class Shop(Base):
    __tablename__ = "shops"
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    address = Column(String)
    phone = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())


class Vehicle(Base):
    __tablename__ = "vehicles"
    id = Column(Integer, primary_key=True)
    vin = Column(String, unique=True, nullable=False)
    make = Column(String, nullable=False)
    model = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())


class Service(Base):
    __tablename__ = "services"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(String)
    base_price = Column(REAL, nullable=False)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())


class Appointment(Base):
    __tablename__ = "appointments"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    shop_id = Column(String, ForeignKey("shops.id"), nullable=False)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False)
    service_id = Column(Integer, ForeignKey("services.id"), nullable=False)
    scheduled_at = Column(DateTime, nullable=False)
    status = Column(
        String, nullable=False, default="scheduled", server_default="scheduled"
    )
    price = Column(REAL, nullable=False)
    payment_method = Column(String, nullable=False)
    payment_status = Column(
        String,
        nullable=False,
        default="pending",
        server_default="pending",
    )
    refund_amount = Column(REAL, default=0.0)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())


class Waitlist(Base):
    __tablename__ = "waitlist"
    id = Column(Integer, primary_key=True)
    shop_id = Column(String, ForeignKey("shops.id"), nullable=False)
    service_id = Column(Integer, ForeignKey("services.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False)
    desired_date = Column(DateTime, nullable=False)
    status = Column(
        String,
        nullable=False,
        default="waiting",
        server_default="waiting",
    )
    promoted_appointment_id = Column(Integer, ForeignKey("appointments.id"))
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())


class Refund(Base):
    __tablename__ = "refunds"
    id = Column(Integer, primary_key=True)
    appointment_id = Column(Integer, ForeignKey("appointments.id"), nullable=False)
    amount = Column(REAL, nullable=False)
    method = Column(String, nullable=False)
    status = Column(
        String,
        nullable=False,
        default="pending",
        server_default="pending",
    )
    transaction_id = Column(String)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp())


app = FastAPI()


class AppointmentLookupResponse(BaseModel):
    appointment_id: int


class CancelAppointmentResponse(BaseModel):
    appointment_id: int
    status: str
    refund_processed: bool
    waitlist_promoted: bool


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/appointments/lookup", response_model=AppointmentLookupResponse, operation_id="find_appointment_by_criteria")
def find_appointment_by_criteria(
    shop_id: str,
    scheduled_at: str,
    vin: str,
    service_name: str,
    db=None,
):
    if db is None:
        db = next(get_db())

    # Parse the scheduled_at string to ensure it's in the correct format
    try:
        parsed_scheduled_at = datetime.fromisoformat(
            scheduled_at.replace(" ", "T")
        )
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid scheduled_at format. Expected ISO format.",
        )

    # Find the vehicle by VIN
    vehicle = db.execute(
        select(Vehicle).where(Vehicle.vin == vin)
    ).scalar_one_or_none()

    if not vehicle:
        raise HTTPException(
            status_code=404,
            detail="Vehicle not found.",
        )

    # Find the service by name
    service = db.execute(
        select(Service).where(Service.name == service_name)
    ).scalar_one_or_none()

    if not service:
        raise HTTPException(
            status_code=404,
            detail="Service not found.",
        )

    # Find the appointment
    appointment = db.execute(
        select(Appointment)
        .where(
            Appointment.shop_id == shop_id,
            Appointment.scheduled_at == parsed_scheduled_at,
            Appointment.vehicle_id == vehicle.id,
            Appointment.service_id == service.id,
        )
    ).scalar_one_or_none()

    if not appointment:
        raise HTTPException(
            status_code=404,
            detail="Appointment not found.",
        )

    return AppointmentLookupResponse(appointment_id=appointment.id)


@app.delete("/appointments/{appointment_id}", response_model=CancelAppointmentResponse, operation_id="cancel_appointment")
def cancel_appointment(
    appointment_id: int,
    db=None,
):
    if db is None:
        db = next(get_db())

    # Get current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))

    # Fetch the appointment
    appointment = db.execute(
        select(Appointment).where(Appointment.id == appointment_id)
    ).scalar_one_or_none()

    if not appointment:
        raise HTTPException(
            status_code=404,
            detail="Appointment not found.",
        )

    # Check if appointment is in scheduled status
    if appointment.status != "scheduled":
        raise HTTPException(
            status_code=400,
            detail="Appointment is not in scheduled status.",
        )

    # Check if the user has permission to cancel this appointment
    if appointment.user_id != current_user_id:
        raise HTTPException(
            status_code=403,
            detail="You don't have permission to cancel this appointment.",
        )

    # Begin transaction
    try:
        # Update appointment status to cancelled
        appointment.status = "cancelled"
        appointment.updated_at = func.current_timestamp()

        # Create refund record
        refund = Refund(
            appointment_id=appointment.id,
            amount=appointment.price,
            method=appointment.payment_method,
            status="pending",
        )
        db.add(refund)

        # Commit changes
        db.commit()

        # Check if there are waitlisted customers for this shop and service
        waitlist_entry = db.execute(
            select(Waitlist)
            .where(
                Waitlist.shop_id == appointment.shop_id,
                Waitlist.service_id == appointment.service_id,
                Waitlist.status == "waiting",
            )
            .order_by(Waitlist.created_at)
        ).first()

        waitlist_promoted = False
        if waitlist_entry:
            # Promote the first waitlisted customer
            waitlist_entry.status = "promoted"
            waitlist_entry.promoted_appointment_id = appointment.id
            waitlist_entry.updated_at = func.current_timestamp()

            # Create a new appointment for the promoted customer
            new_appointment = Appointment(
                user_id=waitlist_entry.user_id,
                shop_id=waitlist_entry.shop_id,
                vehicle_id=waitlist_entry.vehicle_id,
                service_id=waitlist_entry.service_id,
                scheduled_at=waitlist_entry.desired_date,
                status="scheduled",
                price=appointment.price,
                payment_method=appointment.payment_method,
                payment_status="pending",
            )
            db.add(new_appointment)

            # Commit changes
            db.commit()

            waitlist_promoted = True

        # Return response
        return CancelAppointmentResponse(
            appointment_id=appointment.id,
            status=appointment.status,
            refund_processed=True,
            waitlist_promoted=waitlist_promoted,
        )

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while cancelling the appointment: {str(e)}",
        )
