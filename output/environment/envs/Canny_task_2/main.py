import os
from typing import Optional, List

from fastapi import FastAPI, Query, Path, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from contextlib import asynccontextmanager

# Get DATABASE_PATH from environment or default to sqlite for local testing if needed, 
# but instructions say it already contains the complete URL.
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)

class BoardSearchResult(BaseModel):
    id: int
    name: str

class OwnerInfo(BaseModel):
    id: int
    display_name: str

class PostDetail(BaseModel):
    title: str
    status: str
    category: str
    tags: List[str]
    vote_count: int
    owner: OwnerInfo

app = FastAPI()

@app.get("/boards/search", response_model=List[BoardSearchResult], operation_id="search_boards")
def search_boards(keyword: str = Query(..., description="Keyword to search in board names or descriptions")):
    """
    Search boards by the provided keyword in the board name or description and return matching board IDs and names.
    """
    query = text("""
        SELECT id, name 
        FROM boards 
        WHERE name LIKE :keyword OR description LIKE :keyword
    """)
    
    # Use case-insensitive search as is common for such features, though SQLite LIKE is case-insensitive for ASCII by default
    param_keyword = f"%{keyword}%"
    
    with engine.connect() as conn:
        result = conn.execute(query, {"keyword": param_keyword})
        rows = result.fetchall()
        
    return [BoardSearchResult(id=row.id, name=row.name) for row in rows]

@app.get("/boards/{board_id}/posts", response_model=List[PostDetail], operation_id="find_board_posts")
def find_board_posts(
    board_id: int = Path(..., description="ID of the board to query"),
    status: Optional[str] = Query(None, description="Status name to filter posts by (e.g., 'Planned', 'In Progress')"),
    segment: Optional[str] = Query(None, description="Segment name to filter posts by (e.g., 'Enterprise')"),
    category: Optional[str] = Query(None, description="Category name to filter posts by (e.g., 'Integrations')")
):
    """
    Filter posts within a specified board by status, segment, and category, then sort by creation date descending and return post details.
    """
    
    # Base query joining necessary tables
    base_sql = """
        SELECT 
            p.title,
            s.name AS status_name,
            c.name AS category_name,
            p.vote_count,
            u.id AS owner_id,
            u.display_name AS owner_display_name,
            p.created_at
        FROM posts p
        LEFT JOIN statuses s ON p.status_id = s.id
        LEFT JOIN segments seg ON p.segment_id = seg.id
        LEFT JOIN categories c ON p.category_id = c.id
        LEFT JOIN users u ON p.owner_id = u.id
        WHERE p.board_id = :board_id
    """
    
    params = {"board_id": board_id}
    conditions = []
    
    if status:
        conditions.append("s.name = :status")
        params["status"] = status
        
    if segment:
        conditions.append("seg.name = :segment")
        params["segment"] = segment
        
    if category:
        conditions.append("c.name = :category")
        params["category"] = category
        
    if conditions:
        base_sql += " AND " + " AND ".join(conditions)
        
    base_sql += " ORDER BY p.created_at DESC"
    
    with engine.connect() as conn:
        # Fetch main post data
        result = conn.execute(text(base_sql), params)
        posts_rows = result.fetchall()
        
        if not posts_rows:
            return []
            
        # Collect post IDs to fetch tags efficiently
        # Note: The previous query didn't select p.id explicitly in the final output model, 
        # but we need it to join back for tags. Let's adjust the initial query to include p.id.
        
    # Re-run with ID included for tag fetching logic clarity, or handle in one go if possible.
    # Since SQLAlchemy ORM isn't used here, manual assembly is safer for complex joins like many-to-many tags.
    
    sql_with_ids = """
        SELECT 
            p.id as post_id,
            p.title,
            COALESCE(s.name, '') AS status_name,
            COALESCE(c.name, '') AS category_name,
            p.vote_count,
            u.id AS owner_id,
            u.display_name AS owner_display_name
        FROM posts p
        LEFT JOIN statuses s ON p.status_id = s.id
        LEFT JOIN segments seg ON p.segment_id = seg.id
        LEFT JOIN categories c ON p.category_id = c.id
        LEFT JOIN users u ON p.owner_id = u.id
        WHERE p.board_id = :board_id
    """
    
    if conditions:
        sql_with_ids += " AND " + " AND ".join(conditions)
        
    sql_with_ids += " ORDER BY p.created_at DESC"
    
    with engine.connect() as conn:
        result = conn.execute(text(sql_with_ids), params)
        posts_data = {}
        ordered_post_ids = []
        
        for row in result:
            pid = row.post_id
            ordered_post_ids.append(pid)
            posts_data[pid] = {
                "title": row.title,
                "status": row.status_name,
                "category": row.category_name,
                "vote_count": row.vote_count,
                "owner": OwnerInfo(id=row.owner_id, display_name=row.owner_display_name) if row.owner_id else None,
                "tags": []
            }
            
        if not ordered_post_ids:
            return []
            
        # Fetch tags for these posts
        # Create placeholders for IN clause
        placeholders = ", ".join([f":tid_{i}" for i in range(len(ordered_post_ids))])
        tag_params = {f"tid_{i}": pid for i, pid in enumerate(ordered_post_ids)}
        
        tags_query = text(f"""
            SELECT pt.post_id, t.name
            FROM post_tags pt
            JOIN tags t ON pt.tag_id = t.id
            WHERE pt.post_id IN ({placeholders})
        """)
        
        tags_result = conn.execute(tags_query, tag_params)
        
        for tag_row in tags_result:
            pid = tag_row.post_id
            if pid in posts_data:
                posts_data[pid]["tags"].append(tag_row.name)
                
    # Construct final list preserving order
    final_results = []
    for pid in ordered_post_ids:
        data = posts_data[pid]
        # Ensure owner is present in response model structure even if null in DB? 
        # Spec says owner is required object. If owner_id is null, what should happen?
        # Looking at schema: owner_id references users(id). It can be NULL.
        # Response model requires `owner` object with id and display_name.
        # If no owner exists in DB, this might violate the contract unless we assume every post has an owner.
        # Given the sample data, all posts have owners. I will assume valid data.
        # If owner was missing, Pydantic validation would fail. 
        
        final_results.append(PostDetail(**data))
        
    return final_results
