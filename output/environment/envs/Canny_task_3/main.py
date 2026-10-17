import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class PostIdResponse(BaseModel):
    post_id: int


class OwnerInfo(BaseModel):
    id: int
    name: str
    email: str


class PostDetailsResponse(BaseModel):
    description: Optional[str]
    category: Optional[str]
    tags: List[str]
    owner: Optional[OwnerInfo]
    public_comment_count: int
    internal_comment_count: int


class VoterInfo(BaseModel):
    name: str
    email: str
    mrr: float


@app.get("/posts/with_most_votes", response_model=PostIdResponse, operation_id="find_post_with_most_votes")
def find_post_with_most_votes():
    """
    Find the post with the most votes that has the 'billing' tag and is 'Complete'.
    Returns the post ID.
    """
    query = text("""
        SELECT p.id
        FROM posts p
        JOIN post_tags pt ON p.id = pt.post_id
        JOIN tags t ON pt.tag_id = t.id
        WHERE t.name = :tag_name AND p.status = :status
        ORDER BY p.vote_count DESC
        LIMIT 1
    """)
    
    params = {
        "tag_name": "billing",
        "status": "Complete"
    }
    
    with engine.connect() as conn:
        result = conn.execute(query, params).fetchone()
        
    if not result:
        raise HTTPException(status_code=404, detail="No matching post found")
        
    return {"post_id": result[0]}


@app.get("/posts/{post_id}/details", response_model=PostDetailsResponse, operation_id="get_post_details")
def get_post_details(post_id: int):
    """
    Get detailed information about a specific post including description, category, 
    tags, assigned owner, and comment counts.
    """
    # Fetch basic post info and owner info
    post_query = text("""
        SELECT 
            p.description,
            p.category,
            p.public_comment_count,
            p.internal_comment_count,
            u.id AS owner_id,
            u.name AS owner_name,
            u.email AS owner_email
        FROM posts p
        LEFT JOIN users u ON p.owner_id = u.id
        WHERE p.id = :post_id
    """)
    
    with engine.connect() as conn:
        post_result = conn.execute(post_query, {"post_id": post_id}).fetchone()
        
    if not post_result:
        raise HTTPException(status_code=404, detail="Post not found")
        
    description, category, public_count, internal_count, owner_id, owner_name, owner_email = post_result
    
    # Fetch tags for the post
    tags_query = text("""
        SELECT t.name
        FROM tags t
        JOIN post_tags pt ON t.id = pt.tag_id
        WHERE pt.post_id = :post_id
    """)
    
    with engine.connect() as conn:
        tag_rows = conn.execute(tags_query, {"post_id": post_id}).fetchall()
        
    tags = [row[0] for row in tag_rows]
    
    owner_info = None
    if owner_id is not None:
        owner_info = OwnerInfo(
            id=owner_id,
            name=owner_name,
            email=owner_email
        )
        
    return PostDetailsResponse(
        description=description,
        category=category,
        tags=tags,
        owner=owner_info,
        public_comment_count=public_count or 0,
        internal_comment_count=internal_count or 0
    )


@app.get("/posts/{post_id}/voters", response_model=List[VoterInfo], operation_id="get_post_voters")
def get_post_voters(post_id: int):
    """
    Retrieve the top 10 voters for a post sorted by MRR descending.
    Returns names, emails, and MRR values of the top 10 users who voted on the specified post.
    """
    # Verify post exists
    check_post = text("SELECT id FROM posts WHERE id = :post_id")
    with engine.connect() as conn:
        if not conn.execute(check_post, {"post_id": post_id}).fetchone():
            raise HTTPException(status_code=404, detail="Post not found")

    query = text("""
        SELECT 
            u.name,
            u.email,
            u.mrr
        FROM votes v
        JOIN users u ON v.user_id = u.id
        WHERE v.post_id = :post_id
        ORDER BY u.mrr DESC
        LIMIT 10
    """)
    
    with engine.connect() as conn:
        results = conn.execute(query, {"post_id": post_id}).fetchall()
        
    voters = []
    for row in results:
        voters.append(VoterInfo(
            name=row[0],
            email=row[1],
            mrr=float(row[2]) if row[2] is not None else 0.0
        ))
        
    return voters
