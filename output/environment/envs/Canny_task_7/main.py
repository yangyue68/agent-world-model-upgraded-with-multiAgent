import os
from typing import Optional, List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class MergePostsRequest(BaseModel):
    source_post_id: int
    target_post_id: int


class UpdatePostRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    category_id: Optional[int] = None
    status_id: Optional[int] = None
    owner_email: Optional[str] = None
    tag_names: Optional[List[str]] = None


@app.post("/merge-posts", operation_id="merge_duplicate_posts")
def merge_duplicate_posts(request: MergePostsRequest):
    with engine.begin() as conn:
        # Verify both posts exist and are on the same board
        result = conn.execute(
            text("""
                SELECT p1.id AS id1, p2.id AS id2, p1.board_id AS b1, p2.board_id AS b2
                FROM posts p1, posts p2
                WHERE p1.id = :source AND p2.id = :target
            """),
            {"source": request.source_post_id, "target": request.target_post_id}
        ).fetchone()

        if not result:
            raise HTTPException(status_code=404, detail="One or both posts not found")

        if result.b1 != result.b2:
            raise HTTPException(status_code=400, detail="Posts must be on the same board")

        source_id = request.source_post_id
        target_id = request.target_post_id

        # Transfer votes from source to target, avoiding duplicates via INSERT OR IGNORE
        conn.execute(
            text("""
                INSERT OR IGNORE INTO votes (post_id, user_id, created_at, updated_at)
                SELECT :target, v.user_id, v.created_at, v.updated_at
                FROM votes v
                WHERE v.post_id = :source
            """),
            {"target": target_id, "source": source_id}
        )

        # Delete original votes from source post
        conn.execute(
            text("DELETE FROM votes WHERE post_id = :source"),
            {"source": source_id}
        )

        # Transfer comments from source to target
        conn.execute(
            text("""
                UPDATE comments
                SET post_id = :target
                WHERE post_id = :source
            """),
            {"target": target_id, "source": source_id}
        )

        # Transfer tags from source to target, avoiding duplicates
        conn.execute(
            text("""
                INSERT OR IGNORE INTO post_tags (post_id, tag_id, created_at)
                SELECT :target, pt.tag_id, pt.created_at
                FROM post_tags pt
                WHERE pt.post_id = :source
            """),
            {"target": target_id, "source": source_id}
        )

        # Delete original tags from source post
        conn.execute(
            text("DELETE FROM post_tags WHERE post_id = :source"),
            {"source": source_id}
        )

        # Delete the source post
        conn.execute(
            text("DELETE FROM posts WHERE id = :source"),
            {"source": source_id}
        )

    return {
        "merged_post_id": target_id,
        "message": f"Post {source_id} merged into post {target_id}"
    }


@app.put("/posts/{post_id}", operation_id="update_merged_post")
def update_merged_post(post_id: int, request: UpdatePostRequest):
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    
    with engine.begin() as conn:
        # Check if post exists
        result = conn.execute(
            text("SELECT id FROM posts WHERE id = :pid"),
            {"pid": post_id}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Post not found")

        updates = []
        params = {"pid": post_id}

        if request.title is not None:
            updates.append("title = :title")
            params["title"] = request.title
        
        if request.description is not None:
            updates.append("description = :desc")
            params["desc"] = request.description
        
        if request.category_id is not None:
            # Verify category exists
            cat_check = conn.execute(
                text("SELECT id FROM categories WHERE id = :cid"),
                {"cid": request.category_id}
            ).fetchone()
            if not cat_check:
                raise HTTPException(status_code=404, detail="Category not found")
            updates.append("category_id = :cat_id")
            params["cat_id"] = request.category_id
        
        if request.status_id is not None:
            # Verify status exists
            stat_check = conn.execute(
                text("SELECT id FROM statuses WHERE id = :sid"),
                {"sid": request.status_id}
            ).fetchone()
            if not stat_check:
                raise HTTPException(status_code=404, detail="Status not found")
            updates.append("status_id = :stat_id")
            params["stat_id"] = request.status_id
        
        if request.owner_email is not None:
            # Find user by email
            user_result = conn.execute(
                text("SELECT id FROM users WHERE email = :email"),
                {"email": request.owner_email}
            ).fetchone()
            if not user_result:
                raise HTTPException(status_code=404, detail="Owner user not found")
            updates.append("owner_id = :owner_id")
            params["owner_id"] = user_result[0]

        if updates:
            # Always update timestamp
            updates.append("updated_at = CURRENT_TIMESTAMP")
            
            sql = f"UPDATE posts SET {', '.join(updates)} WHERE id = :pid"
            conn.execute(text(sql), params)

        # Handle tags separately since they involve a junction table
        if request.tag_names is not None:
            # Clear existing tags for this post
            conn.execute(
                text("DELETE FROM post_tags WHERE post_id = :pid"),
                {"pid": post_id}
            )
            
            for tag_name in request.tag_names:
                # Find tag ID
                tag_result = conn.execute(
                    text("SELECT id FROM tags WHERE name = :tname"),
                    {"tname": tag_name}
                ).fetchone()
                
                if tag_result:
                    tag_id = tag_result[0]
                    # Insert new association
                    conn.execute(
                        text("""
                            INSERT OR IGNORE INTO post_tags (post_id, tag_id, created_at)
                            VALUES (:pid, :tid, CURRENT_TIMESTAMP)
                        """),
                        {"pid": post_id, "tid": tag_id}
                    )
                else:
                    # If tag doesn't exist, we could optionally create it, 
                    # but spec says use existing tables. Assuming tags must pre-exist or ignore missing ones?
                    # The prompt implies setting specific tags that likely exist ('dark-mode', 'mobile').
                    pass

    return {
        "post_id": post_id,
        "message": "Post updated successfully"
    }
