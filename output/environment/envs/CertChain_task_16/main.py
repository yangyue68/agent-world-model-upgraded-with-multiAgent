import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    ForeignKey,
    Text,
    create_engine,
    select,
    update,
    insert,
    func,
    case,
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH, echo=False)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()

app = FastAPI(title="CertChain Audit API", version="1.0.0")


# --- SQLAlchemy Models ---

class Certificant(Base):
    __tablename__ = "certificants"
    id = Column(Integer, primary_key=True)
    certificant_code = Column(String, unique=True, nullable=False)
    full_name = Column(String, nullable=False)
    email = Column(String)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class AuditSampleCertificant(Base):
    __tablename__ = "audit_sample_certificants"
    id = Column(Integer, primary_key=True)
    audit_sample_id = Column(Integer, ForeignKey("audit_samples.id", ondelete="CASCADE"), nullable=False)
    certificant_id = Column(Integer, ForeignKey("certificants.id", ondelete="CASCADE"), nullable=False)
    audit_status = Column(String, nullable=False, default="Pending")
    ceu_certificates_on_file = Column(Integer, nullable=False, default=0)
    status_updated_at = Column(DateTime)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class AuditStatusHistory(Base):
    __tablename__ = "audit_status_history"
    id = Column(Integer, primary_key=True)
    audit_sample_certificant_id = Column(Integer, ForeignKey("audit_sample_certificants.id", ondelete="CASCADE"), nullable=False)
    previous_status = Column(String)
    new_status = Column(String, nullable=False)
    changed_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    changed_at = Column(DateTime, default=func.now())
    notes = Column(Text)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    email = Column(String, unique=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


# --- Pydantic Schemas ---

class CertificantResponse(BaseModel):
    id: int
    certificant_code: str
    full_name: str
    audit_status: str
    ceu_certificates_on_file: int

    class Config:
        from_attributes = True

class MarkSubmittedResponse(BaseModel):
    message: str
    updated_certificant: CertificantResponse

    class Config:
        from_attributes = True


# --- Helper Functions ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user_id() -> int:
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User ID not provided in environment")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid User ID in environment")

def fetch_certificant_for_audit(db: Session, audit_sample_id: int, certificant_id: int) -> AuditSampleCertificant:
    stmt = select(AuditSampleCertificant).where(
        AuditSampleCertificant.audit_sample_id == audit_sample_id,
        AuditSampleCertificant.certificant_id == certificant_id
    )
    result = db.execute(stmt).scalar_one_or_none()
    if result is None:
        raise HTTPException(status_code=404, detail="Certificant not found in this audit sample")
    return result

def fetch_certificant_details(db: Session, certificant_id: int) -> Certificant:
    stmt = select(Certificant).where(Certificant.id == certificant_id)
    result = db.execute(stmt).scalar_one_or_none()
    if result is None:
        raise HTTPException(status_code=404, detail="Certificant not found")
    return result

def model_to_response(certificant: Certificant, asc: AuditSampleCertificant) -> CertificantResponse:
    return CertificantResponse(
        id=asc.certificant_id, # The spec implies returning the certificant's core info, but 'id' in response usually maps to the entity being described or the link. 
                              # Looking at the list endpoint, it returns items with 'id'. In the context of listing certificants FOR an audit sample, 
                              # the 'id' likely refers to the certificant's own ID or the ASC ID? 
                              # Let's look at the schema: "id": {"type": "integer"}. 
                              # Usually, in a list of certificants, 'id' is the certificant ID.
        certificant_code=certificant.certificant_code,
        full_name=certificant.full_name,
        audit_status=asc.audit_status,
        ceu_certificates_on_file=asc.ceu_certificates_on_file
    )


# --- Endpoints ---

@app.get("/audit-samples/{audit_sample_id}/certificants", operation_id="list_certificants_for_audit_sample")
def list_certificants_for_audit_sample(audit_sample_id: int = Path(..., ge=1)):
    """List certificants in an audit sample with their audit status and CEU certificate status"""
    db: Session = SessionLocal()
    try:
        # Fetch all ASC records for this audit sample
        stmt = select(AuditSampleCertificant).where(
            AuditSampleCertificant.audit_sample_id == audit_sample_id
        )
        asc_rows = db.execute(stmt).scalars().all()
        
        if not asc_rows:
            return []

        # Collect all certificant IDs to fetch details efficiently
        certificant_ids = [asc.certificant_id for asc in asc_rows]
        
        # Fetch certificant details
        certificant_stmt = select(Certificant).where(Certificant.id.in_(certificant_ids))
        certificant_map = {c.id: c for c in db.execute(certificant_stmt).scalars().all()}
        
        responses = []
        for asc in asc_rows:
            certificant = certificant_map.get(asc.certificant_id)
            if certificant:
                responses.append(model_to_response(certificant, asc))
        
        return responses
    finally:
        db.close()


@app.put("/audit-samples/{audit_sample_id}/certificants/{certificant_id}/mark-submitted", operation_id="mark_certificant_submitted")
def mark_certificant_submitted(
    audit_sample_id: int = Path(..., ge=1),
    certificant_id: int = Path(..., ge=1)
):
    """Mark a certificant's audit status as Submitted if CEU certificates are on file"""
    db: Session = SessionLocal()
    current_user_id = get_current_user_id()
    
    try:
        # 1. Get the ASC record
        asc = fetch_certificant_for_audit(db, audit_sample_id, certificant_id)
        
        # 2. Check if CEU certificates are on file
        if asc.ceu_certificates_on_file != 1:
            raise HTTPException(
                status_code=400, 
                detail="Cannot mark as Submitted: CEU certificates are not on file for this certificant."
            )
        
        # 3. Record history before updating
        history_stmt = insert(AuditStatusHistory).values(
            audit_sample_certificant_id=asc.id,
            previous_status=asc.audit_status,
            new_status="Submitted",
            changed_by_user_id=current_user_id,
            notes="CEU certificates confirmed on file."
        )
        db.execute(history_stmt)
        
        # 4. Update the audit status
        update_stmt = update(AuditSampleCertificant).where(
            AuditSampleCertificant.id == asc.id
        ).values(
            audit_status="Submitted",
            status_updated_at=func.now()
        )
        db.execute(update_stmt)
        
        db.commit()
        
        # 5. Fetch updated details for response
        certificant = fetch_certificant_details(db, certificant_id)
        
        # Refresh the ASC object to ensure we have the latest state if needed, though we know it's Submitted now
        # Re-fetching via the DB to be safe with ORM state
        updated_asc = fetch_certificant_for_audit(db, audit_sample_id, certificant_id)
        
        response_data = model_to_response(certificant, updated_asc)
        
        return MarkSubmittedResponse(
            message="Audit status successfully updated to Submitted.",
            updated_certificant=response_data
        )
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
