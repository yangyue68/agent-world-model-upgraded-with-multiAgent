import os

from fastapi import FastAPI

# 导入 models 以把所有 ORM 表注册到 Base.metadata（必须在 create_all 之前）。
import models  # noqa: F401
from database import Base, engine

Base.metadata.create_all(bind=engine)

app = FastAPI(title='AdoptPaws')

from routers.group_001 import router as router_group_001

app.include_router(router_group_001)

@app.get("/healthz")
def healthz():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host=host, port=port)