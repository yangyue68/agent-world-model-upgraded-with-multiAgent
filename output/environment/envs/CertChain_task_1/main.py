import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from contextlib import asynccontextmanager

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)


class CEUCategoryItem(BaseModel):
    name: str
    description: Optional[str] = None


class ExamDomainItem(BaseModel):
    name: str


class CreateCertificationSchemeRequest(BaseModel):
    name: str
    recertification_cycle_years: int
    total_required_ceus: int
    min_categories: int
    ceu_categories: List[CEUCategoryItem]
    exam_domains: List[ExamDomainItem]


class CreateCertificationSchemeResponse(BaseModel):
    scheme_id: int
    message: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    # No startup/shutdown logic that modifies DB state or creates tables
    yield


app = FastAPI(lifespan=lifespan)


@app.post("/certification-schemes", response_model=CreateCertificationSchemeResponse, operation_id="create_certification_scheme")
def create_certification_scheme(payload: CreateCertificationSchemeRequest):
    """
    Creates a new certification scheme with specified name, recertification cycle, 
    CEU requirements, and exam blueprint.
    """
    
    # Validate business rules based on task description if necessary, 
    # though primarily we rely on DB constraints and input validation.
    # The task specifies: 3-year cycle, 120 CEUs, min 2 categories, 5 domains @ 20%.
    # However, the API allows generic creation. We will insert what is provided.
    
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        created_by = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")

    with engine.begin() as conn:
        # Insert into certification_schemes
        stmt_scheme = text("""
            INSERT INTO certification_schemes (name, recertification_cycle_years, total_required_ceus, min_categories, created_by)
            VALUES (:name, :cycle, :ceus, :min_cats, :created_by)
        """)
        
        result = conn.execute(stmt_scheme, {
            "name": payload.name,
            "cycle": payload.recertification_cycle_years,
            "ceus": payload.total_required_ceus,
            "min_cats": payload.min_categories,
            "created_by": created_by
        })
        
        scheme_id = result.lastrowid
        
        if not scheme_id:
            raise HTTPException(status_code=500, detail="Failed to create certification scheme")

        # Insert into ceu_categories
        if payload.ceu_categories:
            stmt_cat = text("""
                INSERT INTO ceu_categories (scheme_id, name, description)
                VALUES (:scheme_id, :name, :description)
            """)
            for cat in payload.ceu_categories:
                conn.execute(stmt_cat, {
                    "scheme_id": scheme_id,
                    "name": cat.name,
                    "description": cat.description
                })

        # Insert into exam_domains
        if payload.exam_domains:
            stmt_dom = text("""
                INSERT INTO exam_domains (scheme_id, name, weight_percent)
                VALUES (:scheme_id, :name, :weight)
            """)
            # Calculate weight per domain. Task says 5 domains each weighted at 20%.
            # If count != 5, we distribute evenly? Or strictly follow input?
            # The spec doesn't provide weight in input, so we assume equal distribution summing to 100%
            num_domains = len(payload.exam_domains)
            if num_domains == 0:
                 pass
            else:
                weight_per_domain = round(100.0 / num_domains, 2)
                for dom in payload.exam_domains:
                    conn.execute(stmt_dom, {
                        "scheme_id": scheme_id,
                        "name": dom.name,
                        "weight": weight_per_domain
                    })

    return CreateCertificationSchemeResponse(
        scheme_id=scheme_id,
        message=f"Certification scheme '{payload.name}' created successfully."
    )
