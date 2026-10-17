import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI(title="BrightPath Financial Wellness API")


class SplitItem(BaseModel):
    category_id: int
    amount: float


class OriginalTransactionOut(BaseModel):
    id: int
    description: str
    amount: float


class SubTransactionOut(BaseModel):
    id: int
    category_id: int
    amount: float
    description: str


class SplitResponse(BaseModel):
    original_transaction: OriginalTransactionOut
    sub_transactions: list[SubTransactionOut]


class CategoryOut(BaseModel):
    id: int
    name: str
    parent_category_id: int | None
    type: str


class TransactionDetailsOut(BaseModel):
    id: int
    user_id: int
    account_id: int
    category_id: int | None
    parent_transaction_id: int | None
    description: str
    amount: float
    transaction_date: str
    type: str
    is_split: bool
    created_at: str
    updated_at: str


def _get_current_user_id() -> int:
    raw = os.environ.get("AWM_CURRENT_USER_ID")
    if not raw:
        raise HTTPException(status_code=401, detail="Missing current user context")
    try:
        return int(raw)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid current user context")


@app.post("/transactions/{transaction_id}/split", response_model=SplitResponse, operation_id="split_transaction")
def split_transaction(
    transaction_id: int = Path(..., ge=1),
    splits: list[SplitItem] = ...,
):
    user_id = _get_current_user_id()

    with engine.begin() as conn:
        # Fetch the original transaction and verify ownership + unsplit status
        row = conn.execute(
            text("""
                SELECT id, user_id, account_id, category_id, parent_transaction_id,
                       description, amount, transaction_date, type, is_split,
                       created_at, updated_at
                FROM transactions
                WHERE id = :tid AND user_id = :uid
            """),
            {"tid": transaction_id, "uid": user_id},
        ).fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="Transaction not found")

        tx_id, tx_user_id, tx_account_id, tx_category_id, tx_parent_id, \
            tx_description, tx_amount, tx_date, tx_type, tx_is_split, \
            tx_created_at, tx_updated_at = row

        if tx_is_split == 1:
            raise HTTPException(status_code=400, detail="Transaction has already been split")

        if tx_parent_id is not None:
            raise HTTPException(status_code=400, detail="Cannot split a sub-transaction")

        # Validate that the sum of absolute amounts matches the original absolute amount
        total_abs_original = abs(tx_amount)
        total_abs_splits = sum(abs(s.amount) for s in splits)

        if len(splits) < 2:
            raise HTTPException(status_code=400, detail="At least two splits are required")

        if round(total_abs_splits, 2) != round(total_abs_original, 2):
            raise HTTPException(
                status_code=400,
                detail=f"Sum of split amounts ({total_abs_splits}) does not match original amount ({tx_amount})",
            )

        # Verify all category_ids belong to the same user and exist
        cat_ids = [s.category_id for s in splits]
        placeholders = ", ".join(f":cid_{i}" for i in range(len(cat_ids)))
        params = {f"cid_{i}": cid for i, cid in enumerate(cat_ids)}
        params["uid"] = user_id

        cats_rows = conn.execute(
            text(f"""
                SELECT id, name, type
                FROM categories
                WHERE id IN ({placeholders}) AND user_id = :uid
            """),
            params,
        ).fetchall()

        valid_cat_map = {r.id: r.name for r in cats_rows}
        for cid in cat_ids:
            if cid not in valid_cat_map:
                raise HTTPException(status_code=400, detail=f"Category {cid} not found or not owned by user")

        # Mark original transaction as split
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            text("""
                UPDATE transactions
                SET is_split = 1, updated_at = :now
                WHERE id = :tid
            """),
            {"tid": tx_id, "now": now_str},
        )

        # Insert sub-transactions
        sub_tx_results = []
        sign = -1.0 if tx_amount < 0 else 1.0

        for s in splits:
            # Preserve the sign convention of the original transaction
            signed_amount = sign * abs(s.amount)
            result = conn.execute(
                text("""
                    INSERT INTO transactions (user_id, account_id, category_id, parent_transaction_id,
                                              description, amount, transaction_date, type, is_split,
                                              created_at, updated_at)
                    VALUES (:uid, :aid, :cid, :pid, :desc, :amt, :tdate, :ttype, 0, :now, :now)
                """),
                {
                    "uid": tx_user_id,
                    "aid": tx_account_id,
                    "cid": s.category_id,
                    "pid": tx_id,
                    "desc": f"{tx_description} - {valid_cat_map[s.category_id]}",
                    "amt": signed_amount,
                    "tdate": tx_date,
                    "ttype": tx_type,
                    "now": now_str,
                },
            )
            new_id = result.lastrowid
            sub_tx_results.append({
                "id": new_id,
                "category_id": s.category_id,
                "amount": signed_amount,
                "description": f"{tx_description} - {valid_cat_map[s.category_id]}",
            })

        original_out = OriginalTransactionOut(
            id=tx_id,
            description=tx_description,
            amount=float(tx_amount),
        )
        subs_out = [SubTransactionOut(**st) for st in sub_tx_results]

        return SplitResponse(original_transaction=original_out, sub_transactions=subs_out)


@app.get("/transactions/{transaction_id}", response_model=TransactionDetailsOut, operation_id="get_transaction_details")
def get_transaction_details(transaction_id: int = Path(..., ge=1)):
    user_id = _get_current_user_id()

    with engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT id, user_id, account_id, category_id, parent_transaction_id,
                       description, amount, transaction_date, type, is_split,
                       created_at, updated_at
                FROM transactions
                WHERE id = :tid AND user_id = :uid
            """),
            {"tid": transaction_id, "uid": user_id},
        ).fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="Transaction not found")

        tx_id, tx_user_id, tx_account_id, tx_category_id, tx_parent_id, \
            tx_description, tx_amount, tx_date, tx_type, tx_is_split, \
            tx_created_at, tx_updated_at = row

        return TransactionDetailsOut(
            id=tx_id,
            user_id=tx_user_id,
            account_id=tx_account_id,
            category_id=tx_category_id,
            parent_transaction_id=tx_parent_id,
            description=tx_description,
            amount=float(tx_amount),
            transaction_date=str(tx_date),
            type=tx_type,
            is_split=bool(tx_is_split),
            created_at=str(tx_created_at),
            updated_at=str(tx_updated_at),
        )


@app.get("/categories", response_model=list[CategoryOut], operation_id="list_categories")
def list_categories():
    user_id = _get_current_user_id()

    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT id, name, parent_category_id, type
                FROM categories
                WHERE user_id = :uid
                ORDER BY id
            """),
            {"uid": user_id},
        ).fetchall()

        return [
            CategoryOut(
                id=r.id,
                name=r.name,
                parent_category_id=r.parent_category_id,
                type=r.type,
            )
            for r in rows
        ]
