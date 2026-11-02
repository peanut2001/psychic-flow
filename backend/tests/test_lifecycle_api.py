"""通过公开 HTTP 接口验证档案生命周期与批量工作流。"""

from __future__ import annotations

import time
from io import BytesIO

from docx import Document
from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas import EvaluationCard


class SequencedEvaluator:
    """每次调用返回递增分数，便于验证重评覆盖。"""

    def __init__(self) -> None:
        self.calls = 0

    async def evaluate(self, *, jd_text: str, prompt: str, resume_text: str, scoring_profile=None) -> EvaluationCard:
        self.calls += 1
        score = 70 + self.calls
        return EvaluationCard.model_validate(
            {
                "candidate": {"name": "林川", "current_role": "工程师"},
                "overall_score": score,
                "match_level": "medium",
                "confidence": 0.9,
                "summary": f"第 {self.calls} 次评分",
                "criteria": [],
                "strengths": [],
                "gaps": [],
                "risk_flags": [],
                "interview_questions": [],
            }
        )


def make_docx() -> bytes:
    document = Document()
    document.add_heading("林川", level=1)
    document.add_paragraph("五年 Python 开发经验")
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def wait_for_run(client: TestClient, run_id: str) -> dict:
    for _ in range(100):
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in {"completed", "partial", "failed", "canceled"}:
            return run
        time.sleep(0.02)
    raise AssertionError("筛选任务未在预期时间内结束")


def create_dossier(client: TestClient) -> tuple[dict, dict]:
    job = client.post(
        "/api/jobs",
        json={"name": "后端工程师", "jd_text": "Python", "prompt": "关注工程能力"},
    ).json()
    resume = client.post(
        f"/api/jobs/{job['id']}/resumes",
        files=[
            (
                "files",
                ("林川.docx", make_docx(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            )
        ],
    ).json()["uploaded"][0]
    return job, resume


def test_resume_soft_delete_restore_and_trash(tmp_path) -> None:
    app = create_app(database_url=f"sqlite:///{tmp_path / 'test.db'}", data_dir=tmp_path / "data")
    with TestClient(app) as client:
        job, resume = create_dossier(client)

        deleted = client.delete(f"/api/resumes/{resume['id']}")
        assert deleted.status_code == 204
        assert client.get(f"/api/jobs/{job['id']}/resumes").json() == []

        trash = client.get("/api/trash", params={"item_type": "resume"}).json()
        assert trash["total"] == 1
        assert trash["items"][0]["name"] == "林川.docx"
        assert trash["items"][0]["purge_at"]

        restored = client.post(f"/api/trash/resume/{resume['id']}/restore")
        assert restored.status_code == 200
        assert client.get(f"/api/jobs/{job['id']}/resumes").json()[0]["id"] == resume["id"]


def test_job_delete_is_recoverable_and_permanent_purge_removes_files(tmp_path) -> None:
    app = create_app(database_url=f"sqlite:///{tmp_path / 'test.db'}", data_dir=tmp_path / "data")
    with TestClient(app) as client:
        job, resume = create_dossier(client)
        original = client.get(f"/api/resumes/{resume['id']}/original")
        assert original.status_code == 200

        assert client.delete(f"/api/jobs/{job['id']}").status_code == 204
        assert client.get("/api/jobs").json() == []
        assert client.get(f"/api/resumes/{resume['id']}/original").status_code == 404

        assert client.delete(f"/api/trash/job/{job['id']}").status_code == 204
        assert client.post(f"/api/trash/job/{job['id']}/restore").status_code == 404


def test_edit_parsed_text_and_reparse(tmp_path, monkeypatch) -> None:
    app = create_app(database_url=f"sqlite:///{tmp_path / 'test.db'}", data_dir=tmp_path / "data")
    with TestClient(app) as client:
        _, resume = create_dossier(client)

        updated = client.patch(
            f"/api/resumes/{resume['id']}",
            json={"display_name": "林川-人工修正版.docx", "parsed_text": "人工修正后的 Python 简历"},
        )
        assert updated.status_code == 200
        assert updated.json()["display_name"] == "林川-人工修正版.docx"
        assert updated.json()["parsed_text_source"] == "manual"
        assert client.get(f"/api/resumes/{resume['id']}/text").text == "人工修正后的 Python 简历"

        async def fake_extract_text(_storage_path: str) -> str:
            return "重新解析后的文本"

        monkeypatch.setattr(app.state.document_processor, "extract_text", fake_extract_text)
        reparsed = client.post(f"/api/resumes/{resume['id']}/reparse")
        assert reparsed.status_code == 202
        for _ in range(50):
            current = client.get(f"/api/jobs/{updated.json()['job_id']}/resumes").json()[0]
            if current["parse_status"] == "ready":
                break
            time.sleep(0.02)
        assert client.get(f"/api/resumes/{resume['id']}/text").text == "重新解析后的文本"


def test_rescore_replaces_current_result_and_batch_review(tmp_path) -> None:
    evaluator = SequencedEvaluator()
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        data_dir=tmp_path / "data",
        evaluator=evaluator,
    )
    with TestClient(app) as client:
        job, resume = create_dossier(client)
        first_run = client.post(
            f"/api/jobs/{job['id']}/runs", json={"resume_ids": [resume["id"]]}
        ).json()
        wait_for_run(client, first_run["id"])
        first = client.get(f"/api/jobs/{job['id']}/evaluations").json()["items"][0]
        assert first["result"]["overall_score"] == 71

        rescored = client.post("/api/evaluations/rescore", json={"evaluation_ids": [first["id"]]})
        assert rescored.status_code == 201
        wait_for_run(client, rescored.json()["id"])
        current = client.get(f"/api/jobs/{job['id']}/evaluations").json()
        assert current["total"] == 1
        assert current["items"][0]["result"]["overall_score"] == 72

        reviewed = client.patch(
            "/api/evaluations/review",
            json={"evaluation_ids": [current["items"][0]["id"]], "status": "advance"},
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["updated_count"] == 1
        assert client.get(f"/api/jobs/{job['id']}/evaluations").json()["items"][0]["review_status"] == "advance"


def test_delete_single_result_does_not_delete_resume(tmp_path) -> None:
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        data_dir=tmp_path / "data",
        evaluator=SequencedEvaluator(),
    )
    with TestClient(app) as client:
        job, resume = create_dossier(client)
        run = client.post(f"/api/jobs/{job['id']}/runs", json={"resume_ids": [resume["id"]]}).json()
        wait_for_run(client, run["id"])
        evaluation = client.get(f"/api/jobs/{job['id']}/evaluations").json()["items"][0]

        assert client.delete(f"/api/evaluations/{evaluation['id']}").status_code == 204
        assert client.get(f"/api/jobs/{job['id']}/evaluations").json()["total"] == 0
        assert client.get(f"/api/jobs/{job['id']}/resumes").json()[0]["id"] == resume["id"]
        assert client.post(f"/api/trash/evaluation/{evaluation['id']}/restore").status_code == 200
        assert client.get(f"/api/jobs/{job['id']}/evaluations").json()["total"] == 1


def test_resume_delete_cascades_evaluations_and_hides_children_in_trash(tmp_path) -> None:
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        data_dir=tmp_path / "data",
        evaluator=SequencedEvaluator(),
    )
    with TestClient(app) as client:
        job, resume = create_dossier(client)
        run = client.post(f"/api/jobs/{job['id']}/runs", json={"resume_ids": [resume["id"]]}).json()
        wait_for_run(client, run["id"])
        evaluation = client.get(f"/api/jobs/{job['id']}/evaluations").json()["items"][0]

        assert client.delete(f"/api/resumes/{resume['id']}").status_code == 204
        assert client.get(f"/api/jobs/{job['id']}/evaluations").json()["total"] == 0
        trash = client.get("/api/trash").json()
        assert trash["total"] == 1
        assert trash["items"][0]["item_type"] == "resume"
        assert client.get(f"/api/trash/evaluation/{evaluation['id']}/restore").status_code == 404

        assert client.post(f"/api/trash/resume/{resume['id']}/restore").status_code == 200
        restored = client.get(f"/api/jobs/{job['id']}/evaluations").json()
        assert restored["total"] == 1
        assert restored["items"][0]["id"] == evaluation["id"]


