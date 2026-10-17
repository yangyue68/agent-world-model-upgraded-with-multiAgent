"""单文件 FastAPI 环境的独立进程运行时，生成探针与 RL 共用。

通过管道发送工具请求，在子进程内经 TestClient 执行完整 ASGI 请求链。
每次启动复制初始库，不修改上游数据库；不共享模块、连接和环境变量。
"""
import json
from contextlib import closing
import os
from pathlib import Path
import queue
import re
import sqlite3
import subprocess
import sys
import threading
import time

from lib.interrupt import check_cancelled, managed_popen, release_process
from lib.environment_context import CURRENT_USER_ID
from lib.tools import resolve_project_path

ROOT = Path(__file__).resolve().parents[1]
ENV_VERSION = "single-file-v1"

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _safe_resolve(raw, root=ROOT) -> Path:
    """把产物里的路径解析成本机可用的绝对路径，并拦住跨平台污染。

    直接 ``Path(raw).resolve()`` 对 ``E:\\...\\X.db`` 这种 Windows 路径是灾难：
    Linux 上 ``os.path.isabs`` 判它为相对路径，``resolve()`` 会拼上 CWD，产出
    ``/root/autodl-tmp/<repo>/E:/.../X.db`` —— 一个永远不存在的垃圾路径，而且
    报错信息完全看不出是路径污染（RL rollout 曾因此整批 FileNotFoundError）。

    这里统一走 resolve_project_path 按项目锚点重定位。注意**不在这里抛错**：
    Windows 本机开发时产物里出现盘符路径是合法的，且 env_dir 是目录、
    存在性判定方式与 .db 文件不同。污染诊断交给 describe_missing_path()。
    """
    return Path(resolve_project_path(str(raw).strip(), root)).resolve()


def describe_missing_path(raw, resolved) -> str:
    """为「解析后仍然找不到」的路径生成诊断信息，区分普通缺失与跨平台污染。"""
    text = str(raw).strip()
    if _WINDOWS_DRIVE_RE.match(text):
        return (
            f"{resolved}（产物里存的是 Windows 绝对路径 {text!r}，跨平台拷贝后失效）。"
            f"请运行 python -m ma_upgrade.scripts.normalize_artifact_paths 把产物路径改回相对路径。"
        )
    return f"{resolved}（产物里的原始路径：{text!r}）"


def copy_database(source, target):
    # source 来自上游产物（db_path 字段），必须做跨平台防御；
    # target 由本模块用 output_dir 拼出，不可能被产物污染，直接 resolve。
    raw_source = source
    source, target = _safe_resolve(source), Path(target).resolve()
    if source == target:
        raise ValueError("初始库与工作库不能相同")
    if not source.is_file():
        raise FileNotFoundError("初始数据库不存在：" + describe_missing_path(raw_source, source))
    target.parent.mkdir(parents=True, exist_ok=True)
    # backup 同时包含 WAL 中已经提交的数据。
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(target)) as dst:
            src.backup(dst)


class SingleFileEnv:
    def __init__(self, scenario, env_dir, output_dir, initial_db_source, timeout=60):
        self.scenario = scenario
        # env_dir 同样来自产物（gen_envs_ma.jsonl 的 env_dir 字段），走同一套防御。
        self.env_dir = str(_safe_resolve(env_dir))
        self.output_dir = str(Path(output_dir).resolve())
        self.initial_db_source = initial_db_source
        self.initial_db_path = str(Path(self.output_dir) / "initial.db")
        self.db_file_path = str(Path(self.output_dir) / "final.db")
        self.timeout = timeout
        self.process = None
        self._log = None
        self._reader = None
        self._responses = queue.Queue()
        self._lock = threading.Lock()
        self._tools = []
        self.openapi = {}
        self.events = []

    def start(self):
        check_cancelled()
        copy_database(self.initial_db_source, self.initial_db_path)
        copy_database(self.initial_db_path, self.db_file_path)
        directory = Path(self.output_dir)
        self._log = (directory / "runtime.log").open("w", encoding="utf-8")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        env["DATABASE_PATH"] = "sqlite:///" + self.db_file_path.replace("\\", "/")
        env["AWM_CURRENT_USER_ID"] = str(CURRENT_USER_ID)
        try:
            self.process = managed_popen(
                [sys.executable, "-u", "-m", "lib.single_file_worker", self.env_dir],
                cwd=str(ROOT), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=self._log, text=True, encoding="utf-8", bufsize=1,
            )
            def read():
                try:
                    for line in self.process.stdout:
                        self._responses.put(line)
                finally:
                    self._responses.put(None)
            self._reader = threading.Thread(target=read, daemon=True)
            self._reader.start()
            result = self._receive()
            if not result.get("ok"):
                raise ValueError(result.get("error", "环境加载失败"))
            self.openapi, self._tools = result["openapi"], result["tools"]
        except BaseException:
            self.close()
            raise

    def _receive(self):
        deadline = time.monotonic() + self.timeout
        while True:
            check_cancelled()
            if time.monotonic() >= deadline:
                raise TimeoutError(f"环境进程超时（{self.timeout}s），详见 {self.output_dir}/runtime.log")
            try:
                line = self._responses.get(timeout=0.1)
            except queue.Empty:
                continue
            if line is None:
                raise RuntimeError(f"环境进程异常退出，详见 {self.output_dir}/runtime.log")
            return json.loads(line)

    def list_tools(self):
        return self._tools

    def call_tool(self, tool_name, arguments):
        with self._lock:
            check_cancelled()
            try:
                self.process.stdin.write(json.dumps({"name": tool_name, "arguments": arguments}, ensure_ascii=False) + "\n")
                self.process.stdin.flush()
                event = self._receive()
            except BaseException:
                self.close()
                raise
            self.events.append(event)
            return event["result"]

    def close(self):
        proc = self.process
        if proc is not None:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
            if self._reader:
                self._reader.join(timeout=1)
            for stream in (proc.stdin, proc.stdout):
                if stream:
                    stream.close()
            release_process(proc)
            self.process = None
        if self._log is not None:
            self._log.close()
            self._log = None
