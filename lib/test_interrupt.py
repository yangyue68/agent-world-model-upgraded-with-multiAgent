"""离线测试取消、线程池退出和子进程清理，不调用真实 LLM。

运行：python -m unittest lib.test_interrupt -v
Windows 子测试使用隐藏的独立控制台，并发送真实 CTRL_C_EVENT。
"""

import asyncio
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from lib.interrupt import (
    InterruptibleThreadPoolExecutor as Pool, as_completed, cancellable,
    interruptible_sleep, managed_popen, run_cli, stop_requested,
)

ROOT = Path(__file__).resolve().parents[1]


def _send_interrupt():
    if os.name == "nt":
        import ctypes
        # 仅发送给本测试进程的独立控制台，不影响父 PowerShell。
        if not ctypes.windll.kernel32.GenerateConsoleCtrlEvent(0, 0):
            raise ctypes.WinError()
    else:
        os.kill(os.getpid(), signal.SIGINT)


def _arm_interrupt(delay=0.3):
    timer = threading.Timer(delay, _send_interrupt)
    timer.daemon = True
    timer.start()


def _worker(mode, directory):
    directory = Path(directory)

    def mark(name):
        (directory / name).write_text("ok", encoding="utf-8")

    async def async_job():
        try:
            await asyncio.sleep(30)
        finally:
            mark("async_closed")

    def job():
        try:
            if mode in {"stubborn", "twice", "child"}:
                time.sleep(30)  # 故意模拟不配合取消的第三方调用。
            else:
                interruptible_sleep(30)
        except Exception:
            mark("bad_task")
        finally:
            mark("worker_closed")

    def main():
        if mode == "child":
            child_code = (
                "import json,socket,sys,time; from pathlib import Path; "
                "s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen(); "
                "Path(sys.argv[1]).write_text(json.dumps(s.getsockname()[1])); time.sleep(30)"
            )
            managed_popen([sys.executable, "-c", child_code, str(directory / "port")],
                          start_new_session=True)
            deadline = time.monotonic() + 5
            while not (directory / "port").exists():
                if time.monotonic() > deadline:
                    raise RuntimeError("测试子进程启动超时")
                interruptible_sleep(0.02)
        if mode == "gpt":
            from lib.gpt import GPTClient

            class FakeClient(GPTClient):
                def _build_client(self):
                    from types import SimpleNamespace

                    async def close():
                        mark("client_closed")

                    self._client = SimpleNamespace(close=close)

                async def _call_async(self, *args, **kwargs):
                    _arm_interrupt()
                    return await async_job()

            client = FakeClient.__new__(FakeClient)

            async def caller():
                # 从已有事件循环调用同步 API，覆盖 GPTClient 内部的嵌套线程池。
                return client.request([])

            asyncio.run(caller())
            return

        _arm_interrupt()
        if mode == "twice":
            _arm_interrupt(0.6)
        if mode == "async":
            asyncio.run(cancellable(async_job()))
            return

        def nested_job():
            with Pool(max_workers=2) as inner:
                for future in as_completed([inner.submit(job) for _ in range(20)]):
                    future.result()

        with Pool(max_workers=1) as pool:
            futures = [pool.submit(nested_job if mode == "nested" else job)]
            futures += [pool.submit(mark, "queued_ran") for _ in range(20)]
            for future in as_completed(futures):
                future.result()

    run_cli(main, grace_seconds=30 if mode == "twice" else 1)


class InterruptTests(unittest.TestCase):
    def run_worker(self, mode):
        with tempfile.TemporaryDirectory(prefix="awm_interrupt_") as directory:
            options = {}
            if os.name == "nt":
                startup = subprocess.STARTUPINFO()
                startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startup.wShowWindow = subprocess.SW_HIDE
                options = {"creationflags": subprocess.CREATE_NEW_CONSOLE, "startupinfo": startup}
            started = time.monotonic()
            proc = subprocess.Popen(
                [sys.executable, "-m", "lib.test_interrupt", "--worker", mode, directory],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"}, **options,
            )
            try:
                stdout, stderr = proc.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                   capture_output=True, timeout=3, creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    proc.kill()
                proc.communicate(timeout=3)
                self.fail(f"{mode}: Ctrl+C 后 15 秒仍未退出")
            elapsed = time.monotonic() - started
            self.assertEqual(proc.returncode, 130, stdout + stderr)
            self.assertNotIn("Traceback", stderr)
            self.assertNotIn("was never awaited", stderr)
            self.assertNotIn("never retrieved", stderr)
            files = {p.name: p.read_text(encoding="utf-8") for p in Path(directory).iterdir()}
            self.assertNotIn("queued_ran", files)
            self.assertNotIn("bad_task", files)
            return elapsed, files, stderr

    def test_cooperative_pool_and_queued_jobs(self):
        _, files, _ = self.run_worker("cooperative")
        self.assertIn("worker_closed", files)

    def test_nested_pool(self):
        _, files, _ = self.run_worker("nested")
        self.assertIn("worker_closed", files)

    def test_async_cleanup(self):
        _, files, _ = self.run_worker("async")
        self.assertIn("async_closed", files)

    def test_gpt_client_cancels_and_closes(self):
        _, files, _ = self.run_worker("gpt")
        self.assertIn("async_closed", files)
        self.assertIn("client_closed", files)

    def test_stuck_worker_has_bounded_exit(self):
        elapsed, _, _ = self.run_worker("stubborn")
        self.assertLess(elapsed, 6)

    def test_second_interrupt_forces_exit(self):
        elapsed, _, _ = self.run_worker("twice")
        self.assertLess(elapsed, 6)  # 首次中断的宽限期在此测试中为 30 秒。

    def test_child_releases_listening_port(self):
        _, files, _ = self.run_worker("child")
        port = json.loads(files["port"])
        with socket.socket() as sock:
            sock.settimeout(1)
            self.assertNotEqual(sock.connect_ex(("127.0.0.1", port)), 0)

    def test_normal_results_and_caught_errors_do_not_cancel_stage(self):
        previous = signal.getsignal(signal.SIGINT)

        def main():
            with Pool(max_workers=2) as pool:
                self.assertEqual(sorted(f.result() for f in as_completed(
                    [pool.submit(lambda value: value * 2, i) for i in range(4)])), [0, 2, 4, 6])
            try:
                with Pool(max_workers=1) as pool:
                    pool.submit(lambda: 1 / 0).result()
            except ZeroDivisionError:
                pass
            self.assertFalse(stop_requested())
            return "complete"

        self.assertEqual(run_cli(main), "complete")
        self.assertIs(signal.getsignal(signal.SIGINT), previous)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        _worker(sys.argv[2], sys.argv[3])
    else:
        unittest.main()
