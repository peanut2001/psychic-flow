"""通过公开 HTTP 接口验证完整筛选与人工复核。"""

from __future__ import annotations

import json
import time
from io import BytesIO

from docx import Document
from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas import EvaluationCard


class FakeEvaluator:
    async def evaluate(self, *, jd_text: str, prompt: str, resume_text: str, scoring_profile=None) -> EvaluationCard:
        assert prompt == "重点考察 Python 工程能力"
        assert "Python" in resume_text
        candidate_name = "李雨" if "李雨" in resume_text else "张晨"
        return EvaluationCard.model_validate(
            {
                "candidate": {"name": candidate_name, "current_role": "后端工程师", "years_experience": 5},
                "overall_score": 86,
                "match_level": "high",
                "confidence": 0.9,
                "summary": "工程经历与岗位高度匹配。",
                "criteria": [
                    {
                        "criterion": "Python 经验",
                        "score": 92,
                        "status": "met",
                        "evidence": [{"text": "五年 Python 后端开发经验", "location": "段落 2"}],
                    }
                ],
                "strengths": ["后端经验扎实"],
                "gaps": [],
                "risk_flags": [],
                "interview_questions": ["如何设计高并发任务系统？"],
            }
        )


def make_docx(name: str = "张晨") -> bytes:
    document = Document()
    document.add_heading(name, level=1)
    document.add_paragraph("五年 Python 后端开发经验")
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_screen_resume_and_record_human_review(tmp_path) -> None:
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        data_dir=tmp_path / "data",
        evaluator=FakeEvaluator(),
    )

    with TestClient(app) as client:
        job = client.post(
            "/api/jobs",
            json={
                "name": "后端工程师",
                "jd_text": "招聘熟练使用 Python 的后端工程师",
                "prompt": "重点考察 Python 工程能力",
            },
        ).json()
        uploaded = client.post(
            f"/api/jobs/{job['id']}/resumes",
            files=[
                ("files", ("张晨.docx", make_docx(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
                ("files", ("李雨.docx", make_docx("李雨"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
            ],
        ).json()["uploaded"]

        created = client.post(
            f"/api/jobs/{job['id']}/runs",
            json={"resume_ids": [resume["id"] for resume in uploaded]},
        )
        assert created.status_code == 201
        run_id = created.json()["id"]

        for _ in range(50):
            run = client.get(f"/api/runs/{run_id}").json()
            if run["status"] == "completed":
                break
            time.sleep(0.02)
        assert run["completed_count"] == 2

        evaluations = client.get(f"/api/runs/{run_id}/evaluations").json()
        assert evaluations[0]["result"]["overall_score"] == 86
        assert evaluations[0]["result"]["criteria"][0]["score"] == 92
        assert evaluations[0]["result"]["criteria"][0]["evidence"][0]["location"] == "段落 2"

        reviewed = client.patch(
            f"/api/evaluations/{evaluations[0]['id']}/review",
            json={"status": "advance", "note": "进入技术面"},
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["review_status"] == "advance"
        assert reviewed.json()["review_note"] == "进入技术面"

        exported = client.get(f"/api/runs/{run_id}/export?format=xlsx")
        assert exported.status_code == 200
        assert exported.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert len(exported.content) > 1000
        assert f'filename="psychic-flow-{run_id}.xlsx"' in exported.headers["content-disposition"]

        exported_json = client.get(f"/api/runs/{run_id}/export?format=json")
        assert exported_json.status_code == 200
        assert exported_json.headers["content-type"].startswith("application/json")
        payload = json.loads(exported_json.content)
        assert payload["schema_version"] == "2"
        assert payload["items"][0]["result"]["overall_score"] == 86

        selected, other = evaluations
        selection_query = f"evaluation_id={selected['id']}"
        selected_json = client.get(f"/api/runs/{run_id}/export?format=json&{selection_query}")
        assert selected_json.status_code == 200
        selected_payload = selected_json.json()
        assert [item["evaluation_id"] for item in selected_payload["items"]] == [selected["id"]]

        exported_markdown = client.get(f"/api/runs/{run_id}/export?format=md&{selection_query}")
        assert exported_markdown.status_code == 200
        assert exported_markdown.headers["content-type"].startswith("text/markdown")
        assert selected["resume_name"] in exported_markdown.text
        assert other["resume_name"] not in exported_markdown.text

        exported_docx = client.get(f"/api/runs/{run_id}/export?format=docx&{selection_query}")
        assert exported_docx.status_code == 200
        assert exported_docx.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        document = Document(BytesIO(exported_docx.content))
        document_text = "\n".join(
            [paragraph.text for paragraph in document.paragraphs]
            + [cell.text for table in document.tables for row in table.rows for cell in row.cells]
        )
        assert selected["resume_name"] in document_text
        assert other["resume_name"] not in document_text
        assert "PSYCHICFLOW" in document_text
        footer_text = "".join(paragraph.text for paragraph in document.sections[0].footer.paragraphs)
        assert footer_text.startswith("PsychicFlow")

        invalid_selection = client.get(
            f"/api/runs/{run_id}/export?format=json&evaluation_id=not-in-this-run"
        )
        assert invalid_selection.status_code == 422

        second_run = client.post(
            f"/api/jobs/{job['id']}/runs",
            json={"resume_ids": [uploaded[0]["id"]]},
        ).json()
        for _ in range(50):
            latest = client.get(f"/api/runs/{second_run['id']}").json()
            if latest["status"] == "completed":
                break
            time.sleep(0.02)

        paged = client.get(
            f"/api/jobs/{job['id']}/evaluations?page=1&page_size=2&sort=screened-desc"
        )
        assert paged.status_code == 200
        page_payload = paged.json()
        assert page_payload["total"] == 2
        assert page_payload["page"] == 1
        assert page_payload["page_size"] == 2
        assert page_payload["total_pages"] == 1
        assert len(page_payload["items"]) == 2
        assert page_payload["items"][0]["screened_at"]
        assert page_payload["items"][0]["run_id"] == second_run["id"]

        untouched = next(item for item in evaluations if item["resume_id"] != uploaded[0]["id"])
        across_runs = [page_payload["items"][0]["id"], untouched["id"]]
        job_export = client.get(
            f"/api/jobs/{job['id']}/export",
            params=[("format", "json"), *(('evaluation_id', item_id) for item_id in across_runs)],
        )
        assert job_export.status_code == 200
        assert {item["evaluation_id"] for item in job_export.json()["items"]} == set(across_runs)


def test_screening_writes_evaluated_name_over_anonymous_filename(tmp_path) -> None:
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        data_dir=tmp_path / "data",
        evaluator=FakeEvaluator(),
    )
    with TestClient(app) as client:
        job = client.post(
            "/api/jobs",
            json={
                "name": "后端工程师",
                "jd_text": "招聘熟练使用 Python 的后端工程师",
                "prompt": "重点考察 Python 工程能力",
            },
        ).json()
        uploaded = client.post(
            f"/api/jobs/{job['id']}/resumes",
            files=[
                (
                    "files",
                    (
                        "【资深JAVA工程师-实时数据引擎_深圳 35-55K】无无 3年.docx",
                        make_docx(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    ),
                )
            ],
        ).json()["uploaded"][0]
        assert "无无" in uploaded["display_name"]

        created = client.post(
            f"/api/jobs/{job['id']}/runs",
            json={"resume_ids": [uploaded["id"]]},
        )
        run_id = created.json()["id"]
        for _ in range(50):
            run = client.get(f"/api/runs/{run_id}").json()
            if run["status"] == "completed":
                break
            time.sleep(0.02)
        resume = client.get(f"/api/jobs/{job['id']}/resumes").json()[0]
        assert resume["display_name"] == "张晨"
