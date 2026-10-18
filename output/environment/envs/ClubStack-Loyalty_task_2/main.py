import os
from typing import Optional, List

from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from contextlib import asynccontextmanager

# Database configuration
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

# Pydantic Models for Response Structure

class MemberResponse(BaseModel):
    id: int
    email: str
    first_name: str
    last_name: str
    phone: Optional[str] = None
    date_of_birth: Optional[str] = None
    created_at: str
    updated_at: str

class ProfileResponse(BaseModel):
    id: int
    member_id: int
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    country: Optional[str] = None
    loyalty_points: int
    membership_tier: str
    created_at: str
    updated_at: str

class SubscriptionResponse(BaseModel):
    id: int
    member_id: int
    plan_name: str
    status: str
    start_date: str
    end_date: Optional[str] = None
    price: float
    billing_cycle: str
    auto_renew: int
    created_at: str
    updated_at: str

class MemberProfileSubscriptionResponse(BaseModel):
    member: MemberResponse
    profile: ProfileResponse
    subscription: SubscriptionResponse

app = FastAPI(title="ClubStack Loyalty API")

@app.get("/members/profile", response_model=MemberProfileSubscriptionResponse, operation_id="get_member_profile")
def get_member_profile(
    email: str = Query(..., description="Email address of the member whose profile needs to be retrieved")
):
    """
    Retrieve member profile and subscription details by email.
    Fetches the complete profile and active subscription details for a member identified by their email address.
    """
    
    # We need to fetch data from three tables: members, member_profiles, subscriptions.
    # The requirement implies fetching the *current* or relevant subscription. 
    # Looking at the schema, there isn't an explicit "is_current" flag on subscriptions other than status.
    # However, typically "subscription details" in such contexts refers to the most recent or active one.
    # Let's assume we want the latest subscription associated with the member based on ID or Start Date if multiple exist?
    # Or simply join them. Since a member might have multiple historical subscriptions, but usually APIs like this return the primary/current one.
    # Given the example data has 1-to-1 mapping effectively for the purpose of the task (John Doe has sub ID 1), 
    # I will select the subscription with the highest ID (most recently created) or just any if unique per logic.
    # To be safe and robust against multiple subs, let's pick the one with status 'active' first, otherwise the latest by ID.
    # Actually, simpler approach: Just join all three. If there are multiple subs, which one do we take?
    # The prompt says "current subscription data". 
    # Let's try to find the active subscription. If none, maybe the latest cancelled/expired?
    # For simplicity and matching typical REST behavior where resource ownership is clear, 
    # I'll query the member, then their profile, then their subscription.
    # If there are multiple subscriptions, I should probably return the most relevant one.
    # Let's stick to the single row expectation implied by the response model structure (not a list).
    # I will order by status priority ('active' > others) and then by ID desc to get the best candidate.
    
    sql_query = """
        SELECT 
            m.id as member_id,
            m.email,
            m.first_name,
            m.last_name,
            m.phone,
            m.date_of_birth,
            m.created_at as member_created_at,
            m.updated_at as member_updated_at,
            
            mp.id as profile_id,
            mp.member_id as profile_member_id,
            mp.address_line1,
            mp.address_line2,
            mp.city,
            mp.state,
            mp.postal_code,
            mp.country,
            mp.loyalty_points,
            mp.membership_tier,
            mp.created_at as profile_created_at,
            mp.updated_at as profile_updated_at,
            
            s.id as sub_id,
            s.member_id as sub_member_id,
            s.plan_name,
            s.status,
            s.start_date,
            s.end_date,
            s.price,
            s.billing_cycle,
            s.auto_renew,
            s.created_at as sub_created_at,
            s.updated_at as sub_updated_at
            
        FROM members m
        JOIN member_profiles mp ON m.id = mp.member_id
        LEFT JOIN subscriptions s ON m.id = s.member_id
        WHERE m.email = :email
        ORDER BY 
            CASE WHEN s.status = 'active' THEN 0 ELSE 1 END ASC,
            s.id DESC
        LIMIT 1;
    """
    
    with engine.connect() as conn:
        result = conn.execute(text(sql_query), {"email": email})
        row = result.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail=f"Member with email {email} not found.")
            
        # Map columns manually since SQLAlchemy Core returns tuples/Row objects
        # Row indices correspond to the SELECT statement order
        
        member_data = {
            "id": row[0],
            "email": row[1],
            "first_name": row[2],
            "last_name": row[3],
            "phone": row[4],
            "date_of_birth": row[5],
            "created_at": str(row[6]),
            "updated_at": str(row[7])
        }
        
        profile_data = {
            "id": row[8],
            "member_id": row[9],
            "address_line1": row[10],
            "address_line2": row[11],
            "city": row[12],
            "state": row[13],
            "postal_code": row[14],
            "country": row[15],
            "loyalty_points": row[16],
            "membership_tier": row[17],
            "created_at": str(row[18]),
            "updated_at": str(row[19])
        }
        
        # Handle case where no subscription exists (LEFT JOIN returned NULLs for sub fields)
        # Although the spec requires returning subscription object, if DB allows missing sub, 
        # we might need to handle it. But looking at initial snapshot, everyone seems to have one.
        # If row[20] is None, it means no subscription found.
        if row[20] is None:
             # Depending on strictness, we could error out or return null. 
             # The response model requires `subscription` field. 
             # If business logic dictates every member MUST have a subscription record even if inactive/cancelled,
             # then finding nothing here is weird. 
             # However, if we strictly follow SQL, and no match was found in LEFT JOIN part...
             # Let's assume valid data always has a subscription linked or we treat it as 404/not found scenario for full profile?
             # Better: Raise specific error or construct empty/default? 
             # Given "Required" in response schema, we must provide it. 
             # If the user exists but has NO subscription records at all, this endpoint technically fails to fulfill contract.
             # Let's check if we can default it or if it's an error. 
             # Usually, "View profile AND subscription" implies both must exist.
             raise HTTPException(status_code=404, detail="Subscription details not found for this member.")

        subscription_data = {
            "id": row[20],
            "member_id": row[21],
            "plan_name": row[22],
            "status": row[23],
            "start_date": row[24],
            "end_date": row[25],
            "price": row[26],
            "billing_cycle": row[27],
            "auto_renew": row[28],
            "created_at": str(row[29]),
            "updated_at": str(row[30])
        }

        return MemberProfileSubscriptionResponse(
            member=MemberResponse(**member_data),
            profile=ProfileResponse(**profile_data),
            subscription=SubscriptionResponse(**subscription_data)
        )
