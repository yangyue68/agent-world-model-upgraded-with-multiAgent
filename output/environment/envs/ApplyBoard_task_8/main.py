import os
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
DATABASE_PATH = os.environ.get("DATABASE_PATH")
app = FastAPI()
engine = create_engine(DATABASE_PATH)


class MissingDocumentResponse(BaseModel):
    application_id: int
    missing_document_types: List[str]


@app.get(
    "/applications/missing-required-documents",
    response_model=List[MissingDocumentResponse],
    operation_id="find_applications_missing_required_documents",
    summary="Find applications with missing required documents submitted in the last 7 days",
    description="Returns application IDs and their missing document types for applications that were submitted within the last 7 days and are missing at least one required document.",
    tags=["PrimaryResource"],
)
def find_applications_missing_required_documents(
    days_back: int = Query(7, description="Number of days back to consider for submission date (default: 7)")
):
    cutoff_date = datetime.now() - timedelta(days=days_back)
    
    query = text("""
        SELECT 
            a.id as application_id,
            rd.document_type
        FROM applications a
        JOIN required_documents rd ON a.id = rd.application_id
        WHERE a.submitted_at >= :cutoff_date
          AND rd.is_required = 1
          AND NOT EXISTS (
              SELECT 1 
              FROM submitted_documents sd 
              WHERE sd.application_id = a.id 
                AND sd.document_type = rd.document_type
          )
        ORDER BY a.id, rd.document_type
    """)
    
    with engine.connect() as conn:
        results = conn.execute(query, {"cutoff_date": cutoff_date}).fetchall()
    
    # Group by application_id
    app_map = {}
    for row in results:
        app_id = row.application_id
        doc_type = row.document_type
        if app_id not in app_map:
            app_map[app_id] = []
        app_map[app_id].append(doc_type)
    
    response = [
        MissingDocumentResponse(
            application_id=app_id,
            missing_document_types=sorted(doc_types)
        )
        for app_id, doc_types in sorted(app_map.items())
    ]
    
    return response
