# -*- coding: utf-8 -*-
"""数据库状态快照与行级 diff（通用工具，不依赖项目其他模块）。

用途：替代 verifier 阶段为每个任务手写的 verify_task。
verify_task 是「针对某个任务判断数据库对不对」，本模块是「如实报告数据库发生了什么变化」，
后者是确定性事实，由 LLM judge 结合任务与轨迹做判断。

设计要点：
  1. 精确性优先：diff 必须在完整值上计算。任何长度截断只能发生在输出阶段，
     绝不能参与比较（否则改了长文本中间几个字符会被漏判）。
  2. 无冗余：
       - delete 只输出主键（整行已在初始快照里）
       - insert 只输出新行（最终态只能从这里得知）
       - update 只输出变化列的 [before, after]（未变列两边快照都有）
       - 未变化的表完全不出现
  3. 类型敏感：1 与 1.0、1 与 '1' 视为不同；主键 key 用 (类型名, 值) 构造。
  4. 主键匹配：按 PRAGMA table_info 的 pk 序号排序建索引，复合主键天然支持。
     若 agent 改动了主键值，会先表现为 delete+insert，这时再做一次整行内容配对，
     能配上的标记为 update_pk，避免被误读成「删了一行又加了一行」。
"""

import json
import sqlite3


# ---------- 值规范化与比较 ----------

def _norm(value):
    """把 SQLite 取出的值规范化成可稳定比较、可 JSON 序列化的形式。

    bytes 统一转 hex 标记；float 用 repr 保留精度（避免 0.1+0.2 != 0.3 类误判）。
    """
    if isinstance(value, bytes):
        return {"__bytes_hex__": value.hex()}
    if isinstance(value, float):
        return {"__float__": repr(value)}
    return value


def _typed_key(value):
    """构造类型敏感的 hashable key：类型名 + 规范化值的 repr。

    这样 1(int) / 1.0(float) / '1'(str) 不会互相匹配。
    """
    norm = _norm(value)
    return (type(norm).__name__, repr(norm))


def _cell_equal(a, b):
    """单元格是否相等：先比类型标签，再比规范化值的 repr。"""
    return _typed_key(a) == _typed_key(b)


def _row_equal(row_a, row_b):
    if len(row_a) != len(row_b):
        return False
    return all(_cell_equal(x, y) for x, y in zip(row_a, row_b))


# ---------- schema 读取 ----------

def list_tables(conn):
    """返回所有业务表名（排除 sqlite_* 内部表），按名字排序。"""
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return [r[0] for r in rows]


def table_meta(conn, table):
    """读取表的列名、类型和主键。

    pk 按 PRAGMA 里的 pk 序号升序排列，保证复合主键顺序稳定。
    """
    info = conn.execute('PRAGMA table_info("%s")' % table).fetchall()
    columns = [r[1] for r in info]
    types = [(r[2] or "").upper() for r in info]
    pk_cols = [r[1] for r in sorted((r for r in info if r[5]), key=lambda r: r[5])]
    return {"columns": columns, "types": types, "pk": pk_cols}


# ---------- 快照 ----------

def snapshot_db(db_path, tables=None):
    """对数据库所有表执行 SELECT *，返回紧凑 JSON 快照。

    格式（每张表）：
        "表名": {"columns": [...], "types": [...], "pk": [...], "rows": [[v, ...], ...]}

    行用数组而非字典：列名只在表级出现一次，比每行重复列名省一半左右体积。
    本函数不做任何截断，输出长度即真实数据量。
    """
    conn = sqlite3.connect(db_path)
    try:
        return snapshot_conn(conn, tables)
    finally:
        conn.close()


def snapshot_conn(conn, tables=None):
    """snapshot_db 的连接版，便于复用已打开的连接。"""
    names = tables if tables is not None else list_tables(conn)
    snap = {}
    for name in names:
        meta = table_meta(conn, name)
        # Normalize BLOB cells before the snapshot leaves SQLite.  Ordinary
        # numeric/text values stay in their compact native JSON form so the
        # snapshot-size threshold measures actual data rather than type tags.
        rows = [[{"__bytes_hex__": value.hex()} if isinstance(value, bytes) else value
                 for value in r]
                for r in conn.execute('SELECT * FROM "%s"' % name)]
        snap[name] = {
            "columns": meta["columns"],
            "types": meta["types"],
            "pk": meta["pk"],
            "rows": rows,
        }
    return snap


def snapshot_size(snap):
    """快照序列化成紧凑 JSON 后的字符数。sample 阶段用它做体积门槛。"""
    return len(snapshot_text(snap))


def snapshot_text(snap, indent=None):
    """把快照序列化成文本。indent=None 时输出最紧凑形式。"""
    if indent is None:
        return json.dumps(snap, ensure_ascii=False, separators=(",", ":"), default=str)
    return json.dumps(snap, ensure_ascii=False, indent=indent, default=str)


# ---------- diff ----------

def _row_index(table, use_pk):
    """按主键（无主键则按整行内容）给一张表的行建索引。"""
    cols = table["columns"]
    pk = table["pk"]
    index = {}
    for row in table["rows"]:
        if use_pk:
            key = tuple(_typed_key(row[cols.index(c)]) for c in pk)
        else:
            key = repr([_typed_key(v) for v in row])
        index[key] = row
    return index


def _key_dict(row, cols, pk):
    """把行转成 {主键列: 值} 形式。"""
    if pk:
        return {c: _norm(row[cols.index(c)]) for c in pk}
    return {"__row__": [_norm(v) for v in row]}


