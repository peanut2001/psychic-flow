"""验证页面随请求携带的模型配置：覆盖 .env、贯穿后台筛选任务、测试连接接口。"""

from __future__ import annotations

import json
import re
import time
from io import BytesIO
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from docx import Document
from fastapi.testclient import TestClient
from openai import APIConnectionError, NotFoundError

from app import model_config
from app.config import Settings
from app.evaluator import OpenAIEvaluator
from app.main import create_app

BROWSER_HEADERS = {
    "X-Model-Api-Key": "sk-browser-only",
    "X-Model-Base-Url": "https://gateway.example/v1",
    "X-Model-Text": "browser-text",
    "X-Model-Vision": "browser-vision",
}


def make_fake_openai(*, fail: BaseException | None = None, models: list[str] | None = None):
    """返回记录构造参数与调用的 AsyncOpenAI 替身类，避免测试调用真实模型。"""

    instances: list[Any] = []

    class FakeOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            self.calls: list[dict[str, Any]] = []
            self.closed = False
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
            self.models = SimpleNamespace(list=self._list_models)
            instances.append(self)

        async def _list_models(self) -> SimpleNamespace:
            if fail is not None:
                raise fail
            return SimpleNamespace(data=[SimpleNamespace(id=item) for item in models or []])

        async def _create(self, **kwargs: Any) -> SimpleNamespace:
            self.calls.append(kwargs)
            if fail is not None:
                raise fail
            content = "OK"
            prompt_text = "\n".join(str(message["content"]) for message in kwargs["messages"])
            dimensions = re.search(r"<compiled_scoring_dimensions>(.*?)</compiled_scoring_dimensions>", prompt_text)
            if dimensions:
                content = json.dumps(
                    {
                        "candidate": {"name": "林川"},
                        "confidence": 0.9,
                        "summary": "页面配置评分",
                        "criteria": [
                            {
                                "criterion_id": item["criterion_id"],
                                "score": 80,
                                "status": "met",
                                "evidence": [{"text": "五年 Python 开发经验", "location": "段落 2"}],
                            }
                            for item in json.loads(dimensions.group(1))
                        ],
                        "background_highlights": [],
                        "strengths": [],
                        "gaps": [],
                        "risk_flags": [],
                        "interview_questions": [],
                    },
                    ensure_ascii=False,
                )
            message = SimpleNamespace(content=content)
            return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])

        async def __aenter__(self) -> "FakeOpenAI":
            return self

        async def __aexit__(self, *exc: object) -> None:
            self.closed = True

    return FakeOpenAI, instances


def make_docx() -> bytes:
    document = Document()
    document.add_heading("林川", level=1)
    document.add_paragraph("五年 Python 开发经验")
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def env_settings() -> Settings:
    """不读取本机 .env，模拟「.env 未配置 key」。"""

    return Settings(_env_file=None, openai_api_key="", text_model="env-text", vision_model="env-vision")


def test_health_reports_browser_config_only_when_headers_present(tmp_path) -> None:
    app = create_app(database_url=f"sqlite:///{tmp_path / 'test.db'}", settings=env_settings())
    with TestClient(app) as client:
        plain = client.get("/api/health").json()
        assert plain["model_configured"] is False
        assert plain["text_model"] == "env-text"

        browser = client.get("/api/health", headers=BROWSER_HEADERS).json()
        assert browser["model_configured"] is True
        assert browser["text_model"] == "browser-text"
        assert browser["vision_model"] == "browser-vision"
        assert "sk-browser-only" not in json.dumps(browser)


