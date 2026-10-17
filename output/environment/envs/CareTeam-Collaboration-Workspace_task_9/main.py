import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class SearchCareTeamsResponse(BaseModel):
    care_team_id: int


class InitiateDataExchangeResponse(BaseModel):
    data_exchange_id: int
    status: str


@app.get("/care-teams/search", response_model=SearchCareTeamsResponse, operation_id="search_care_teams")
def search_care_teams(
    patient_identifier: str = Query(..., description="The patient identifier (e.g., PT-789456)"),
    target_organization_name: str = Query(..., description="The name of the target organization (e.g., MetroHealth Behavioral Health Network)")
):
    """
    Search for a care team by patient identifier and target organization name.
    
    Retrieves the internal ID of a care team associated with a specific patient 
    and targeting a particular organization via an active consent.
    """
    query = text("""
        SELECT ct.id AS care_team_id
        FROM patients p
        JOIN care_teams ct ON ct.patient_id = p.id AND ct.status = 'active'
        JOIN consents c ON c.patient_id = p.id AND c.status = 'active'
        JOIN organizations o ON o.id = c.grantee_organization_id
        WHERE p.patient_identifier = :patient_identifier
          AND o.name = :target_organization_name
        LIMIT 1
    """)
    
    with engine.connect() as conn:
        result = conn.execute(query, {
            "patient_identifier": patient_identifier,
            "target_organization_name": target_organization_name
        }).fetchone()
        
    if not result:
        raise HTTPException(status_code=404, detail="No matching active care team found for the given patient and target organization.")
        
    return {"care_team_id": result.care_team_id}


@app.post("/data-exchanges", response_model=InitiateDataExchangeResponse, operation_id="initiate_data_exchange")
def initiate_data_exchange(
    patient_identifier: str = Query(..., description="The patient identifier (e.g., PT-789456)"),
    target_organization_name: str = Query(..., description="The name of the target organization (e.g., MetroHealth Behavioral Health Network)"),
    discharge_summary_tag: str = Query(..., description="Tag identifying the discharge summary document (e.g., DISCHARGE_20240512)"),
    care_team_id: int = Query(..., description="The ID of the source care team initiating the exchange"),
    consent_id: int = Query(..., description="The ID of the consent granting permission for this exchange"),
    initiated_by_user_id: int = Query(..., description="The ID of the user initiating the exchange")
):
    """
    Initiate a consent-gated FHIR-based data exchange.
    
    Creates a new data exchange from a CareTeam to a target organization for a specific patient,
    sharing the latest active Care Plan and a specified discharge summary document.
    """
    # Validate inputs against DB state
    
    # 1. Verify Patient exists
    q_patient = text("SELECT id FROM patients WHERE patient_identifier = :pid")
    with engine.connect() as conn:
        patient_row = conn.execute(q_patient, {"pid": patient_identifier}).fetchone()
        if not patient_row:
            raise HTTPException(status_code=404, detail="Patient not found.")
        patient_db_id = patient_row.id
        
        # 2. Verify Target Organization exists
        q_org = text("SELECT id FROM organizations WHERE name = :org_name")
        org_row = conn.execute(q_org, {"org_name": target_organization_name}).fetchone()
        if not org_row:
            raise HTTPException(status_code=404, detail="Target organization not found.")
        target_org_db_id = org_row.id
        
        # 3. Verify Consent is valid for this patient/org pair
        q_consent = text("""
            SELECT id FROM consents 
            WHERE id = :cid 
              AND patient_id = :pid 
              AND grantee_organization_id = :oid 
              AND status = 'active'
        """)
        consent_row = conn.execute(q_consent, {
            "cid": consent_id,
            "pid": patient_db_id,
            "oid": target_org_db_id
        }).fetchone()
        if not consent_row:
            raise HTTPException(status_code=403, detail="Invalid or inactive consent for this exchange.")
            
        # 4. Find Latest Active Care Plan for the patient
        q_latest_cp = text("""
            SELECT id, version FROM care_plans
            WHERE patient_id = :pid
              AND status = 'active'
            ORDER BY version DESC
            LIMIT 1
        """)
        cp_row = conn.execute(q_latest_cp, {"pid": patient_db_id}).fetchone()
        if not cp_row:
            raise HTTPException(status_code=404, detail="No active care plan found for patient.")
        latest_care_plan_id = cp_row.id
        latest_care_plan_version = cp_row.version
        
        # 5. Find Document with specified tag linked to the latest care plan (or just patient?)
        # Spec says: "sharing only the latest version of the active Care Plan and associated discharge summary document tagged..."
        # Usually documents are linked to care plans. Let's look for doc with tag linked to the latest CP first, 
        # or generally for the patient if not strictly bound? The schema has care_plan_id in documents.
        # Let's assume it must be associated with the shared care plan context or at least current for that patient.
        # Given the example data: Doc 1 is linked to CP 2 (latest). Tag DISCHARGE_20240512 is on Doc 1.
        
        q_doc = text("""
            SELECT d.id, d.version
            FROM documents d
            JOIN document_tags dt ON dt.document_id = d.id
            WHERE d.patient_id = :pid
              AND dt.tag = :tag
              AND d.status = 'current'
            ORDER BY d.created_at DESC
            LIMIT 1
        """)
        doc_row = conn.execute(q_doc, {
            "pid": patient_db_id,
            "tag": discharge_summary_tag
        }).fetchone()
        
        if not doc_row:
             raise HTTPException(status_code=404, detail=f"Discharge summary document with tag '{discharge_summary_tag}' not found.")
             
        doc_id = doc_row.id
        doc_version = doc_row.version

        # 6. Create Data Exchange Record
        insert_de = text("""
            INSERT INTO data_exchanges (
                patient_id, 
                source_care_team_id, 
                target_organization_id, 
                consent_id, 
                status, 
                fhir_bundle, 
                initiated_by_user_id
            ) VALUES (
                :pid, 
                :ctid, 
                :toid, 
                :cid, 
                'pending', 
                NULL, 
                :uid
            ) RETURNING id
        """)
        de_result = conn.execute(insert_de, {
            "pid": patient_db_id,
            "ctid": care_team_id,
            "toid": target_org_db_id,
            "cid": consent_id,
            "uid": initiated_by_user_id
        })
        new_de_id = de_result.fetchone().id
        
        # 7. Add Items to Data Exchange
        # Item 1: Care Plan
        insert_item_cp = text("""
            INSERT INTO data_exchange_items (data_exchange_id, resource_type, resource_id, version)
            VALUES (:deid, 'CarePlan', :cpid, :ver)
        """)
        conn.execute(insert_item_cp, {
            "deid": new_de_id,
            "cpid": latest_care_plan_id,
            "ver": latest_care_plan_version
        })
        
        # Item 2: Document
        insert_item_doc = text("""
            INSERT INTO data_exchange_items (data_exchange_id, resource_type, resource_id, version)
            VALUES (:deid, 'Document', :did, :ver)
        """)
        conn.execute(insert_item_doc, {
            "deid": new_de_id,
            "did": doc_id,
            "ver": doc_version
        })
        
        conn.commit()

    return {
        "data_exchange_id": new_de_id,
        "status": "pending"
    }
