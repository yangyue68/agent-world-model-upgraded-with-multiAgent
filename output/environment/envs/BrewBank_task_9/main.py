import os
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, ForeignKey, CheckConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    display_name = Column(String)
    bio = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class Recipe(Base):
    __tablename__ = "recipes"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text)
    is_public = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        CheckConstraint("is_public IN (0, 1)", name="check_is_public"),
    )

class Comment(Base):
    __tablename__ = "comments"
    id = Column(Integer, primary_key=True, index=True)
    recipe_id = Column(Integer, ForeignKey("recipes.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    parent_comment_id = Column(Integer, ForeignKey("comments.id"))
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

app = FastAPI()

class AddRecipeCommentRequest(BaseModel):
    body: str

class ReplyToCommentRequest(BaseModel):
    body: str

class CommentResponse(BaseModel):
    id: int
    recipe_id: int
    user_id: int
    body: str
    created_at: str

class ReplyResponse(BaseModel):
    id: int
    recipe_id: int
    user_id: int
    parent_comment_id: int
    body: str
    created_at: str

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/recipes/{recipe_id}/comments", response_model=CommentResponse, operation_id="add_recipe_comment")
def add_recipe_comment(recipe_id: int, request: AddRecipeCommentRequest, db=None):
    if db is None:
        db = next(get_db())
    
    # Verify recipe exists and is public
    recipe = db.query(Recipe).filter(Recipe.id == recipe_id, Recipe.is_public == 1).first()
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    
    # Get current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # Create new comment
    comment = Comment(
        recipe_id=recipe_id,
        user_id=current_user_id,
        body=request.body
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    
    return CommentResponse(
        id=comment.id,
        recipe_id=comment.recipe_id,
        user_id=comment.user_id,
        body=comment.body,
        created_at=comment.created_at.isoformat() + "Z"
    )

@app.post("/comments/{comment_id}/replies", response_model=ReplyResponse, operation_id="reply_to_comment")
def reply_to_comment(comment_id: int, request: ReplyToCommentRequest, db=None):
    if db is None:
        db = next(get_db())
    
    # Verify original comment exists
    original_comment = db.query(Comment).filter(Comment.id == comment_id).first()
    if not original_comment:
        raise HTTPException(status_code=404, detail="Original comment not found")
    
    # Get current user from environment
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID", 0))
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # Create reply
    reply = Comment(
        recipe_id=original_comment.recipe_id,
        user_id=current_user_id,
        parent_comment_id=comment_id,
        body=request.body
    )
    db.add(reply)
    db.commit()
    db.refresh(reply)
    
    return ReplyResponse(
        id=reply.id,
        recipe_id=reply.recipe_id,
        user_id=reply.user_id,
        parent_comment_id=reply.parent_comment_id,
        body=reply.body,
        created_at=reply.created_at.isoformat() + "Z"
    )
