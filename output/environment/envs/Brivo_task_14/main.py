import os
from datetime import datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class ContractorCreate(BaseModel):
    first_name: str
    last_name: str
    company_name: str
    email: str | None = None
    phone: str | None = None


class ContractorResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    company_name: str
    email: str | None = None
    phone: str | None = None


class AccessGroupAssign(BaseModel):
    access_group_id: int


class CredentialIssue(BaseModel):
    contractor_id: int


class CredentialResponse(BaseModel):
    id: int
    contractor_id: int
    credential_type: str
    status: str


class VisitorInvitationCreate(BaseModel):
    visitor_name: str
    host_user_id: int
    site_id: int
    scheduled_start: str
    scheduled_end: str | None = None
    notes: str | None = None


class VisitorInvitationResponse(BaseModel):
    id: int
    visitor_name: str
    host_user_id: int
    site_id: int
    scheduled_start: str
    scheduled_end: str | None = None
    status: str


def _parse_datetime(value: str) -> datetime:
    cleaned = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise HTTPException(status_code=422, detail=f"Invalid datetime format: {value}")


@app.post("/contractors", response_model=ContractorResponse, operation_id="create_contractor")
def create_contractor(payload: ContractorCreate):
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                INSERT INTO contractors (first_name, last_name, company_name, email, phone)
                VALUES (:first_name, :last_name, :company_name, :email, :phone)
                """
            ),
            {
                "first_name": payload.first_name,
                "last_name": payload.last_name,
                "company_name": payload.company_name,
                "email": payload.email,
                "phone": payload.phone,
            },
        )
        contractor_id = result.lastrowid
        row = conn.execute(
            text(
                """
                SELECT id, first_name, last_name, company_name, email, phone
                FROM contractors
                WHERE id = :id
                """
            ),
            {"id": contractor_id},
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=500, detail="Failed to retrieve created contractor")

    return ContractorResponse(
        id=row.id,
        first_name=row.first_name,
        last_name=row.last_name,
        company_name=row.company_name,
        email=row.email,
        phone=row.phone,
    )


@app.post(
    "/contractors/{contractor_id}/assign-access-group",
    response_model=dict,
    operation_id="assign_contractor_to_access_group",
)
def assign_contractor_to_access_group(contractor_id: int, payload: AccessGroupAssign):
    with engine.begin() as conn:
        contractor_exists = conn.execute(
            text("SELECT 1 FROM contractors WHERE id = :id"),
            {"id": contractor_id},
        ).fetchone()
        if contractor_exists is None:
            raise HTTPException(status_code=404, detail="Contractor not found")

        group_exists = conn.execute(
            text("SELECT 1 FROM access_groups WHERE id = :id"),
            {"id": payload.access_group_id},
        ).fetchone()
        if group_exists is None:
            raise HTTPException(status_code=404, detail="Access group not found")

        conn.execute(
            text(
                """
                INSERT INTO contractor_access_groups (contractor_id, access_group_id)
                VALUES (:contractor_id, :access_group_id)
                """
            ),
            {"contractor_id": contractor_id, "access_group_id": payload.access_group_id},
        )

    return {"contractor_id": contractor_id, "access_group_id": payload.access_group_id}


@app.post("/credentials", response_model=CredentialResponse, operation_id="issue_mobile_credential")
def issue_mobile_credential(payload: CredentialIssue):
    with engine.begin() as conn:
        contractor_exists = conn.execute(
            text("SELECT 1 FROM contractors WHERE id = :id"),
            {"id": payload.contractor_id},
        ).fetchone()
        if contractor_exists is None:
            raise HTTPException(status_code=404, detail="Contractor not found")

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        expires_at = datetime.utcnow().replace(year=datetime.utcnow().year + 1).strftime("%Y-%m-%d %H:%M:%S")

        result = conn.execute(
            text(
                """
                INSERT INTO credentials (contractor_id, credential_type, status, issued_at, expires_at)
                VALUES (:contractor_id, 'mobile', 'active', :issued_at, :expires_at)
                """
            ),
            {
                "contractor_id": payload.contractor_id,
                "issued_at": now,
                "expires_at": expires_at,
            },
        )
        credential_id = result.lastrowid
        row = conn.execute(
            text(
                """
                SELECT id, contractor_id, credential_type, status
                FROM credentials
                WHERE id = :id
                """
            ),
            {"id": credential_id},
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=500, detail="Failed to retrieve created credential")

    return CredentialResponse(
        id=row.id,
        contractor_id=row.contractor_id,
        credential_type=row.credential_type,
        status=row.status,
    )


@app.post(
    "/visitor-invitations",
    response_model=VisitorInvitationResponse,
    operation_id="create_visitor_invitation",
)
def create_visitor_invitation(payload: VisitorInvitationCreate):
    start_dt = _parse_datetime(payload.scheduled_start)
    end_dt = _parse_datetime(payload.scheduled_end) if payload.scheduled_end else None

    with engine.begin() as conn:
        host_exists = conn.execute(
            text("SELECT 1 FROM users WHERE id = :id"),
            {"id": payload.host_user_id},
        ).fetchone()
        if host_exists is None:
            raise HTTPException(status_code=404, detail="Host user not found")

        site_exists = conn.execute(
            text("SELECT 1 FROM sites WHERE id = :id"),
            {"id": payload.site_id},
        ).fetchone()
        if site_exists is None:
            raise HTTPException(status_code=404, detail="Site not found")

        result = conn.execute(
            text(
                """
                INSERT INTO visitor_invitations (
                    visitor_name, host_user_id, site_id, scheduled_start, scheduled_end, status, notes
                )
                VALUES (
                    :visitor_name, :host_user_id, :site_id, :scheduled_start, :scheduled_end, 'pending', :notes
                )
                """
            ),
            {
                "visitor_name": payload.visitor_name,
                "host_user_id": payload.host_user_id,
                "site_id": payload.site_id,
                "scheduled_start": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "scheduled_end": end_dt.strftime("%Y-%m-%d %H:%M:%S") if end_dt else None,
                "notes": payload.notes,
            },
        )
        invitation_id = result.lastrowid
        row = conn.execute(
            text(
                """
                SELECT id, visitor_name, host_user_id, site_id, scheduled_start, scheduled_end, status
                FROM visitor_invitations
                WHERE id = :id
                """
            ),
            {"id": invitation_id},
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=500, detail="Failed to retrieve created visitor invitation")

    return VisitorInvitationResponse(
        id=row.id,
        visitor_name=row.visitor_name,
        host_user_id=row.host_user_id,
        site_id=row.site_id,
        scheduled_start=str(row.scheduled_start),
        scheduled_end=str(row.scheduled_end) if row.scheduled_end else None,
        status=row.status,
    )
