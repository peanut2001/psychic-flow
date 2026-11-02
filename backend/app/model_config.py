"""模型连接配置：页面随请求携带的配置优先，缺省回退到 .env。

浏览器把 API Key、接口地址与模型名保存在本地，每次请求通过 ``X-Model-*`` 请求头带给后端。
中间件把它放进请求级 ``ContextVar``；请求内创建的后台任务（筛选任务、简历解析）会复制
当前上下文，因此整批筛选都沿用发起请求时的配置。后端不落盘、不入库，也不跨请求保留。
不由请求触发的后台循环（身份提取、邮箱捕获）拿不到页面配置，只能使用 .env。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import Settings

HEADER_API_KEY = "x-model-api-key"
HEADER_BASE_URL = "x-model-base-url"
HEADER_TEXT_MODEL = "x-model-text"
HEADER_VISION_MODEL = "x-model-vision"


@dataclass(frozen=True)
class ModelConfig:
    """一次模型调用使用的连接配置；key 不进入 repr，避免出现在日志与异常中。"""

    api_key: str = field(repr=False)
    base_url: str | None
    text_model: str
    vision_model: str
    from_request: bool = False


_request_config: ContextVar[ModelConfig | None] = ContextVar("psychic_flow_request_model_config", default=None)


def current_model_config(settings: Settings) -> ModelConfig:
    """返回当前生效的模型配置：请求头配置优先，否则使用 .env。"""

    override = _request_config.get()
    if override is not None:
        return override
    return ModelConfig(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url or None,
        text_model=settings.text_model,
        vision_model=settings.vision_model,
    )


def config_from_headers(headers: Headers, settings: Settings) -> ModelConfig | None:
    """解析页面携带的配置；没有 key 时视为未携带，模型名留空时回退 .env 默认值。"""

    api_key = headers.get(HEADER_API_KEY, "").strip()
    if not api_key:
        return None
    return ModelConfig(
        api_key=api_key,
        base_url=headers.get(HEADER_BASE_URL, "").strip() or None,
        text_model=headers.get(HEADER_TEXT_MODEL, "").strip() or settings.text_model,
        vision_model=headers.get(HEADER_VISION_MODEL, "").strip() or settings.vision_model,
        from_request=True,
    )


def open_client(config: ModelConfig, settings: Settings, *, timeout: float | None = None, max_retries: int | None = None) -> AsyncOpenAI:
    """按配置新建 OpenAI 兼容客户端；调用方负责关闭。"""

    return AsyncOpenAI(
        api_key=config.api_key or "not-configured",
        base_url=config.base_url,
        timeout=settings.model_timeout_seconds if timeout is None else timeout,
        max_retries=settings.model_max_retries if max_retries is None else max_retries,
    )


@asynccontextmanager
async def model_session(
    settings: Settings,
    default_client: Callable[[], AsyncOpenAI],
) -> AsyncIterator[tuple[AsyncOpenAI, ModelConfig]]:
    """取得本次调用的客户端与配置。

    .env 配置沿用组件缓存的客户端；页面配置每次新建客户端并在用完后关闭，不跨请求保留 key。
    """

    config = current_model_config(settings)
    if not config.from_request:
        yield default_client(), config
        return
    async with open_client(config, settings) as client:
        yield client, config


def describe_model_error(exc: APIError, config: ModelConfig, model: str | None = None) -> str:
    """把 SDK 异常转成可展示的中文说明；model 为空表示获取模型列表。"""

    target = f"模型 {model}" if model else "模型列表"
    if isinstance(exc, APITimeoutError):
        return "连接接口地址超时，请检查地址或稍后重试"
    if isinstance(exc, APIConnectionError):
        return "无法连接接口地址，请检查地址（通常以 /v1 结尾）与网络"
    if isinstance(exc, AuthenticationError):
        return "API Key 无效或已失效"
    if isinstance(exc, PermissionDeniedError):
        return f"API Key 无权访问{target}"
    if isinstance(exc, NotFoundError):
        if model:
            return f"模型 {model} 不存在，或接口地址有误（通常以 /v1 结尾）"
        return "接口地址有误（通常以 /v1 结尾），或该接口不提供模型列表"
    if isinstance(exc, RateLimitError):
        return "接口限流或额度不足，请稍后重试"
    if isinstance(exc, APIStatusError):
        detail = exc.message.replace(config.api_key, "***")[:200]
        return f"{target}调用失败（HTTP {exc.status_code}）：{detail}"
    return f"{target}调用失败，请检查配置后重试"


def _probe_client(config: ModelConfig, settings: Settings) -> AsyncOpenAI:
    """设置弹窗用的短超时、不重试客户端，失败尽快反馈。"""

    return open_client(config, settings, timeout=min(settings.model_timeout_seconds, 30), max_retries=0)


async def list_available_models(config: ModelConfig, settings: Settings) -> list[str]:
    """读取接口提供的模型列表（去重、排序），供页面下拉选择；失败抛出带中文说明的 RuntimeError。"""

    if not config.api_key:
        raise RuntimeError("请填写 API Key")
    async with _probe_client(config, settings) as client:
        try:
            page = await client.models.list()
        except APIError as exc:
            raise RuntimeError(describe_model_error(exc, config)) from exc
    models = sorted({item.id for item in getattr(page, "data", None) or [] if getattr(item, "id", None)})
    if not models:
        raise RuntimeError("该接口没有返回可用模型")
    return models


async def check_model_connection(config: ModelConfig, settings: Settings) -> list[str]:
    """用给定配置向每个不同的模型发一条最小请求，返回已验证的模型名；失败抛出带中文说明的 RuntimeError。"""

    if not config.api_key:
        raise RuntimeError("请填写 API Key")
    models = list(dict.fromkeys([config.text_model, config.vision_model]))
    async with _probe_client(config, settings) as client:
        for model in models:
            try:
                response = await client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "ping"}],
                )
            except APIError as exc:
                raise RuntimeError(describe_model_error(exc, config, model)) from exc
            if not getattr(response, "choices", None):
                raise RuntimeError(f"模型 {model} 没有返回结果")
    return models


class RequestModelConfigMiddleware:
    """纯 ASGI 中间件：把请求头中的模型配置放入当前请求上下文。

    不使用 BaseHTTPMiddleware，保证路由、BackgroundTasks 与路由内创建的 asyncio 任务都能继承该上下文。
    """

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        token = _request_config.set(config_from_headers(Headers(scope=scope), self.settings))
        try:
            await self.app(scope, receive, send)
        finally:
            _request_config.reset(token)
