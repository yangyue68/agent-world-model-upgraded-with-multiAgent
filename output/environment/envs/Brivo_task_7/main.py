import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class AccessGroupItem(BaseModel):
    group_id: int
    group_name: str


class DoorResponse(BaseModel):
    door_id: int
    door_name: str
    location: Optional[str]
    reader_status: str
    access_groups: List[AccessGroupItem]


def get_active_doors_for_site_internal(site_id: int) -> List[DoorResponse]:
    """
    Helper function to retrieve active doors for a given site ID.
    Includes door details, aggregated reader status, and associated access groups.
    """
    db = SessionLocal()
    try:
        # 1. Get all active doors for the site
        query_doors = text("""
            SELECT id, name, location 
            FROM doors 
            WHERE site_id = :site_id AND is_active = 1
            ORDER BY id
        """)
        doors_result = db.execute(query_doors, {"site_id": site_id}).fetchall()
        
        if not doors_result:
            return []

        door_ids = [row.id for row in doors_result]
        doors_map = {row.id: {"door_name": row.name, "location": row.location} for row in doors_result}

        # 2. Get reader statuses for these doors
        # We need to determine the 'reader_status' for each door. 
        # The schema allows multiple readers per door? No, usually one primary reader or we aggregate.
        # Looking at data: Each door has exactly one reader in the sample.
        # If there were multiple, how should we represent it? The API spec says "reader operational status" (singular).
        # Let's assume we take the status of the reader(s). If multiple, maybe join them or pick one?
        # Given the simplicity and typical Brivo models, let's fetch all readers for these doors.
        # To match the output format strictly, I will aggregate distinct statuses or just take the first one if unique.
        # However, looking at the example data, it's 1-to-1.
        # Let's fetch readers grouped by door_id.
        
        placeholders = ", ".join([f":did_{i}" for i in range(len(door_ids))])
        params_readers = {f"did_{i}": did for i, did in enumerate(door_ids)}
        
        query_readers = text(f"""
            SELECT door_id, operational_status 
            FROM readers 
            WHERE door_id IN ({placeholders})
        """)
        readers_result = db.execute(query_readers, params_readers).fetchall()
        
        # Map door_id to list of statuses
        door_reader_statuses = {}
        for row in readers_result:
            if row.door_id not in door_reader_statuses:
                door_reader_statuses[row.door_id] = []
            door_reader_statuses[row.door_id].append(row.operational_status)
            
        # Determine final status string. 
        # If single reader, use its status. If multiple, perhaps comma separated or priority based?
        # Spec doesn't specify multi-reader handling clearly, but standard practice might be to show all or most critical.
        # For this implementation, since the DB structure implies potentially many, but examples are 1:1,
        # I'll join distinct statuses with commas if multiple, otherwise just the status.
        # Actually, simpler approach often expected in these tasks: Just grab the status. 
        # If there are no readers, what is the status? 'unknown'?
        
        final_door_status = {}
        for did in door_ids:
            statuses = door_reader_statuses.get(did, [])
            if not statuses:
                final_door_status[did] = "unknown"
            else:
                # Deduplicate and sort for consistency, then join
                unique_statuses = sorted(list(set(statuses)))
                final_door_status[did] = ", ".join(unique_statuses)

        # 3. Get access groups for these doors
        query_access_groups = text(f"""
            SELECT dag.door_id, ag.id as group_id, ag.name as group_name
            FROM door_access_groups dag
            JOIN access_groups ag ON dag.access_group_id = ag.id
            WHERE dag.door_id IN ({placeholders})
            ORDER BY dag.door_id, ag.id
        """)
        ag_result = db.execute(query_access_groups, params_readers).fetchall()
        
        door_access_groups_map = {did: [] for did in door_ids}
        for row in ag_result:
            door_access_groups_map[row.door_id].append({
                "group_id": row.group_id,
                "group_name": row.group_name
            })

        # 4. Construct Response
        response_list = []
        for did in door_ids:
            d_info = doors_map[did]
            r_status = final_door_status.get(did, "unknown")
            a_groups = door_access_groups_map.get(did, [])
            
            response_list.append(DoorResponse(
                door_id=did,
                door_name=d_info["door_name"],
                location=d_info["location"],
                reader_status=r_status,
                access_groups=[AccessGroupItem(**ag) for ag in a_groups]
            ))
            
        return response_list

    finally:
        db.close()


@app.get("/sites/{site_id}/active_doors", response_model=List[DoorResponse], operation_id="get_active_doors_for_site")
def get_active_doors_for_site(site_id: int):
    """
    Retrieve all active doors at a specific site with reader status and access groups
    """
    # Verify site exists? Not strictly required by spec unless error handling needed.
    # But good practice. If site doesn't exist, returning empty list is acceptable behavior for "no doors found".
    result = get_active_doors_for_site_internal(site_id)
    return result


@app.get("/sites/by_name/{site_name}/active_doors", response_model=List[DoorResponse], operation_id="get_active_doors_for_site_by_name")
def get_active_doors_for_site_by_name(site_name: str):
    """
    Retrieve all active doors at a specific site by site name with reader status and access groups
    """
    db = SessionLocal()
    try:
        # Find site ID by name
        query_site = text("SELECT id FROM sites WHERE name = :name LIMIT 1")
        site_row = db.execute(query_site, {"name": site_name}).fetchone()
        
        if not site_row:
            # Site not found. Return empty list as per REST conventions for sub-resources not existing,
            # or 404? The spec doesn't explicitly say 404 for missing site, but logically if site doesn't exist,
            # it has no doors. Returning [] is safe and consistent with the other endpoint which returns [] for no doors.
            return []
            
        site_id = site_row.id
        
        result = get_active_doors_for_site_internal(site_id)
        return result
    finally:
        db.close()
