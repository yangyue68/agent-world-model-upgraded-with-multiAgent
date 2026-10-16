"""各阶段共用的协作取消：Ctrl+C 停止工作，超时或再次中断时强制退出。

只在 run_cli() 内安装信号处理器，导入本模块不改变进程行为。
线程不能被安全地强杀；线程池、网络请求和长循环通过取消检查退出。
默认给清理 5 秒，仍阻塞的第三方调用由 CLI 的看门狗兜底结束进程。
"""

import asyncio
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor as _ThreadPoolExecutor, wait as _wait


class StageCancelled(BaseException):
    """用户取消，不应被各阶段的 except Exception 当作坏任务或请求失败。"""


_active = None
_POLL = 0.1


def stop_requested() -> bool:
    return _active is not None and _active.stopping


def check_cancelled() -> None:
    if stop_requested():
        raise StageCancelled()


def interruptible_sleep(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while True:
        check_cancelled()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(_POLL, remaining))


class _RunControl:
    def __init__(self, grace_seconds):
        self.grace_seconds = grace_seconds
        self.stopping = False
        self.force = False
        self.deadline = None
        self.finished = threading.Event()
        self.lock = threading.RLock()
        self.futures = set()
        self.processes = {}

    def request_stop(self):
        # 信号回调只设置标记，不打印日志、不获取线程池/文件锁、不打断写盘。
        if not self.stopping:
            self.deadline = time.monotonic() + self.grace_seconds
            self.stopping = True

    def on_signal(self, signum, frame):
        if self.stopping:
            self.force = True
        else:
            self.request_stop()

    def track_future(self, future):
        with self.lock:
            self.futures.add(future)
        future.add_done_callback(self._forget_future)

    def _forget_future(self, future):
        with self.lock:
            self.futures.discard(future)

    def drain(self):
        while True:
            with self.lock:
                pending = list(self.futures)
            if not pending:
                return
            if self.stopping:
                for future in pending:
                    future.cancel()
            # Windows 上不做无限期 Event.wait/future.result/thread.join。
            _wait(pending, timeout=_POLL)

    def watchdog(self):
        notified = False
        while not self.finished.wait(_POLL):
            if not self.stopping:
                continue
            if not notified:
                _notice("收到中断：停止新任务并取消请求，正在清理；再次 Ctrl+C 可强制退出。")
                notified = True
            if self.force or time.monotonic() >= self.deadline:
                _notice("清理超时或再次收到中断，强制退出（退出码 130）。")
                self.kill_children()
                os._exit(130)

    def kill_children(self):
        with self.lock:
            children = [(proc, session) for proc, session in self.processes.items()
                        if proc.poll() is None]
        if os.name == "nt" and children:
            # 合并为一次调用，避免大量子进程令强制退出逐个等待超时。
            command = ["taskkill", "/T", "/F"]
            for proc, _ in children:
                command.extend(["/PID", str(proc.pid)])
            try:
                subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=2, creationflags=subprocess.CREATE_NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired):
                pass
        for proc, new_session in children:
            if proc.poll() is not None:
                continue
            try:
                if os.name != "nt" and new_session:
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                except OSError:
                    pass


def _notice(message):
    # 看门狗不能等待可能被工作线程持有的 logging/stdout 锁。
    try:
        os.write(2, (message + "\n").encode(sys.stderr.encoding or "utf-8", errors="replace"))
    except (OSError, AttributeError):
        pass


