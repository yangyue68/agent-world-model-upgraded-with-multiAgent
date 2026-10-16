# -*- coding: utf-8 -*-
"""Print exact row-level diffs for saved rollout trajectories.

This is a read-only diagnostic.  It deliberately has no dependency on the
removed task-specific verifier code: ``db_diff`` is the single source of truth
for the database transition.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

_MA_ROOT = Path(__file__).resolve().parent.parent
if str(_MA_ROOT) not in sys.path:
    sys.path.insert(0, str(_MA_ROOT))

from lib.db_diff import diff_db, diff_text, snapshot_db, snapshot_size  # noqa: E402


DEFAULT_TRAJECTORY_ROOT = _MA_ROOT / "output" / "env" / "trajectory"


def iter_trajectories(root: Path):
    if not root.is_dir():
        return
    for task_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for probe_dir in sorted(p for p in task_dir.iterdir() if p.is_dir()):
            initial = probe_dir / "initial.db"
            final = probe_dir / "final.db"
            traj = probe_dir / "traj.json"
            if initial.exists() and final.exists():
                yield task_dir.name, probe_dir.name, initial, final, traj


def main() -> None:
    parser = argparse.ArgumentParser(description="Print exact db_diff results for saved rollout trajectories")
    parser.add_argument("--traj-root", default=str(DEFAULT_TRAJECTORY_ROOT))
    parser.add_argument("--max-tasks", type=int, default=None)
    parser.add_argument("--indent", type=int, default=2)
    args = parser.parse_args()

    total = 0
    for total, (task_name, probe_name, initial, final, traj_path) in enumerate(
        iter_trajectories(Path(args.traj_root)), 1
    ):
        if args.max_tasks is not None and total > args.max_tasks:
            break
        print("\n" + "=" * 100)
        print(f"task={task_name} probe={probe_name}")
        try:
            initial_snapshot = snapshot_db(str(initial))
            diff = diff_db(str(initial), str(final), initial_snap=initial_snapshot)
        except Exception as exc:
            print(f"diff failed: {type(exc).__name__}: {exc}")
            continue

        print(
            "initial_snapshot_chars=%d tables=%d rows=%d"
            % (snapshot_size(initial_snapshot), len(initial_snapshot),
               sum(len(table["rows"]) for table in initial_snapshot.values()))
        )
        print("summary=" + json.dumps(diff["summary"], ensure_ascii=False))
        print(diff_text(diff, indent=args.indent or None))
        if traj_path.exists():
            trajectory = json.loads(traj_path.read_text(encoding="utf-8"))
            print("final_answer=" + str(trajectory.get("final_answer") or "")[:600])

    print("\nchecked=%d trajectories" % min(total, args.max_tasks or total))


if __name__ == "__main__":
    main()
