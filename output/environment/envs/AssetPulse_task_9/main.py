import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()

ALLOWED_DATA_TYPES = {"TEXT", "INTEGER", "REAL", "BLOB"}


class ClassificationOut(BaseModel):
    id: int
    name: str


class FieldDefinition(BaseModel):
    name: str
    data_type: str
    is_required: bool = False


class CreateSchemaRequest(BaseModel):
    name: str
    fields: List[FieldDefinition]


class CreateSchemaResponse(BaseModel):
    schema_id: int
    classification_id: int


class AssociateSchemaRequest(BaseModel):
    schema_id: int
    classification_id: int


class AssociateSchemaResponse(BaseModel):
    schema_id: int
    classification_id: int


class AddMetadataFieldRequest(BaseModel):
    schema_id: int
    name: str
    data_type: str
    is_required: bool = False


class AddMetadataFieldResponse(BaseModel):
    field_id: int
    schema_id: int
    name: str
    data_type: str
    is_required: bool


@app.get("/classifications", response_model=List[ClassificationOut], operation_id="find_classifications")
def find_classifications(name: Optional[str] = Query(None)):
    with engine.connect() as conn:
        if name:
            result = conn.execute(
                text("SELECT id, name FROM classifications WHERE name LIKE :name"),
                {"name": f"%{name}%"}
            )
        else:
            result = conn.execute(text("SELECT id, name FROM classifications"))
        rows = result.fetchall()
    return [{"id": r[0], "name": r[1]} for r in rows]


@app.post("/metadata-schemas", response_model=CreateSchemaResponse, operation_id="create_cold_chain_schema")
def create_cold_chain_schema(request: CreateSchemaRequest):
    # Validate data types first to avoid DB Integrity Errors
    for field_def in request.fields:
        if field_def.data_type.upper() not in ALLOWED_DATA_TYPES:
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid data_type '{field_def.data_type}'. Must be one of {list(ALLOWED_DATA_TYPES)}"
            )

    # Find the classification ID for "Refrigerated Transport Unit"
    with engine.connect() as conn:
        class_result = conn.execute(
            text("SELECT id FROM classifications WHERE name = :name"),
            {"name": "Refrigerated Transport Unit"}
        ).fetchone()
        if not class_result:
            raise HTTPException(status_code=404, detail="Classification 'Refrigerated Transport Unit' not found")
        classification_id = class_result[0]

        # Insert the new metadata schema
        insert_schema = conn.execute(
            text("INSERT INTO metadata_schemas (name, description) VALUES (:name, :description) RETURNING id"),
            {"name": request.name, "description": None}
        )
        schema_id = insert_schema.fetchone()[0]

        # Insert each field into metadata_fields
        for idx, field_def in enumerate(request.fields):
            conn.execute(
                text("""
                    INSERT INTO metadata_fields (schema_id, name, data_type, is_required, display_order)
                    VALUES (:schema_id, :name, :data_type, :is_required, :display_order)
                """),
                {
                    "schema_id": schema_id,
                    "name": field_def.name,
                    "data_type": field_def.data_type.upper(),
                    "is_required": 1 if field_def.is_required else 0,
                    "display_order": idx + 1
                }
            )

        # Associate the schema with the classification
        conn.execute(
            text("""
                INSERT INTO schema_classifications (schema_id, classification_id)
                VALUES (:schema_id, :classification_id)
            """),
            {"schema_id": schema_id, "classification_id": classification_id}
        )

        conn.commit()

    return {"schema_id": schema_id, "classification_id": classification_id}


@app.post("/schema-classifications", response_model=AssociateSchemaResponse, operation_id="associate_schema_with_classification")
def associate_schema_with_classification(request: AssociateSchemaRequest):
    with engine.connect() as conn:
        # Verify schema exists
        schema_exists = conn.execute(
            text("SELECT id FROM metadata_schemas WHERE id = :id"),
            {"id": request.schema_id}
        ).fetchone()
        if not schema_exists:
            raise HTTPException(status_code=404, detail="Schema not found")

        # Verify classification exists
        class_exists = conn.execute(
            text("SELECT id FROM classifications WHERE id = :id"),
            {"id": request.classification_id}
        ).fetchone()
        if not class_exists:
            raise HTTPException(status_code=404, detail="Classification not found")

        # Check if association already exists
        existing = conn.execute(
            text("SELECT schema_id, classification_id FROM schema_classifications WHERE schema_id = :sid AND classification_id = :cid"),
            {"sid": request.schema_id, "cid": request.classification_id}
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Association already exists")

        conn.execute(
            text("""
                INSERT INTO schema_classifications (schema_id, classification_id)
                VALUES (:schema_id, :classification_id)
            """),
            {"schema_id": request.schema_id, "classification_id": request.classification_id}
        )
        conn.commit()

    return {"schema_id": request.schema_id, "classification_id": request.classification_id}


@app.post("/metadata-fields", response_model=AddMetadataFieldResponse, operation_id="add_metadata_field")
def add_metadata_field(request: AddMetadataFieldRequest):
    # Validate data type
    if request.data_type.upper() not in ALLOWED_DATA_TYPES:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid data_type '{request.data_type}'. Must be one of {list(ALLOWED_DATA_TYPES)}"
        )

    with engine.connect() as conn:
        # Verify schema exists
        schema_exists = conn.execute(
            text("SELECT id FROM metadata_schemas WHERE id = :id"),
            {"id": request.schema_id}
        ).fetchone()
        if not schema_exists:
            raise HTTPException(status_code=404, detail="Schema not found")

        # Check if field name already exists in this schema
        existing_field = conn.execute(
            text("SELECT id FROM metadata_fields WHERE schema_id = :sid AND name = :name"),
            {"sid": request.schema_id, "name": request.name}
        ).fetchone()
        if existing_field:
            raise HTTPException(status_code=409, detail="Field name already exists in this schema")

        # Get max display_order for this schema
        max_order_result = conn.execute(
            text("SELECT COALESCE(MAX(display_order), 0) FROM metadata_fields WHERE schema_id = :sid"),
            {"sid": request.schema_id}
        ).fetchone()
        next_order = max_order_result[0] + 1

        # Insert the new field
        insert_result = conn.execute(
            text("""
                INSERT INTO metadata_fields (schema_id, name, data_type, is_required, display_order)
                VALUES (:schema_id, :name, :data_type, :is_required, :display_order)
                RETURNING id
            """),
            {
                "schema_id": request.schema_id,
                "name": request.name,
                "data_type": request.data_type.upper(),
                "is_required": 1 if request.is_required else 0,
                "display_order": next_order
            }
        )
        field_id = insert_result.fetchone()[0]
        conn.commit()

    return {
        "field_id": field_id,
        "schema_id": request.schema_id,
        "name": request.name,
        "data_type": request.data_type.upper(),
        "is_required": request.is_required
    }
