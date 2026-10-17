import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class VerificationResponse(BaseModel):
    verification_url: str
    candidate_name: str
    certification_title: str
    issuing_organization: str


@app.post("/badges/{badge_id}/verification-url", response_model=VerificationResponse, operation_id="generate_verification_url")
def generate_verification_url(badge_id: str):
    with engine.connect() as conn:
        # Fetch badge details along with user, certification, and organization info
        query = text("""
            SELECT 
                b.badge_id,
                b.verification_url,
                u.full_name AS candidate_name,
                c.title AS certification_title,
                o.name AS issuing_organization
            FROM badges b
            JOIN users u ON b.user_id = u.id
            JOIN certifications c ON b.certification_id = c.id
            JOIN organizations o ON c.organization_id = o.id
            WHERE b.badge_id = :badge_id
        """)
        
        result = conn.execute(query, {"badge_id": badge_id}).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Badge not found")
        
        current_verification_url = result[1]
        candidate_name = result[2]
        certification_title = result[3]
        issuing_organization = result[4]
        
        # If verification URL already exists, return it
        if current_verification_url:
            return VerificationResponse(
                verification_url=current_verification_url,
                candidate_name=candidate_name,
                certification_title=certification_title,
                issuing_organization=issuing_organization
            )
        
        # Generate new verification URL
        new_verification_url = f"https://verify.certchain.example/badge/{badge_id}"
        
        # Update the database with the new verification URL
        update_query = text("""
            UPDATE badges 
            SET verification_url = :url, updated_at = CURRENT_TIMESTAMP
            WHERE badge_id = :badge_id
        """)
        
        conn.execute(update_query, {"url": new_verification_url, "badge_id": badge_id})
        conn.commit()
        
        return VerificationResponse(
            verification_url=new_verification_url,
            candidate_name=candidate_name,
            certification_title=certification_title,
            issuing_organization=issuing_organization
        )
