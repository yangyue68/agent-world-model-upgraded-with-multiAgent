"""Publish successful task artifacts and remove only diagnostic workspaces."""
from pathlib import Path
import json
import shutil
from .storage import portable


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def publish(directory, accepted, config, stamp):
    env_target = Path(config.envs_dir) / stamp
    traj_target = Path(config.trajectory_dir) / stamp
    env_target.mkdir(parents=True, exist_ok=True)
    traj_target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(directory) / "main.py", env_target / "main.py")
    source_meta = Path(directory) / "environment.json"
    if source_meta.is_file():
        shutil.copy2(source_meta, env_target / "environment.json")
    if accepted:
        source = accepted[0]
        shutil.copy2(source / "traj.json", traj_target / "traj.json")
        for name in ("initial.db", "final.db"):
            if (source / name).is_file():
                shutil.copy2(source / name, traj_target / name)
    project_root = Path(config.envs_dir).resolve().parents[2]
    return portable(env_target, project_root), portable(traj_target, project_root)


def cleanup(path):
    if path:
        shutil.rmtree(path, ignore_errors=True)
