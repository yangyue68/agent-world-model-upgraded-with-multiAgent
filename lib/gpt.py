# ============================================================
# 本文件的职责：
#   封装"调用大模型(LLM)"的客户端（GPTClient），支持 Azure OpenAI 和 OpenAI 两种 provider。
#   提供同步/异步、单个/批量、带并发限制与重试的对话补全接口，并做响应兜底。
# ============================================================

import os                       # 操作系统工具：读取环境变量
import asyncio                  # 异步 I/O：并发请求、信号量
import time                     # 时间工具：生成时间戳
import json                     # JSON 工具（备用）
from openai import AsyncAzureOpenAI, BadRequestError, AsyncOpenAI, InternalServerError  # OpenAI 客户端及异常类型
from openai.types.chat import ChatCompletion  # ChatCompletion 类型
from loguru import logger       # 日志工具
from tqdm.asyncio import tqdm   # 异步进度条
from lib.interrupt import (
    InterruptibleThreadPoolExecutor, StageCancelled, cancellable,
    check_cancelled, stop_requested, wait_future,
)


class ChatCompletionFallback(dict):
    # 一个"字典 + 属性访问"的响应包装类，用来兜底：即使响应结构不完整也能用点号访问字段。
    def __init__(self, data: dict[str, any]):
        super().__init__()
        for k, v in data.items():
            self[k] = self._wrap(v)  # 递归包装每个值（嵌套 dict 也变成该类型）

    def __getattr__(self, name: str) -> any:
        # 支持用点号访问字典键：obj.foo 等价于 obj["foo"]
        if name in self:
            return self[name]
        if name == "message" and "choices" in self and isinstance(self["choices"], list):
            # 特殊处理 .message：取第一个 choice 的 message
            first_choice = self["choices"][0]
            return first_choice.message
        return super().__getattribute__(name)  # 其他属性走默认逻辑

    @classmethod
    def _wrap(cls, obj: any) -> any:
        # 递归包装：dict -> 本类型，list -> 列表逐个包装，其余原样返回
        if isinstance(obj, dict):
            return cls(obj)
        if isinstance(obj, list):
            return [cls._wrap(o) for o in obj]
        return obj