def test_job_delete_cascades_and_restore_brings_children_back(tmp_path) -> None:
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        data_dir=tmp_path / "data",
        evaluator=SequencedEvaluator(),
    )
    with TestClient(app) as client:
        job, resume = create_dossier(client)
        run = client.post(f"/api/jobs/{job['id']}/runs", json={"resume_ids": [resume["id"]]}).json()
        wait_for_run(client, run["id"])

        assert client.delete(f"/api/jobs/{job['id']}").status_code == 204
        trash = client.get("/api/trash").json()
        assert trash["total"] == 1
        assert trash["items"][0]["item_type"] == "job"

        assert client.post(f"/api/trash/job/{job['id']}/restore").status_code == 200
        assert client.get(f"/api/jobs/{job['id']}/resumes").json()[0]["id"] == resume["id"]
        assert client.get(f"/api/jobs/{job['id']}/evaluations").json()["total"] == 1


def test_cancel_run_stays_canceled_even_if_evaluator_finishes_late(tmp_path) -> None:
    import asyncio
    import threading

    class BlockingEvaluator:
        def __init__(self) -> None:
            self.started = threading.Event()
            self.release = threading.Event()

        async def evaluate(self, *, jd_text: str, prompt: str, resume_text: str, scoring_profile=None):
            self.started.set()
            while not self.release.is_set():
                await asyncio.sleep(0.01)
            return EvaluationCard.model_validate(
                {
                    "candidate": {"name": "晚到"},
                    "overall_score": 90,
                    "match_level": "high",
                    "confidence": 0.9,
                    "summary": "不应写回",
                    "criteria": [],
                    "strengths": [],
                    "gaps": [],
                    "risk_flags": [],
                    "interview_questions": [],
                }
            )

    blocker = BlockingEvaluator()
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        data_dir=tmp_path / "data",
        evaluator=blocker,
    )
    with TestClient(app) as client:
        job, resume = create_dossier(client)
        run = client.post(f"/api/jobs/{job['id']}/runs", json={"resume_ids": [resume["id"]]}).json()
        assert blocker.started.wait(2)
        canceled = client.post(f"/api/runs/{run['id']}/cancel")
        assert canceled.status_code == 200
        assert canceled.json()["status"] == "canceled"
        blocker.release.set()
        time.sleep(0.15)
        final = client.get(f"/api/runs/{run['id']}").json()
        assert final["status"] == "canceled"
        evaluations = client.get(f"/api/runs/{run['id']}/evaluations").json()
        assert all(item["status"] == "canceled" for item in evaluations)
        assert all(item["result"] is None for item in evaluations)


