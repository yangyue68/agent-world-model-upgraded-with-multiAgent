import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# 运行时通过 DATABASE_PATH 注入 SQLite 连接串；未设置时回退到本地文件（仅供独立运行）。
DATABASE_PATH = os.getenv("DATABASE_PATH", "sqlite:///./local.db")

_connect_args = {"check_same_thread": False} if DATABASE_PATH.startswith("sqlite") else {}

engine = create_engine(DATABASE_PATH, connect_args=_connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI 依赖：请求级 session，确保 finally 一定 close。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()