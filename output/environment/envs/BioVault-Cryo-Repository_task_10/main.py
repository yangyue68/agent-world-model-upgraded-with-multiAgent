import os
from datetime import date, datetime
from typing import List, Optional

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is required")

engine = create_engine(DATABASE_PATH)

app = FastAPI(title="BioVault Cryo Repository API")


class SpecimenWithConsentRestriction(BaseModel):
    specimen_id: int
    specimen_code: str
    specimen_type: str
    collection_date: str
    consent_id: int
    consent_code: str
    withdrawal_date: str


class AccessViolationDetail(BaseModel):
    specimen_code: str
    access_type: str
    access_date: str


class SpecimenAccessHistoryResponse(BaseModel):
    violations_found: bool
    details: List[AccessViolationDetail] = []


@app.get(
    "/primary-resource/specimens-with-consent-restrictions",
    operation_id="find_specimens_with_consent_restrictions",
    response_model=List[SpecimenWithConsentRestriction],
    tags=["PrimaryResource"],
)
def find_specimens_with_consent_restrictions(
    donor_code: str = Query(..., description="Donor identifier in format DON-XXXXX"),
):
    """
    Find specimens linked to a donor with active IRB consent restrictions prohibiting commercial use.
    """
    query = text("""
        SELECT 
            s.id as specimen_id,
            s.specimen_code,
            s.specimen_type,
            s.collection_date,
            ic.id as consent_id,
            ic.consent_code,
            ic.withdrawal_date
        FROM specimens s
        JOIN donors d ON s.donor_id = d.id
        JOIN specimen_consents sc ON s.id = sc.specimen_id
        JOIN irb_consents ic ON sc.consent_id = ic.id
        WHERE d.donor_code = :donor_code
          AND ic.commercial_use_allowed = 0
          AND ic.status IN ('active', 'withdrawn')
          AND ic.withdrawal_date IS NOT NULL
    """)
    
    with engine.connect() as conn:
        result = conn.execute(query, {"donor_code": donor_code})
        rows = result.fetchall()
        
        items = []
        for row in rows:
            # Format dates as strings
            collection_date_str = row.collection_date.isoformat() if isinstance(row.collection_date, (date, datetime)) else str(row.collection_date)
            withdrawal_date_str = row.withdrawal_date.isoformat() if isinstance(row.withdrawal_date, (date, datetime)) else str(row.withdrawal_date)
            
            items.append(SpecimenWithConsentRestriction(
                specimen_id=row.specimen_id,
                specimen_code=row.specimen_code,
                specimen_type=row.specimen_type,
                collection_date=collection_date_str,
                consent_id=row.consent_id,
                consent_code=row.consent_code,
                withdrawal_date=withdrawal_date_str,
            ))
        
        return items


@app.get(
    "/primary-resource/specimen-access-history",
    operation_id="check_specimen_access_history",
    response_model=SpecimenAccessHistoryResponse,
    tags=["PrimaryResource"],
)
def check_specimen_access_history(
    donor_code: str = Query(..., description="Donor identifier in format DON-XXXXX"),
    withdrawal_date: str = Query(..., description="Consent withdrawal date in YYYY-MM-DD format"),
):
    """
    Verify no access or transfers occurred after consent withdrawal date.
    Returns true if no violations found, false otherwise.
    """
    try:
        wd_date = datetime.strptime(withdrawal_date, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"Invalid withdrawal_date format: {withdrawal_date}. Expected YYYY-MM-DD.")

    # Get all specimens for the donor that have consents with commercial_use_allowed=0 and status withdrawn/active with this withdrawal date
    specimens_query = text("""
        SELECT DISTINCT s.id, s.specimen_code
        FROM specimens s
        JOIN donors d ON s.donor_id = d.id
        JOIN specimen_consents sc ON s.id = sc.specimen_id
        JOIN irb_consents ic ON sc.consent_id = ic.id
        WHERE d.donor_code = :donor_code
          AND ic.commercial_use_allowed = 0
          AND ic.withdrawal_date = :withdrawal_date
    """)
    
    with engine.connect() as conn:
        spec_result = conn.execute(specimens_query, {"donor_code": donor_code, "withdrawal_date": wd_date})
        specimen_rows = spec_result.fetchall()
        
        if not specimen_rows:
            return SpecimenAccessHistoryResponse(violations_found=False, details=[])
        
        specimen_ids = [row.id for row in specimen_rows]
        specimen_codes_map = {row.id: row.specimen_code for row in specimen_rows}
        
        violations = []
        
        # Check access logs
        placeholders = ",".join([f":sid_{i}" for i in range(len(specimen_ids))])
        params = {f"sid_{i}": sid for i, sid in enumerate(specimen_ids)}
        params["wd_datetime"] = datetime.combine(wd_date, datetime.min.time())
        
        access_logs_query = text(f"""
            SELECT 
                sal.specimen_id,
                sal.access_type,
                sal.accessed_at
            FROM specimen_access_logs sal
            WHERE sal.specimen_id IN ({placeholders})
              AND sal.accessed_at > :wd_datetime
        """)
        
        access_result = conn.execute(access_logs_query, params)
        for row in access_result.fetchall():
            spec_code = specimen_codes_map.get(row.specimen_id, "")
            accessed_at_str = row.accessed_at.isoformat() if isinstance(row.accessed_at, datetime) else str(row.accessed_at)
            violations.append(AccessViolationDetail(
                specimen_code=spec_code,
                access_type=row.access_type,
                access_date=accessed_at_str,
            ))
        
        # Check transfers
        transfers_query = text(f"""
            SELECT 
                st.specimen_id,
                'transfer' as access_type,
                st.transfer_date as accessed_at
            FROM specimen_transfers st
            WHERE st.specimen_id IN ({placeholders})
              AND st.transfer_date > :wd_datetime
        """)
        
        transfers_result = conn.execute(transfers_query, params)
        for row in transfers_result.fetchall():
            spec_code = specimen_codes_map.get(row.specimen_id, "")
            transfer_date_str = row.accessed_at.isoformat() if isinstance(row.accessed_at, datetime) else str(row.accessed_at)
            violations.append(AccessViolationDetail(
                specimen_code=spec_code,
                access_type=row.access_type,
                access_date=transfer_date_str,
            ))
        
        return SpecimenAccessHistoryResponse(
            violations_found=len(violations) > 0,
            details=violations,
        )