def test_run_list_reports_live_progress_while_running(tmp_path) -> None:
    """任务中心列表在任务运行中也要反映已完成份数，而不是结束后才更新。"""

    import asyncio
    import threading

    class SecondCallBlocksEvaluator:
        def __init__(self) -> None:
            self.calls = 0
            self.blocked = threading.Event()
            self.release = threading.Event()

        async def evaluate(self, *, jd_text: str, prompt: str, resume_text: str, scoring_profile=None):
            self.calls += 1
            if self.calls > 1:
                self.blocked.set()
                while not self.release.is_set():
                    await asyncio.sleep(0.01)
            return EvaluationCard.model_validate(
                {
                    "candidate": {"name": "进度"},
                    "overall_score": 80,
                    "match_level": "medium",
                    "confidence": 0.9,
                    "summary": "进度测试",
                    "criteria": [],
                    "strengths": [],
                    "gaps": [],
                    "risk_flags": [],
                    "interview_questions": [],
                }
            )

    evaluator = SecondCallBlocksEvaluator()
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        data_dir=tmp_path / "data",
        evaluator=evaluator,
    )
    with TestClient(app) as client:
        job, first = create_dossier(client)
        document = Document()
        document.add_heading("周岚", level=1)
        document.add_paragraph("三年 Go 开发经验")
        buffer = BytesIO()
        document.save(buffer)
        second = client.post(
            f"/api/jobs/{job['id']}/resumes",
            files=[("files", ("周岚.docx", buffer.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))],
        ).json()["uploaded"][0]
        run = client.post(f"/api/jobs/{job['id']}/runs", json={"resume_ids": [first["id"], second["id"]]}).json()
        try:
            assert evaluator.blocked.wait(2)
            for _ in range(100):
                statuses = [item["status"] for item in client.get(f"/api/runs/{run['id']}/evaluations").json()]
                if "completed" in statuses:
                    break
                time.sleep(0.02)
            listed = client.get(f"/api/jobs/{job['id']}/runs").json()["items"][0]
            assert listed["id"] == run["id"]
            assert listed["status"] == "running"
            assert listed["completed_count"] == 1
            assert listed["total_count"] == 2
        finally:
            evaluator.release.set()
        assert wait_for_run(client, run["id"])["completed_count"] == 2


def test_invalid_prompt_weights_rejected_on_create_run(tmp_path) -> None:
    app = create_app(database_url=f"sqlite:///{tmp_path / 'test.db'}", data_dir=tmp_path / "data")
    with TestClient(app) as client:
        job = client.post(
            "/api/jobs",
            json={
                "name": "后端",
                "jd_text": "Python",
                "prompt": "## 1. 能力：60 分\n## 2. 经验：30 分\n",
            },
        )
        assert job.status_code == 400
        assert "100" in job.json()["detail"]



def test_rescore_for_another_job_preserves_source_result(tmp_path) -> None:
    app = create_app(database_url=f"sqlite:///{tmp_path / 'test.db'}", data_dir=tmp_path / "data", evaluator=SequencedEvaluator())
    with TestClient(app) as client:
        job, resume = create_dossier(client)
        run = client.post(f"/api/jobs/{job['id']}/runs", json={"resume_ids": [resume['id']]}).json()
        wait_for_run(client, run['id'])
        first = client.get(f"/api/jobs/{job['id']}/evaluations").json()['items'][0]
        target = client.post('/api/jobs', json={'name': '质量架构师', 'jd_text': '测试架构', 'prompt': '关注测试能力'}).json()
        for target_id in ['missing', target['id']]:
            response = client.post('/api/evaluations/rescore', json={'evaluation_ids': [first['id']], 'job_id': target_id})
            if target_id == 'missing':
                assert response.status_code == 404
                continue
            assert response.status_code == 201
            assert response.json()['job_id'] == target['id']
            assert wait_for_run(client, response.json()['id'])['status'] == 'completed'
        source = client.get(f"/api/jobs/{job['id']}/evaluations").json()['items']
        assert [item['id'] for item in source] == [first['id']]
        result = client.get(f"/api/jobs/{target['id']}/evaluations").json()['items'][0]
        assert result['resume_id'] == resume['id']
        assert result['result']['overall_score'] == 72
