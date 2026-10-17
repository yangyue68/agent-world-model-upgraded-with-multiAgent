import os
from datetime import datetime
from typing import Literal
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

DATABASE_PATH = os.environ.get("DATABASE_PATH")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database Models
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
    style = Column(String)
    is_public = Column(Integer, nullable=False, default=0)
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
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

# Pydantic Models
class RecipeResponse(BaseModel):
    id: int
    title: str
    is_public: bool

class CommentResponse(BaseModel):
    id: int
    recipe_id: int
    user_id: int
    body: str

# FastAPI App
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.put("/recipes/{recipe_id}/publish", response_model=RecipeResponse, operation_id="publish_recipe")
def publish_recipe(recipe_id: int, db: Session = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    db_recipe = db.query(Recipe).filter(Recipe.id == recipe_id).first()
    if not db_recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    
    if db_recipe.user_id != current_user_id:
        raise HTTPException(status_code=403, detail="Only the owner can publish this recipe")
    
    db_recipe.is_public = 1
    db.commit()
    db.refresh(db_recipe)
    
    return db_recipe

@app.post("/recipes/{recipe_id}/comments", response_model=CommentResponse, operation_id="add_comment_to_recipe")
def add_comment_to_recipe(recipe_id: int, body: str, db: Session = Depends(get_db)):
    current_user_id = int(os.environ.get("AWM_CURRENT_USER_ID"))
    
    db_recipe = db.query(Recipe).filter(Recipe.id == recipe_id).first()
    if not db_recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    
    db_comment = Comment(
        recipe_id=recipe_id,
        user_id=current_user_id,
        body=body
    )
    
    db.add(db_comment)
    db.commit()
    db.refresh(db_comment)
    
    return db_comment