def run_cli(main, *args, grace_seconds: float = 5.0, **kwargs):
    """运行命令行入口；取消返回退出码 130，正常异常仍原样报告。"""
    global _active
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("run_cli 必须在主线程调用")
    if _active is not None:
        return main(*args, **kwargs)
    control = _RunControl(grace_seconds)
    _active = control
    signals = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGBREAK"):
        signals.append(signal.SIGBREAK)
    previous = {sig: signal.getsignal(sig) for sig in signals}
    for sig in signals:
        signal.signal(sig, control.on_signal)
    watcher = threading.Thread(target=control.watchdog, name="stage-interrupt", daemon=True)
    watcher.start()
    try:
        result = main(*args, **kwargs)
        check_cancelled()
        return result
    except (StageCancelled, KeyboardInterrupt, asyncio.CancelledError):
        control.request_stop()
        control.drain()
        control.kill_children()
        _notice("已停止；已落盘结果保留，未完成的任务可在下次运行时重试。")
        raise SystemExit(130) from None
    except BaseException:
        # 异常退出也停止后台请求，避免 traceback 打完仍被非 daemon 线程拖住。
        control.request_stop()
        control.drain()
        control.kill_children()
        raise
    finally:
        control.finished.set()
        watcher.join(timeout=0.5)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        _active = None


class InterruptibleThreadPoolExecutor(_ThreadPoolExecutor):
    """保留标准线程池接口，但提交、等待和退出都响应全局取消。"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._control = _active
        self._pending = set()
        self._pending_lock = threading.RLock()

    def submit(self, fn, /, *args, **kwargs):
        check_cancelled()

        def guarded():
            check_cancelled()
            return fn(*args, **kwargs)

        future = super().submit(guarded)
        with self._pending_lock:
            self._pending.add(future)
        future.add_done_callback(self._forget)
        if self._control is not None:
            self._control.track_future(future)
        return future

    def _forget(self, future):
        with self._pending_lock:
            self._pending.discard(future)

    def shutdown(self, wait=True, *, cancel_futures=False):
        super().shutdown(wait=False, cancel_futures=cancel_futures or stop_requested())
        if wait:
            while True:
                with self._pending_lock:
                    pending = list(self._pending)
                if not pending:
                    break
                if stop_requested():
                    super().shutdown(wait=False, cancel_futures=True)
                    break
                # 有限等待可让 Windows 主线程及时处理 SIGINT。
                _wait(pending, timeout=_POLL)

    def __exit__(self, exc_type, exc_value, traceback):
        # 普通业务异常可能由调用方容错处理，不能把它变成整个阶段的取消。
        if (exc_type is not None and self._control is not None
                and issubclass(exc_type, (StageCancelled, KeyboardInterrupt, asyncio.CancelledError))):
            self._control.request_stop()
        self.shutdown(wait=True, cancel_futures=exc_type is not None)
        if exc_type is None:
            check_cancelled()
        return False


def as_completed(futures, timeout=None):
    """as_completed 的有限轮询版本，兼容嵌套线程池和已取消 Future。"""
    pending = set(futures)
    deadline = None if timeout is None else time.monotonic() + timeout
    while pending:
        check_cancelled()
        done = {future for future in pending if future.done()}
        if not done:
            remaining = _POLL if deadline is None else min(_POLL, deadline - time.monotonic())
            if remaining <= 0:
                raise TimeoutError(f"{len(pending)} 个任务尚未完成")
            done, _ = _wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
        for future in done:
            check_cancelled()
            pending.remove(future)
            yield future


def wait_future(future):
    """等待单个 Future；工作函数本身的 TimeoutError 不会被误判为等待超时。"""
    for completed in as_completed([future]):
        return completed.result()


async def cancellable(awaitable):
    """跨线程检测取消并中止当前协程，允许其 finally 关闭网络连接。"""
    task = asyncio.ensure_future(awaitable)
    try:
        while not task.done():
            if stop_requested():
                raise asyncio.CancelledError()
            await asyncio.wait([task], timeout=_POLL)
        if stop_requested():
            raise asyncio.CancelledError()
        return await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def managed_popen(*args, **kwargs):
    """登记本阶段启动的服务器，强制退出时可清理该子进程树。"""
    check_cancelled()
    if os.name == "nt":
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
    proc = subprocess.Popen(*args, **kwargs)
    if _active is not None:
        with _active.lock:
            _active.processes[proc] = kwargs.get("start_new_session", False)
    return proc


def release_process(proc):
    if _active is not None:
        with _active.lock:
            _active.processes.pop(proc, None)
