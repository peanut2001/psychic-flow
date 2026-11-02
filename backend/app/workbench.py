"""HTTP 层共用的序列化、访问校验与导出辅助。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .config import Settings
from .evaluator import compile_scoring_rubric
from .exporter import build_csv, build_docx, build_json, build_markdown, build_xlsx
from .lifecycle import TrashType
from .model_config import current_model_config
from .models import Evaluation, Job, Resume, ScreeningRun
from .schemas import EvaluationCard, EvaluationOut, JobOut, ResumeOut, RunOut, ScoringDimensionOut, TrashItem


def active_job(session: Session, job_id: str) -> Job | None:
    return session.scalar(select(Job).where(Job.id == job_id, Job.deleted_at.is_(None)))


def active_resume(session: Session, resume_id: str) -> Resume | None:
    return session.scalar(
        select(Resume)
        .outerjoin(Resume.job)
        .where(
            Resume.id == resume_id,
            Resume.deleted_at.is_(None),
            or_(Job.deleted_at.is_(None), Resume.job_id.is_(None)),
        )
    )


def active_run(session: Session, run_id: str) -> ScreeningRun | None:
    return session.scalar(
        select(ScreeningRun)
        .join(ScreeningRun.job)
        .where(
            ScreeningRun.id == run_id,
            ScreeningRun.deleted_at.is_(None),
            Job.deleted_at.is_(None),
        )
    )


def create_run_for_resumes(
    session: Session,
    job: Job,
    resumes: list[Resume],
    settings: Settings,
) -> ScreeningRun:
    """为一批简历创建筛选任务与评估记录行（不提交事务，由调用方提交）。

    供岗位内「筛选已选」与简历池「分配并评估」共用，保证快照口径一致。
    """

    model = current_model_config(settings)
    run = ScreeningRun(
        job_id=job.id,
        jd_snapshot=job.jd_text,
        prompt_snapshot=job.prompt,
        scoring_profile_snapshot=job.scoring_profile,
        text_model=model.text_model,
        vision_model=model.vision_model,
        schema_version="2",
        total_count=len(resumes),
    )
    session.add(run)
    session.flush()
    evaluations: list[Evaluation] = []
    for resume in resumes:
        previous = session.scalar(
            select(Evaluation)
            .where(
                Evaluation.resume_id == resume.id,
                Evaluation.job_id == job.id,
                Evaluation.is_current.is_(True),
                Evaluation.deleted_at.is_(None),
            )
            .order_by(Evaluation.updated_at.desc())
        )
        evaluations.append(
            Evaluation(
                run_id=run.id,
                job_id=job.id,
                resume_id=resume.id,
                is_current=previous is None,
                replaces_evaluation_id=previous.id if previous else None,
            )
        )
    session.add_all(evaluations)
    return run


def serialize_scoring(
    prompt: str,
    profile: list[dict[str, object]] | None = None,
) -> tuple[list[ScoringDimensionOut], Literal["prompt", "default", "profile", "invalid"], str | None]:
    """把提示词或界面量尺编译为可预览结果；无法识别时保留岗位本身，只返回错误说明。"""

    try:
        rubric = compile_scoring_rubric(prompt, profile)
    except RuntimeError as exc:
        return [], "invalid", str(exc)
    return (
        [
            ScoringDimensionOut(
                criterion_id=item.criterion_id,
                name=item.name,
                weight=item.weight,
                kind=item.kind,
                knockout=item.knockout,
            )
            for item in rubric.dimensions
        ],
        rubric.source,
        None,
    )


def serialize_job(session: Session, job: Job) -> JobOut:
    resume_count = session.scalar(
        select(func.count()).select_from(Resume).where(Resume.job_id == job.id, Resume.deleted_at.is_(None))
    ) or 0
    run_count = session.scalar(
        select(func.count())
        .select_from(ScreeningRun)
        .where(ScreeningRun.job_id == job.id, ScreeningRun.deleted_at.is_(None))
    ) or 0
    scoring_dimensions, scoring_source, scoring_error = serialize_scoring(job.prompt, job.scoring_profile)
    return JobOut(
        id=job.id,
        name=job.name,
        jd_text=job.jd_text,
        prompt=job.prompt,
        resume_count=resume_count,
        run_count=run_count,
        scoring_dimensions=scoring_dimensions,
        scoring_source=scoring_source,
        scoring_error=scoring_error,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def serialize_resume(resume: Resume) -> ResumeOut:
    return ResumeOut(
        id=resume.id,
        job_id=resume.job_id,
        original_name=resume.original_name,
        display_name=resume.display_name or resume.original_name,
        mime_type=resume.mime_type,
        size_bytes=resume.size_bytes,
        page_count=resume.page_count,
        parse_status=resume.parse_status,
        parse_error=resume.parse_error,
        source=resume.source if resume.source in {"upload", "email", "paste"} else "upload",
        parsed_text_source=resume.parsed_text_source
        if resume.parsed_text_source in {"extracted", "manual"}
        else "extracted",
        parsed_text_updated_at=resume.parsed_text_updated_at,
        created_at=resume.created_at,
        updated_at=resume.updated_at,
    )


def serialize_run(run: ScreeningRun, *, refresh_counts: bool = False) -> RunOut:
    if refresh_counts:
        completed = sum(item.status == "completed" for item in run.evaluations)
        failed = sum(item.status == "failed" for item in run.evaluations)
        return RunOut(
            id=run.id,
            job_id=run.job_id,
            status=run.status,
            total_count=run.total_count,
            completed_count=completed,
            failed_count=failed,
            text_model=run.text_model,
            vision_model=run.vision_model,
            created_at=run.created_at,
            started_at=run.started_at,
            completed_at=run.completed_at,
        )
    return RunOut.model_validate(run)


def resume_display_name(resume: Resume) -> str:
    return resume.display_name or resume.original_name


def serialize_evaluation(evaluation: Evaluation) -> EvaluationOut:
    return EvaluationOut(
        id=evaluation.id,
        run_id=evaluation.run_id,
        resume_id=evaluation.resume_id,
        resume_name=resume_display_name(evaluation.resume),
        status=evaluation.status,
        result=EvaluationCard.model_validate(evaluation.result_json) if evaluation.result_json else None,
        error=evaluation.error,
        attempts=evaluation.attempts,
        review_status=evaluation.review_status,
        review_note=evaluation.review_note,
        screened_at=evaluation.run.created_at,
        updated_at=evaluation.updated_at,
    )


def export_response(
    items: list[Evaluation],
    format: Literal["xlsx", "csv", "json", "md", "docx"],
    filename_stem: str,
) -> Response:
    builders = {
        "xlsx": (build_xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        "csv": (build_csv, "text/csv; charset=utf-8"),
        "json": (build_json, "application/json; charset=utf-8"),
        "md": (build_markdown, "text/markdown; charset=utf-8"),
        "docx": (build_docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    }
    builder, media_type = builders[format]
    return Response(
        builder(items),
        media_type=media_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="{filename_stem}.{format}"',
        },
    )


def serialize_trash_item(item_type: TrashType, item: Job | Resume | ScreeningRun | Evaluation) -> TrashItem:
    if item.deleted_at is None or item.purge_at is None:
        raise ValueError("资源不在回收站")
    if item_type == "job":
        job = item
        assert isinstance(job, Job)
        return TrashItem(
            item_type="job",
            id=job.id,
            name=job.name,
            job_id=job.id,
            job_name=job.name,
            deleted_at=job.deleted_at,
            purge_at=job.purge_at,
        )
    if item_type == "resume":
        resume = item
        assert isinstance(resume, Resume)
        return TrashItem(
            item_type="resume",
            id=resume.id,
            name=resume_display_name(resume),
            job_id=resume.job_id,
            job_name=resume.job.name if resume.job else "简历池",
            deleted_at=resume.deleted_at,
            purge_at=resume.purge_at,
        )
    if item_type == "run":
        run = item
        assert isinstance(run, ScreeningRun)
        return TrashItem(
            item_type="run",
            id=run.id,
            name=f"筛选任务 · {run.total_count} 份简历",
            job_id=run.job_id,
            job_name=run.job.name,
            deleted_at=run.deleted_at,
            purge_at=run.purge_at,
        )
    evaluation = item
    assert isinstance(evaluation, Evaluation)
    candidate = (evaluation.result_json or {}).get("candidate", {}).get("name")
    return TrashItem(
        item_type="evaluation",
        id=evaluation.id,
        name=candidate or resume_display_name(evaluation.resume),
        job_id=evaluation.run.job_id,
        job_name=evaluation.run.job.name,
        deleted_at=evaluation.deleted_at,
        purge_at=evaluation.purge_at,
    )


def require_active_evaluation(session: Session, evaluation_id: str) -> Evaluation:
    evaluation = session.scalar(
        select(Evaluation)
        .join(Evaluation.run)
        .join(Evaluation.resume)
        .join(ScreeningRun.job)
        .where(
            Evaluation.id == evaluation_id,
            Evaluation.deleted_at.is_(None),
            ScreeningRun.deleted_at.is_(None),
            Resume.deleted_at.is_(None),
            Job.deleted_at.is_(None),
        )
    )
    if not evaluation:
        raise HTTPException(status_code=404, detail="评估结果不存在")
    return evaluation


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_storage_file(path: str) -> Path:
    file_path = Path(path)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return file_path
