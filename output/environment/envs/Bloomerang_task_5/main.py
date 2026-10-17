import os
from datetime import datetime, date, timedelta
from typing import List, Literal
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Date, Float, ForeignKey, Text, func, case, and_, or_
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Donor(Base):
    __tablename__ = "donors"
    id = Column(Integer, primary_key=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String)
    phone = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Campaign(Base):
    __tablename__ = "campaigns"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text)
    start_date = Column(Date)
    end_date = Column(Date)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Gift(Base):
    __tablename__ = "gifts"
    id = Column(Integer, primary_key=True)
    donor_id = Column(Integer, ForeignKey("donors.id"), nullable=False)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"))
    amount = Column(Float, nullable=False)
    gift_date = Column(Date, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    donor = relationship("Donor")
    campaign = relationship("Campaign")

class Segment(Base):
    __tablename__ = "segments"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class DonorSegment(Base):
    __tablename__ = "donor_segments"
    id = Column(Integer, primary_key=True)
    donor_id = Column(Integer, ForeignKey("donors.id"), nullable=False)
    segment_id = Column(Integer, ForeignKey("segments.id"), nullable=False)
    assigned_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    donor = relationship("Donor")
    segment = relationship("Segment")

class EmailTemplate(Base):
    __tablename__ = "email_templates"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    subject = Column(String)
    body = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class EmailSequence(Base):
    __tablename__ = "email_sequences"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    template_id = Column(Integer, ForeignKey("email_templates.id"), nullable=False)
    start_date = Column(Date, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    template = relationship("EmailTemplate")

class ScheduledEmail(Base):
    __tablename__ = "scheduled_emails"
    id = Column(Integer, primary_key=True)
    sequence_id = Column(Integer, ForeignKey("email_sequences.id"), nullable=False)
    donor_id = Column(Integer, ForeignKey("donors.id"), nullable=False)
    scheduled_date = Column(Date, nullable=False)
    status = Column(String, default="pending")
    sent_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    donor = relationship("Donor")
    sequence = relationship("EmailSequence")

# Request/Response Models
class DonorResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    email: str

class AssignSegmentRequest(BaseModel):
    donor_ids: List[int]

class AssignSegmentResponse(BaseModel):
    message: str
    assigned_count: int

class ScheduleEmailSequenceRequest(BaseModel):
    donor_ids: List[int]
    start_date: date

class ScheduleEmailSequenceResponse(BaseModel):
    message: str
    scheduled_count: int

app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/lapsed-donors", response_model=List[DonorResponse], operation_id="find_lapsed_donors")
def find_lapsed_donors(db=Depends(get_db)):
    # Find donors who gave $1000+ to Capital Campaign and haven't given in last 24 months
    
    # Get the Capital Campaign ID
    capital_campaign = db.query(Campaign).filter(Campaign.name == "Capital Campaign").first()
    if not capital_campaign:
        raise HTTPException(status_code=404, detail="Capital Campaign not found")
    
    # Calculate cutoff date (24 months ago)
    cutoff_date = datetime.now().date() - timedelta(days=24*30)
    
    # Find donors who gave $1000+ to Capital Campaign
    major_donors = db.query(Gift.donor_id).filter(
        and_(
            Gift.campaign_id == capital_campaign.id,
            Gift.amount >= 1000.0
        )
    ).distinct().subquery()
    
    # Find donors who haven't given in last 24 months
    recent_gifts = db.query(Gift.donor_id).filter(
        Gift.gift_date >= cutoff_date
    ).distinct().subquery()
    
    # Get donors who are major donors but haven't given recently
    lapsed_donors = db.query(Donor).filter(
        and_(
            Donor.id.in_(major_donors),
            ~Donor.id.in_(recent_gifts)
        )
    ).all()
    
    return [
        DonorResponse(
            id=donor.id,
            first_name=donor.first_name,
            last_name=donor.last_name,
            email=donor.email
        ) for donor in lapsed_donors
    ]

@app.post("/assign-segment", response_model=AssignSegmentResponse, operation_id="assign_reengagement_segment")
def assign_reengagement_segment(request: AssignSegmentRequest, db=Depends(get_db)):
    # Get the Re-engagement Q1 2025 segment ID
    segment = db.query(Segment).filter(Segment.name == "Re-engagement Q1 2025").first()
    if not segment:
        raise HTTPException(status_code=404, detail="Re-engagement Q1 2025 segment not found")
    
    # Check if all donors exist
    existing_donors = db.query(Donor.id).filter(Donor.id.in_(request.donor_ids)).all()
    existing_ids = {d.id for d in existing_donors}
    
    missing_donors = set(request.donor_ids) - existing_ids
    if missing_donors:
        raise HTTPException(status_code=400, detail=f"Invalid donor IDs: {missing_donors}")
    
    # Create donor_segment entries
    donor_segments = []
    for donor_id in request.donor_ids:
        donor_segment = DonorSegment(
            donor_id=donor_id,
            segment_id=segment.id
        )
        donor_segments.append(donor_segment)
    
    db.add_all(donor_segments)
    db.commit()
    
    return AssignSegmentResponse(
        message=f"Successfully assigned {len(request.donor_ids)} donors to Re-engagement Q1 2025 segment",
        assigned_count=len(request.donor_ids)
    )

@app.post("/schedule-email-sequence", response_model=ScheduleEmailSequenceResponse, operation_id="schedule_lapsed_donor_emails")
def schedule_lapsed_donor_emails(request: ScheduleEmailSequenceRequest, db=Depends(get_db)):
    # Get the email template
    template = db.query(EmailTemplate).filter(EmailTemplate.name == "Lapsed_Major_Donor_Reminder").first()
    if not template:
        raise HTTPException(status_code=404, detail="Lapsed_Major_Donor_Reminder template not found")
    
    # Get the Re-engagement Q1 2025 segment ID
    segment = db.query(Segment).filter(Segment.name == "Re-engagement Q1 2025").first()
    if not segment:
        raise HTTPException(status_code=404, detail="Re-engagement Q1 2025 segment not found")
    
    # Check if all donors exist and belong to the segment
    donor_ids_in_segment = db.query(DonorSegment.donor_id).filter(
        DonorSegment.segment_id == segment.id
    ).subquery()
    
    existing_donors = db.query(Donor.id).filter(
        and_(
            Donor.id.in_(request.donor_ids),
            Donor.id.in_(donor_ids_in_segment)
        )
    ).all()
    
    existing_ids = {d.id for d in existing_donors}
    missing_donors = set(request.donor_ids) - existing_ids
    if missing_donors:
        raise HTTPException(status_code=400, detail=f"Invalid donor IDs or donors not in Re-engagement Q1 2025 segment: {missing_donors}")
    
    # Create email sequence
    sequence = EmailSequence(
        name="Lapsed Major Donor Reminder Sequence",
        template_id=template.id,
        start_date=request.start_date
    )
    db.add(sequence)
    db.flush()  # Get the sequence ID
    
    # Create scheduled emails
    scheduled_emails = []
    for donor_id in request.donor_ids:
        scheduled_email = ScheduledEmail(
            sequence_id=sequence.id,
            donor_id=donor_id,
            scheduled_date=request.start_date
        )
        scheduled_emails.append(scheduled_email)
    
    db.add_all(scheduled_emails)
    db.commit()
    
    return ScheduleEmailSequenceResponse(
        message=f"Successfully scheduled email sequence for {len(request.donor_ids)} donors",
        scheduled_count=len(request.donor_ids)
    )