class GPTClient:
    def __init__(self, provider: str = "azure", api_key: str | None = None, base_url: str | None = None, timeout: float = 600.0, max_retry_num: int = 3, retry_delay_seconds: float = 3, concurrency_limit: int = 64):
        # 初始化：确定 provider、api_key、endpoint、模型覆盖、重试参数和并发限制。

        if os.environ.get('AWM_SYN_LLM_PROVIDER'):
            # 环境变量里的 provider 优先于参数
            provider = os.environ.get('AWM_SYN_LLM_PROVIDER')

        if os.environ.get('AWM_SYN_OVERRIDE_MODEL'):
            # 环境变量指定的模型覆盖所有请求里的模型名
            self._override_model = os.environ.get('AWM_SYN_OVERRIDE_MODEL')
        else:
            self._override_model = None

        assert provider in ['azure', 'openai'], "Invalid provider, must be 'azure' or 'openai'. For openai, you can set custom base url to adapt to your own inference service."  # 只支持这两种 provider

        if provider == 'azure':
            # Azure 模式：从参数或环境变量读取 endpoint 和 api_key
            self.endpoint = base_url or os.getenv("AZURE_ENDPOINT_URL")
            self.api_key = api_key or os.getenv("AZURE_OPENAI_API_KEY")
        elif provider == 'openai':
            # OpenAI 模式：默认 base_url 指向官方地址
            self.endpoint = base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
            self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        else:
            raise ValueError(f"Invalid provider: {provider}")

        assert self.endpoint is not None and self.api_key is not None, "Endpoint and API key are required"  # endpoint 和 key 缺一不可
        
        self.timeout = timeout  # 请求超时时间
        self.provider = provider  # 记录 provider 类型（_build_client 需要先于它被设置）
        self._build_client()      # 新建 AsyncOpenAI 客户端

        self._max_retry_num = max_retry_num           # 最大重试次数
        self._retry_delay_seconds = retry_delay_seconds  # 重试初始延迟（指数退避）
        self._concurrency_limit = concurrency_limit   # 并发上限
        self._semaphores: dict[asyncio.AbstractEventLoop, asyncio.Semaphore] = {}  # 每个事件循环一个信号量（控制并发）
        self.log_once = True                          # 某些日志只打印一次的开关

    def _build_client(self):
        """根据 provider/endpoint/api_key/timeout 新建 AsyncOpenAI 客户端。

        每次 asyncio.run 都会新建一个事件循环；httpx 连接池不能跨事件循环复用（会报
        "Event loop is closed"），所以每次调用前重建客户端，保证每个循环用全新连接池。
        """
        if self.provider == 'azure':
            self._client = AsyncAzureOpenAI(
                azure_endpoint=self.endpoint,
                api_key=self.api_key,
                api_version='2025-01-01-preview',
                timeout=self.timeout,
            )
        else:  # openai
            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.endpoint,
                timeout=self.timeout,
            )

    def _obj_to_plain(self, obj: any) -> any:
        # 把任意对象递归转成"纯 Python 结构"（dict/list/基础类型），便于包装成可点号访问的结构。
        if obj is None or isinstance(obj, (str, int, float, bool)):
            return obj  # 基础类型直接返回
        if isinstance(obj, (list, tuple)):
            return [self._obj_to_plain(o) for o in obj]  # 列表/元组逐个递归
        if isinstance(obj, dict):
            return {k: self._obj_to_plain(v) for k, v in obj.items()}  # 字典逐个递归
        if hasattr(obj, "to_dict"):
            return self._obj_to_plain(obj.to_dict())  # 有 to_dict 方法（如 Pydantic）则调用
        if hasattr(obj, "dict"):
            return self._obj_to_plain(obj.dict())  # 有 dict 方法则调用
        if hasattr(obj, "__dict__"):
            return {k: self._obj_to_plain(v) for k, v in vars(obj).items()}  # 一般对象转 __dict__
        return obj

    def _wrap_response(self, raw: any) -> ChatCompletionFallback:
        # 把原始响应转成 ChatCompletionFallback 包装对象
        plain = self._obj_to_plain(raw)
        return ChatCompletionFallback(plain)

    def _build_refusal_completion(self, model: str, cf_results: dict[str, any]) -> ChatCompletionFallback:
        # 构造一个"内容被过滤/拒绝"的兜底响应（用于请求失败时的占位）
        fallback = {
            "id": None,
            "object": "chat.completion",
            "created": int(time.time()),   # 当前时间戳
            "model": model,
            "choices": [
                {
                    "finish_reason": "content_filter",  # 结束原因：内容过滤
                    "index": 0,
                    "logprobs": None,
                    "message": {
                        "content": "",
                        "refusal": True,     # 标记为拒绝
                        "role": "assistant",
                        "annotations": [],
                        "audio": None,
                        "function_call": None,
                        "tool_calls": None,
                    },
                }
            ],
            "usage": None,
            "prompt_filter_results": [
                {"prompt_index": 0, "content_filter_results": cf_results}  # 内容过滤结果
            ],
        }
        return ChatCompletionFallback(fallback)

    def _get_semaphore(self) -> asyncio.Semaphore:
        # 获取当前事件循环对应的信号量（用于控制并发数）
        try:
            loop = asyncio.get_running_loop()  # 当前运行中的事件循环
        except RuntimeError:
            return asyncio.Semaphore(1)  # 没有事件循环则返回容量 1 的信号量
        if loop not in self._semaphores:
            self._semaphores[loop] = asyncio.Semaphore(self._concurrency_limit)  # 为该循环创建信号量
        return self._semaphores[loop]

    async def _call_async(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float | None,
        max_completion_tokens: int | None,
        semaphore: asyncio.Semaphore | None = None,
        **kwargs
    ) -> ChatCompletionFallback:
        # 核心异步调用：组装参数 -> 带重试地调用大模型 -> 返回包装后的响应。
        params = {"model": model, "messages": messages}  # 基础参数
        if self._override_model and (not model or model == "your-llm-model-name"):
            # 只有没显式传 model 时才用环境变量覆盖（显式 model 优先，供 verifier 探针用 deepseek-flash 等不同模型）
            params["model"] = self._override_model
        
        if model.startswith('gpt-5'):
            temperature = 1.0  # gpt-5 系列强制 temperature=1.0

        params.update(kwargs)  # 合并额外参数
        # deepseek 系列默认禁用 thinking（省时间 + 省钱）：除非调用方显式传了 extra_body
        if "deepseek" in (params.get("model") or "").lower() and "extra_body" not in params:
            params["extra_body"] = {"thinking": {"type": "disabled"}}
        # 对支持思考模式的 Qwen3 显式关闭 thinking，减少生成开销。
        # <tool_call> 特殊 token 导致的空内容由 rollout 的普通文本标签解决，
        # 不能依靠 enable_thinking=False 修复。调用方已有 extra_body 时保持其配置。
        if "qwen3" in (params.get("model") or "").lower() and "extra_body" not in params:
            params["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
        if temperature is not None:
            params["temperature"] = temperature  # 设置温度
        if max_completion_tokens is not None:
            params["max_completion_tokens"] = max_completion_tokens  # 设置最大输出 token
        if 'max_tokens' in params:
            # gpt-5 系列用 max_completion_tokens；deepseek 等 OpenAI 兼容模型仍用 max_tokens（否则会被服务端忽略，导致输出截断）
            if params.get("model", "").startswith('gpt-5'):
                params['max_completion_tokens'] = params['max_tokens']
                del params['max_tokens']
        last_error = None
        retry_delay = self._retry_delay_seconds  # 初始重试延迟
        sem = semaphore or self._get_semaphore()  # 用传入的或默认的信号量控制并发
        for attempt in range(self._max_retry_num):
            check_cancelled()
            # 最多重试 max_retry_num 次
            try:
                async with sem:
                    # 在信号量内发起请求，避免超过并发上限
                    raw: ChatCompletion = await self._client.chat.completions.create(**params)
                if attempt > 0:
                    logger.info(f"Retry {attempt + 1}/{self._max_retry_num} succeeded")  # 重试成功
                return self._wrap_response(raw)  # 成功，包装并返回
            except (BadRequestError, InternalServerError) as e:
                # 坏请求/服务器内部错误：解析内容过滤结果
                cf_results = {}
                code = None
                try:
                    err = e.response.json().get("error", {})
                    code = err.get("code") or err.get("innererror", {}).get("code")
                    cf_results = err.get("innererror", {}).get("content_filter_result", {}) or {}
                except Exception:
                    pass  # 解析失败忽略
                # 失败时不打印完整 prompt；大轨迹会把日志放大到数十万字符。
                messages = params.get("messages") or []
                message_chars = [len(str(message.get("content", ""))) for message in messages]
                logger.error(
                    f"Error in GPT request: {e}, code: {code}, "
                    f"content_filter_result: {cf_results}, return refusal completion. "
                    f"model={params.get('model')!r}, message_chars={message_chars}, "
                    f"total_input_chars={sum(message_chars)}"
                )
                last_error = e
                if attempt < self._max_retry_num - 1:
                    # 还有重试机会：指数退避后继续
                    logger.warning(f"BadRequestError, InternalServerError on attempt {attempt + 1}/{self._max_retry_num}: {e}. Retrying in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                    continue
            except Exception as e:
                # 其他异常（如连接错误）
                logger.error(f"Error in GPT request: {e}")
                last_error = e
                if attempt < self._max_retry_num - 1:
                    # 还有重试机会：指数退避后继续
                    logger.warning(f"Connection error on attempt {attempt + 1}/{self._max_retry_num}: {e}. Retrying in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                else:
                    break  # 重试次数用完，跳出循环
        logger.error(f"Max retries exceeded. Last error: {last_error}")  # 重试耗尽
        return self._build_refusal_completion(model, {"error": f"Max retries exceeded: {str(last_error)}"})  # 返回兜底响应

    async def send_request(
        self,
        messages: list[dict[str, str]],
        model: str = "your-llm-model-name",
        temperature: float | None = None,
        max_completion_tokens: int | None = None,
        semaphore: asyncio.Semaphore | None = None,
        **kwargs
    ) -> ChatCompletionFallback:
        # 异步发送单次请求（带并发信号量），内部委托给 _call_async。
        return await cancellable(self._call_async(
            messages, model, temperature, max_completion_tokens, semaphore=semaphore, **kwargs
        ))

    def request(
        self,
        messages: list[dict[str, str]],
        model: str = "your-llm-model-name",
        temperature: float | None = None,
        max_completion_tokens: int | None = None,
        **kwargs
    ) -> ChatCompletionFallback:
        # 同步版请求：在同步上下文里跑异步的 _request_async。
        return self._run_async(self._request_async(messages, model, temperature, max_completion_tokens, **kwargs))

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str = "your-llm-model-name",
        stream: bool = False,
        **kwargs
    ) -> str:
        # 同步对话补全：返回纯文本内容。
        response = self.request(
            messages=messages,
            model=model,
            temperature=temperature,
            # vLLM/OpenAI-compatible endpoints generally accept max_tokens.
            # _call_async will translate it to max_completion_tokens only for
            # GPT-5, which keeps rollout compatible with both backends.
            max_tokens=max_tokens,
            **kwargs
        )
        try:
            message = response.choices[0].message
            content = getattr(message, "content", None)
            if content:
                return content
            # 部分 Qwen/vLLM 配置把模型输出放在 reasoning_content，content 留空。
            # rollout 需要看到模型文本才能解析协议，因此在 content 为空时保留该字段，
            # 同时打印 finish_reason，避免空字符串静默变成“0 次工具调用”。
            reasoning = getattr(message, "reasoning_content", None)
            if reasoning:
                logger.warning("LLM response content 为空，使用 reasoning_content 作为文本回退")
                return reasoning
            finish_reason = getattr(response.choices[0], "finish_reason", None)
            logger.error(f"LLM response 没有 content（finish_reason={finish_reason}）：{response}")
            return ""
        except (AttributeError, IndexError, KeyError) as e:
            # 结构异常时返回空字符串
            logger.error(f"Failed to extract content from response: {e}")
            logger.error(f"Response structure: {response}")
            return ""

    def batch_requests(
        self,
        requests: list[dict[str, any]],
        progress_bar: bool = True,
    ) -> list[ChatCompletionFallback]:
        # 同步批量请求：委托给异步的 _batch_requests_async。
        return self._run_async(self._batch_requests_async(requests, progress_bar))

    def batch_chat_completion(
        self,
        requests: list[dict[str, any]],
        progress_bar: bool = True,
    ) -> list[str]:
        # 同步批量对话补全：返回文本内容列表。
        responses = self.batch_requests(requests, progress_bar)
        contents = []
        for i, response in enumerate(responses):
            try:
                message = response.choices[0].message
                content = getattr(message, "content", None)
                if not content:
                    content = getattr(message, "reasoning_content", None)
                    if content:
                        logger.warning(f"LLM batch response {i} content 为空，使用 reasoning_content 作为文本回退")
                if not content:
                    finish_reason = getattr(response.choices[0], "finish_reason", None)
                    logger.error(f"LLM batch response {i} 没有 content（finish_reason={finish_reason}）：{response}")
                    content = ""
                contents.append(content)
            except (AttributeError, IndexError, KeyError) as e:
                logger.error(f"Failed to extract content from response {i}: {e}")
                logger.error(f"Response structure: {response}")
                contents.append("")  # 失败用空字符串占位
        return contents

    def _run_async(self, coro):
        # 生命周期保持在同一事件循环中；取消时先关闭连接，再退出工作线程。
        if stop_requested():
            coro.close()
            raise StageCancelled()

        async def _wrapped():
            self._build_client()
            try:
                return await cancellable(coro)
            finally:
                try:
                    await self._client.close()
                except Exception:
                    pass

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            running_loop = False
        else:
            running_loop = True
        try:
            if not running_loop:
                return asyncio.run(_wrapped())
            # 仅捕获检测 loop 的 RuntimeError；请求中的 RuntimeError 不应重新执行。
            with InterruptibleThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(lambda: asyncio.run(_wrapped()))
                return wait_future(future)
        except asyncio.CancelledError:
            if stop_requested():
                raise StageCancelled() from None
            raise
        finally:
            # 提交后尚未开始就被取消时，释放未 await 的原始 coroutine。
            import inspect
            if inspect.getcoroutinestate(coro) == inspect.CORO_CREATED:
                coro.close()

    async def _request_async(
        self,
        messages: list[dict[str, str]],
        model: str = "your-llm-model-name",
        temperature: float | None = None,
        max_completion_tokens: int | None = None,
        **kwargs
    ) -> ChatCompletionFallback:
        # 异步请求的内部实现（委托给 send_request）。
        return await self.send_request(messages, model, temperature, max_completion_tokens, **kwargs)

    async def _batch_requests_async(
        self,
        requests: list[dict[str, any]],
        progress_bar: bool = True,
    ) -> list[ChatCompletionFallback]:
        # 异步批量请求：为每个请求创建协程，用信号量控制并发，统一 gather。
        tasks = []
        sem = self._get_semaphore()  # 共享信号量
        for req in requests:
            task = self.send_request(
                req["messages"],
                req.get("model", "your-llm-model-name"),
                req.get("temperature"),
                req.get("max_completion_tokens"),
                semaphore=sem,  # 传入共享信号量
                # 把请求里除基础字段外的其他参数一并传入
                **{k: v for k, v in req.items() if k not in ("messages", "model", "temperature", "max_completion_tokens")},
            )
            tasks.append(task)
        if progress_bar:
            return await tqdm.gather(*tasks, desc="Processing requests")  # 带进度条的并发等待
        else:
            return await asyncio.gather(*tasks)  # 普通并发等待

    async def request_async(
        self,
        messages: list[dict[str, str]],
        model: str = "your-llm-model-name",
        temperature: float | None = None,
        max_completion_tokens: int | None = None,
        **kwargs
    ) -> ChatCompletionFallback:
        # 对外异步请求接口。
        return await self._request_async(messages, model, temperature, max_completion_tokens, **kwargs)

    async def chat_completion_async(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str = "your-llm-model-name",
        stream: bool = False,
        **kwargs
    ) -> str:
        # 异步对话补全：返回纯文本内容。
        response = await self.request_async(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs
        )
        try:
            message = response.choices[0].message
            content = getattr(message, "content", None)
            if content:
                return content
            reasoning = getattr(message, "reasoning_content", None)
            if reasoning:
                logger.warning("LLM async response content 为空，使用 reasoning_content 作为文本回退")
                return reasoning
            finish_reason = getattr(response.choices[0], "finish_reason", None)
            logger.error(f"LLM async response 没有 content（finish_reason={finish_reason}）：{response}")
            return ""
        except (AttributeError, IndexError, KeyError) as e:
            logger.error(f"Failed to extract content from response: {e}")
            logger.error(f"Response structure: {response}")
            return ""  # 结构异常时返回空字符串

    async def batch_chat_completion_async(
        self,
        requests: list[dict[str, any]],
        progress_bar: bool = True,
    ) -> list[str]:
        # 异步批量对话补全：返回文本内容列表。
        responses = await self.batch_requests_async(requests, progress_bar)
        contents = []
        for i, response in enumerate(responses):
            try:
                content = response.choices[0].message.content  # 提取文本
                contents.append(content)
            except (AttributeError, IndexError, KeyError) as e:
                logger.error(f"Failed to extract content from response {i}: {e}")
                logger.error(f"Response structure: {response}")
                contents.append("")  # 失败用空字符串占位
        return contents

    async def batch_requests_async(
        self,
        requests: list[dict[str, any]],
        progress_bar: bool = True,
    ) -> list[ChatCompletionFallback]:
        # 对外异步批量请求接口。
        return await self._batch_requests_async(requests, progress_bar)
