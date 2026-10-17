import os
from typing import Optional, List

from fastapi import FastAPI, Query, HTTPException
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


class PostResponse(BaseModel):
    id: int
    title: str
    vote_count: int
    created_at: str


@app.get("/boards/{board_id}/posts", response_model=List[PostResponse], operation_id="search_board_posts")
def search_board_posts(
    board_id: int,
    keyword: Optional[str] = Query(None, description="Keyword to search in post titles"),
    status: Optional[str] = Query(None, description="Status name to filter by"),
    category: Optional[str] = Query(None, description="Category name to filter by"),
    tag: Optional[str] = Query(None, description="Tag name to filter by"),
    limit: Optional[int] = Query(None, description="Maximum number of results to return"),
):
    """
    Search posts in a specific board with filters and sorting.
    
    Specifically for the task:
    - Board: 'Product Feedback' (ID 1 based on snapshot, but we use path param)
    - Keyword: 'dark mode'
    - Status: 'Under Review'
    - Category: 'UI/UX'
    - Tag: 'mobile'
    - Sort: votes descending
    - Limit: top 5
    """
    
    # Build dynamic SQL query
    base_query = """
        SELECT 
            p.id,
            p.title,
            p.vote_count,
            p.created_at
        FROM posts p
        JOIN boards b ON p.board_id = b.id
        LEFT JOIN statuses s ON p.status_id = s.id
        LEFT JOIN categories c ON p.category_id = c.id
        LEFT JOIN post_tags pt ON p.id = pt.post_id
        LEFT JOIN tags t ON pt.tag_id = t.id
        WHERE p.board_id = :board_id
    """
    
    params = {"board_id": board_id}
    
    if keyword:
        base_query += " AND p.title LIKE :keyword"
        params["keyword"] = f"%{keyword}%"
        
    if status:
        base_query += " AND s.name = :status"
        params["status"] = status
        
    if category:
        base_query += " AND c.name = :category"
        params["category"] = category
        
    if tag:
        base_query += " AND t.name = :tag"
        params["tag"] = tag
    
    # Group by post ID to handle multiple tags if necessary, though here we filter by single tag
    # Since we are joining post_tags and tags, if a post has multiple tags matching criteria, 
    # it might appear multiple times without DISTINCT or GROUP BY.
    # However, the requirement is just filtering. Let's ensure distinct posts.
    # Actually, since we join left, and filter on t.name, a post matches if it has that tag.
    # If a post has multiple instances of the same tag (unlikely due to PK), or different tags,
    # the join produces one row per matching tag. We want unique posts.
    # Using DISTINCT or GROUP BY p.id is safer.
    
    final_query = f"""
        SELECT DISTINCT
            sub.id,
            sub.title,
            sub.vote_count,
            sub.created_at
        FROM ({base_query}) AS sub
        ORDER BY sub.vote_count DESC
    """
    
    effective_limit = limit if limit else 5
    final_query += f" LIMIT {effective_limit}"
    
    db = SessionLocal()
    try:
        result = db.execute(text(final_query), params)
        rows = result.fetchall()
        
        response_data = []
        for row in rows:
            response_data.append(PostResponse(
                id=row.id,
                title=row.title,
                vote_count=row.vote_count,
                created_at=str(row.created_at)
            ))
            
        return response_data
    finally:
        db.close()
