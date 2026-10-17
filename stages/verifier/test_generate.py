"""Verifier 单任务流程的离线测试，不调用模型。"""

import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from stages.verifier import generate


def _jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _make_db(path: Path, value: str) -> None:
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO items(id, value) VALUES (1, ?)", (value,))
        conn.commit()
    finally:
        conn.close()


class VerifierGenerateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env_dir = self.root / "envs" / "Demo_task_1"
        self.traj_dir = self.root / "trajectory" / "Demo_task_1"
        self.env_dir.mkdir(parents=True)
        self.traj_dir.mkdir(parents=True)
        _make_db(self.traj_dir / "initial.db", "before")
        _make_db(self.traj_dir / "final.db", "after")
        (self.traj_dir / "traj.json").write_text(
            json.dumps({
                "messages": [
                    {"role": "system", "content": "tools"},
                    {"role": "user", "content": "change the value"},
                    {"role": "assistant", "content": "<final_answer>done</final_answer>"},
                ]
            }),
            encoding="utf-8",
        )
        self.env_path = self.root / "env.jsonl"
        self.sample_path = self.root / "sample.jsonl"
        self.env_path.write_text(json.dumps({
            "scenario": "Demo", "task_id": "task_1", "task": "change the value",
            "env_dir": str(self.env_dir), "traj_dir": str(self.traj_dir),
            "db_path": str(self.root / "database.db"),
        }) + "\n", encoding="utf-8")
        self.sample_path.write_text(json.dumps({
            "scenario": "Demo", "task_id": "task_1", "task": "change the value",
        }) + "\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def config(self, resume=False):
        output = self.root / "out"
        return generate.Config(
            input_env=str(self.env_path),
            input_sample=str(self.sample_path),
            trajectory_root=str(self.root / "trajectory"),
            output=str(output / "gen.jsonl"),
            bad_output=str(output / "bad.jsonl"),
            ready_output=str(output / "ready.jsonl"),
            solvable_output=str(output / "solvable.jsonl"),
            unsolvable_output=str(output / "unsolvable.jsonl"),
            evidence_dir=str(output / "evidence"),
            model="offline-judge",
            max_concurrency=1,
            resume=resume,
        )

    def test_select_and_prepare_one_trajectory(self):
        config = self.config()
        config.pre_process()
        item = generate._select_tasks(config)[0]
        payload, metadata = generate._prepare_evidence(item, {
            "task": "change the value",
        })
        self.assertEqual(item["task_idx"], 0)
        self.assertEqual(payload["task"], "change the value")
        self.assertIn("initial_snapshot", payload["database_state"])
        self.assertIn("tables", payload["database_state"]["diff"])
        self.assertEqual(Path(metadata["trajectory_path"]).name, "traj.json")

    def test_single_result_is_mapped_to_solvable_and_resume_skips_it(self):
        config = self.config(resume=False)
        config.pre_process()
        with mock.patch.object(generate, "GPTClient", return_value=object()), mock.patch.object(
            generate, "llm_judge", return_value=("complete", {"classification": "complete"})
        ) as judge:
            results = generate.run(config)
            self.assertEqual(judge.call_count, 1)
        self.assertEqual(results[0]["task_id"], 0)
        self.assertEqual(len(_jsonl(Path(config.solvable_output))), 1)
        self.assertEqual(_jsonl(Path(config.unsolvable_output)), [])

        resumed = replace(config, resume=True)
        with mock.patch.object(generate, "GPTClient", side_effect=AssertionError("cache should avoid client")), \
                mock.patch.object(generate, "llm_judge", side_effect=AssertionError("cache should avoid judge")):
            generate.run(resumed)
        self.assertEqual(len(_jsonl(Path(resumed.output))), 1)

    def test_evidence_error_is_bad_and_does_not_enter_unsolvable(self):
        (self.traj_dir / "final.db").unlink()
        config = self.config()
        config.pre_process()
        with mock.patch.object(generate, "_judge_task", wraps=generate._judge_task) as mocked:
            results = generate.run(config)
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(results[0]["classification"], "evidence_error")
        self.assertEqual(_jsonl(Path(config.bad_output))[0]["stage"], "evidence")
        self.assertEqual(_jsonl(Path(config.unsolvable_output)), [])

    def test_duplicate_environment_key_is_recorded_as_evidence_error(self):
        with self.env_path.open("a", encoding="utf-8") as output:
            output.write(self.env_path.read_text(encoding="utf-8"))
        config = self.config()
        config.pre_process()
        items = generate._select_tasks(config)
        self.assertEqual(len(items), 1)
        self.assertTrue(any("重复包含任务" in error for error in items[0]["input_errors"]))

    def test_judge_classes_map_to_rl_lists(self):
        config = self.config()
        config.pre_process()
        item = generate._select_tasks(config)[0]
        sample = {"task": "change the value"}
        expected = {
            "complete": "completed",
            "incomplete": "not_completed",
            "agent_error": "not_completed",
            "server_error": "bad",
            "judge_error": "bad",
        }
        with mock.patch.object(generate, "GPTClient", return_value=object()):
            for classification, status in expected.items():
                with self.subTest(classification=classification), tempfile.TemporaryDirectory() as cache:
                    config.evidence_dir = cache
                    with mock.patch.object(
                        generate, "llm_judge",
                        return_value=(classification, {"classification": classification}),
                    ):
                        result = generate._judge_task(item, sample, config)
                    self.assertEqual(result["status"], status)
                    self.assertEqual(result["completed"], True if status == "completed" else (
                        False if status == "not_completed" else None
                    ))

    def test_changed_database_invalidates_cached_judgement(self):
        config = self.config(resume=True)
        config.pre_process()
        with mock.patch.object(generate, "GPTClient", return_value=object()), mock.patch.object(
            generate, "llm_judge", return_value=("complete", {"classification": "complete"})
        ) as judge:
            generate.run(config)
            self.assertEqual(judge.call_count, 1)

        _make_db(self.traj_dir / "final.db", "changed-again")
        with mock.patch.object(generate, "GPTClient", return_value=object()), mock.patch.object(
            generate, "llm_judge", return_value=("incomplete", {"classification": "incomplete"})
        ) as judge:
            results = generate.run(config)
            self.assertEqual(judge.call_count, 1)
        self.assertEqual(results[0]["classification"], "incomplete")


if __name__ == "__main__":
    unittest.main()
