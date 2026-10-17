"""Publish successful task artifacts. Directory names are deterministic (scenario+task);
republishing the same task overwrites the previous artifact instead of accumulating orphans."""
from pathlib import Path
import json
import shutil
from .storage import portable


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def publish(directory, accepted, config, name):
    env_target = Path(config.envs_dir) / name
    traj_target = Path(config.trajectory_dir) / name
    # 重跑同一任务直接覆盖旧目录，保证 envs/trajectory 与 jsonl 记录数长期一致。
    shutil.rmtree(env_target, ignore_errors=True)
    shutil.rmtree(traj_target, ignore_errors=True)
    env_target.mkdir(parents=True, exist_ok=True)
    traj_target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(directory) / "main.py", env_target / "main.py")
    source_meta = Path(directory) / "environment.json"
    if source_meta.is_file():
        shutil.copy2(source_meta, env_target / "environment.json")
    if accepted:
        source = accepted[0]
        shutil.copy2(source / "traj.json", traj_target / "traj.json")
        for fname in ("initial.db", "final.db"):
            if (source / fname).is_file():
                shutil.copy2(source / fname, traj_target / fname)
    project_root = Path(config.envs_dir).resolve().parents[2]
    return portable(env_target, project_root), portable(traj_target, project_root)