def test_run_started_with_browser_headers_uses_them_in_background(tmp_path, monkeypatch) -> None:
    fake_openai, instances = make_fake_openai()
    monkeypatch.setattr(model_config, "AsyncOpenAI", fake_openai)
    app = create_app(database_url=f"sqlite:///{tmp_path / 'test.db'}", settings=env_settings())
    with TestClient(app) as client:
        job = client.post("/api/jobs", json={"name": "后端工程师", "jd_text": "Python", "prompt": "关注工程能力"}).json()
        resume = client.post(
            f"/api/jobs/{job['id']}/resumes",
            files=[("files", ("林川.docx", make_docx(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))],
        ).json()["uploaded"][0]
        run = client.post(
            f"/api/jobs/{job['id']}/runs",
            json={"resume_ids": [resume["id"]]},
            headers=BROWSER_HEADERS,
        ).json()
        for _ in range(200):
            current = client.get(f"/api/runs/{run['id']}").json()
            if current["status"] in {"completed", "partial", "failed", "canceled"}:
                break
            time.sleep(0.02)

        assert current["status"] == "completed", client.get(f"/api/runs/{run['id']}/evaluations").json()
        assert current["text_model"] == "browser-text"
        assert current["vision_model"] == "browser-vision"
        assert "sk-browser-only" not in json.dumps(current)
        assert instances, "评分应通过页面配置创建模型客户端"
        assert all(item.kwargs["api_key"] == "sk-browser-only" for item in instances)
        assert all(item.kwargs["base_url"] == "https://gateway.example/v1" for item in instances)
        assert all(call["model"] == "browser-text" for item in instances for call in item.calls)
        assert all(item.closed for item in instances)


@pytest.mark.asyncio
async def test_evaluator_without_any_key_points_to_model_settings() -> None:
    evaluator = OpenAIEvaluator(env_settings())
    with pytest.raises(RuntimeError, match="模型设置"):
        await evaluator.evaluate(jd_text="Python", prompt="关注工程能力", resume_text="五年 Python 开发经验")


def test_model_config_test_endpoint_checks_each_model(tmp_path, monkeypatch) -> None:
    fake_openai, instances = make_fake_openai()
    monkeypatch.setattr(model_config, "AsyncOpenAI", fake_openai)
    app = create_app(database_url=f"sqlite:///{tmp_path / 'test.db'}", settings=env_settings())
    with TestClient(app) as client:
        response = client.post(
            "/api/model-config/test",
            json={"api_key": "sk-test", "base_url": "https://gateway.example/v1", "text_model": "m-text", "vision_model": "m-vision"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["checked_models"] == ["m-text", "m-vision"]
        assert instances[0].kwargs["api_key"] == "sk-test"
        assert instances[0].kwargs["base_url"] == "https://gateway.example/v1"
        assert [call["model"] for call in instances[0].calls] == ["m-text", "m-vision"]
        assert instances[0].closed

        same = client.post("/api/model-config/test", json={"api_key": "sk-test", "text_model": "m-text", "vision_model": "m-text"})
        assert same.json()["checked_models"] == ["m-text"]
        assert instances[1].kwargs["base_url"] is None

        missing = client.post("/api/model-config/test", json={"api_key": "", "text_model": "m-text", "vision_model": "m-text"})
        assert missing.status_code == 422


def test_model_config_test_endpoint_reports_connection_failure(tmp_path, monkeypatch) -> None:
    failure = APIConnectionError(request=httpx.Request("POST", "https://unreachable.example/v1/chat/completions"))
    fake_openai, _ = make_fake_openai(fail=failure)
    monkeypatch.setattr(model_config, "AsyncOpenAI", fake_openai)
    app = create_app(database_url=f"sqlite:///{tmp_path / 'test.db'}", settings=env_settings())
    with TestClient(app) as client:
        response = client.post(
            "/api/model-config/test",
            json={"api_key": "sk-test", "base_url": "https://unreachable.example/v1", "text_model": "m-text", "vision_model": "m-text"},
        )
        assert response.status_code == 400
        assert "无法连接" in response.json()["detail"]
        assert "sk-test" not in response.text


def test_model_list_endpoint_returns_gateway_models(tmp_path, monkeypatch) -> None:
    fake_openai, instances = make_fake_openai(models=["gpt-6.1-sol", "gpt-6-luna", "gpt-6.1-sol", "codex-auto-review"])
    monkeypatch.setattr(model_config, "AsyncOpenAI", fake_openai)
    app = create_app(database_url=f"sqlite:///{tmp_path / 'test.db'}", settings=env_settings())
    with TestClient(app) as client:
        response = client.post("/api/model-config/models", json={"api_key": " sk-test ", "base_url": "https://gateway.example/v1"})
        assert response.status_code == 200, response.text
        assert response.json() == {"models": ["codex-auto-review", "gpt-6-luna", "gpt-6.1-sol"]}
        assert instances[0].kwargs["api_key"] == "sk-test"
        assert instances[0].kwargs["base_url"] == "https://gateway.example/v1"
        assert instances[0].closed

        assert client.post("/api/model-config/models", json={"api_key": ""}).status_code == 422


def test_model_list_endpoint_reports_empty_and_failed_lists(tmp_path, monkeypatch) -> None:
    empty_openai, _ = make_fake_openai(models=[])
    monkeypatch.setattr(model_config, "AsyncOpenAI", empty_openai)
    app = create_app(database_url=f"sqlite:///{tmp_path / 'test.db'}", settings=env_settings())
    with TestClient(app) as client:
        empty = client.post("/api/model-config/models", json={"api_key": "sk-test"})
        assert empty.status_code == 400
        assert "没有返回可用模型" in empty.json()["detail"]

        request = httpx.Request("GET", "https://gateway.example/models")
        failure = NotFoundError("not found", response=httpx.Response(404, request=request), body=None)
        failing_openai, _ = make_fake_openai(fail=failure)
        monkeypatch.setattr(model_config, "AsyncOpenAI", failing_openai)
        missing = client.post("/api/model-config/models", json={"api_key": "sk-test", "base_url": "https://gateway.example"})
        assert missing.status_code == 400
        assert "模型列表" in missing.json()["detail"]