def _non_pk_signature(row, cols, pk):
    """行内非主键列的签名，用于识别「同一行改了主键」。"""
    pk_set = set(pk)
    return [repr(_typed_key(v)) for c, v in zip(cols, row) if c not in pk_set]


def diff_snapshots(initial, final):
    """对比两个快照，返回结构化行级 diff。

    operation 形态：
      insert    : {"op": "insert", "key": {...}, "row": {列: 值}}
      delete    : {"op": "delete", "key": {...}}
      update    : {"op": "update", "key": {...}, "changes": {列: [before, after]}}
      update_pk : {"op": "update_pk", "before_key": {...}, "after_key": {...},
                   "changes": {列: [before, after]}}
    """
    initial_tables = set(initial.keys())
    final_tables = set(final.keys())
    created = sorted(final_tables - initial_tables)
    dropped = sorted(initial_tables - final_tables)

    added_columns = {}
    dropped_columns = {}
    tables = {}
    n_insert = n_update = n_delete = n_pk_update = 0

    for name in sorted(initial_tables & final_tables):
        t_init = initial[name]
        t_final = final[name]
        cols_init = t_init["columns"]
        cols_final = t_final["columns"]
        pk = t_final["pk"] or t_init["pk"]
        use_pk = bool(pk) and all(c in cols_init for c in pk) and all(c in cols_final for c in pk)

        lost_cols = [c for c in cols_init if c not in cols_final]
        new_cols = [c for c in cols_final if c not in cols_init]
        if lost_cols:
            dropped_columns[name] = lost_cols
        if new_cols:
            added_columns[name] = new_cols

        idx_init = _row_index(t_init, use_pk)
        idx_final = _row_index(t_final, use_pk)

        operations = []

        # 主键相同的行：比较内容找 update
        for key in idx_init.keys() & idx_final.keys():
            row_i = idx_init[key]
            row_f = idx_final[key]
            if _row_equal(row_i, row_f):
                continue
            changes = {}
            for c in cols_final:
                vf = row_f[cols_final.index(c)]
                if c not in cols_init:
                    changes[c] = [None, _norm(vf)]
                    continue
                vi = row_i[cols_init.index(c)]
                if not _cell_equal(vi, vf):
                    changes[c] = [_norm(vi), _norm(vf)]
            if changes:
                operations.append({
                    "op": "update",
                    "key": _key_dict(row_f, cols_final, pk),
                    "changes": changes,
                })
                n_update += 1

        # 未匹配的 delete / insert
        only_init = [idx_init[k] for k in idx_init.keys() - idx_final.keys()]
        only_final = [idx_final[k] for k in idx_final.keys() - idx_init.keys()]

        # 先把 delete+insert 配对成「主键被改了」
        still_del = []
        for row_i in only_init:
            matched_idx = None
            sig_i = _non_pk_signature(row_i, cols_init, pk)
            for j, cand in enumerate(only_final):
                if sig_i == _non_pk_signature(cand, cols_final, pk):
                    matched_idx = j
                    break
            if matched_idx is None:
                still_del.append(row_i)
                continue
            matched = only_final.pop(matched_idx)
            changes = {}
            for c in cols_final:
                vf = matched[cols_final.index(c)]
                vi = row_i[cols_init.index(c)] if c in cols_init else None
                if not _cell_equal(vi, vf):
                    changes[c] = [_norm(vi), _norm(vf)]
            operations.append({
                "op": "update_pk",
                "before_key": _key_dict(row_i, cols_init, pk),
                "after_key": _key_dict(matched, cols_final, pk),
                "changes": changes,
            })
            n_pk_update += 1

        for row_i in still_del:
            operations.append({"op": "delete", "key": _key_dict(row_i, cols_init, pk)})
            n_delete += 1

        for row_f in only_final:
            operations.append({
                "op": "insert",
                "key": _key_dict(row_f, cols_final, pk),
                "row": {c: _norm(v) for c, v in zip(cols_final, row_f)},
            })
            n_insert += 1

        if operations:
            tables[name] = {
                "pk": pk,
                "columns": cols_final,
                "operations": operations,
            }

    # agent 一般改不了 schema，防御性保留建表/删表报告
    for name in created:
        t = final[name]
        tables[name] = {
            "pk": t["pk"],
            "columns": t["columns"],
            "operations": [{"op": "create_table", "row_count": len(t["rows"])}],
        }
    for name in dropped:
        tables[name] = {
            "pk": initial[name]["pk"],
            "columns": initial[name]["columns"],
            "operations": [{"op": "drop_table", "row_count": len(initial[name]["rows"])}],
        }

    return {
        "summary": {
            "inserted": n_insert,
            "updated": n_update,
            "pk_updated": n_pk_update,
            "deleted": n_delete,
            "tables_changed": len(tables),
        },
        "tables": tables,
        "schema_changes": {
            "created_tables": created,
            "dropped_tables": dropped,
            "added_columns": added_columns,
            "dropped_columns": dropped_columns,
        },
    }


def diff_db(initial_db_path, final_db_path, initial_snap=None):
    """对两个 .db 文件做 diff。

    initial_snap: 可传入已算好的初始快照（RL 里同一任务的多个 rollout 复用），避免重复读取。
    """
    initial = initial_snap if initial_snap is not None else snapshot_db(initial_db_path)
    final = snapshot_db(final_db_path)
    return diff_snapshots(initial, final)


def diff_text(diff, indent=None):
    """把 diff 序列化成给 judge 看的文本。"""
    if indent is None:
        return json.dumps(diff, ensure_ascii=False, separators=(",", ":"), default=str)
    return json.dumps(diff, ensure_ascii=False, indent=indent, default=str)

