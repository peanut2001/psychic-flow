"""将筛选结果安全导出为招聘人员可继续处理的表格与文档。"""

from __future__ import annotations

import csv
from io import BytesIO, StringIO
import json
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from .models import Evaluation


HEADERS = [
    "候选人",
    "简历文件",
    "筛选日期",
    "总分",
    "背景加分",
    "匹配等级",
    "置信度",
    "维度评分",
    "评估摘要",
    "优势",
    "缺口",
    "风险提示",
    "面试问题",
    "证据摘要",
    "人工状态",
    "招聘备注",
    "处理状态",
    "错误信息",
]


def safe_cell(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def compact_number(value: Any) -> str:
    """避免在文档中把整数贡献分显示为 23.0。"""

    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def criterion_score_label(criterion: dict[str, Any]) -> str:
    """同时展示百分制能力分与后端计算的加权贡献。"""

    score = criterion.get("score")
    if score is None:
        return "待重新评分"
    label = f"{score}/100"
    weight = criterion.get("weight") or 0
    if weight:
        label += f"（贡献 {compact_number(criterion.get('weighted_score', 0))}/{weight}）"
    return label


def evaluation_row(item: Evaluation) -> list[Any]:
    result = item.result_json or {}
    candidate = result.get("candidate") or {}
    evidence = []
    dimension_scores = []
    for criterion in result.get("criteria") or []:
        dimension_scores.append(f"{criterion.get('criterion', '')}: {criterion_score_label(criterion)}")
        snippets = "；".join(
            f"{entry.get('text', '')}（{entry.get('location', '')}）" for entry in criterion.get("evidence") or []
        )
        evidence.append(f"{criterion.get('criterion', '')}: {snippets}")
    row = [
        candidate.get("name") or "未识别",
        item.resume.original_name,
        item.run.created_at.isoformat(),
        result.get("overall_score"),
        result.get("background_bonus", 0),
        {"high": "高", "medium": "中", "low": "低"}.get(result.get("match_level"), ""),
        result.get("confidence"),
        "；".join(dimension_scores),
        result.get("summary") or "",
        "；".join(result.get("strengths") or []),
        "；".join(result.get("gaps") or []),
        "；".join(result.get("risk_flags") or []),
        "；".join(result.get("interview_questions") or []),
        "\n".join(evidence),
        {"pending": "待复核", "advance": "推进", "reject": "淘汰", "hold": "保留"}.get(item.review_status, item.review_status),
        item.review_note,
        item.status,
        item.error or "",
    ]
    return [safe_cell(value) for value in row]


def build_xlsx(items: list[Evaluation]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "筛选结果"
    sheet.append(HEADERS)
    for item in items:
        sheet.append(evaluation_row(item))
    header_fill = PatternFill("solid", fgColor="173F35")
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = Font(color="FFFFFF", bold=True)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    widths = [16, 24, 22, 9, 10, 10, 10, 36, 36, 30, 30, 30, 34, 48, 12, 28, 12, 30]
    for index, width in enumerate(widths, 1):
        sheet.column_dimensions[chr(64 + index)].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def build_csv(items: list[Evaluation]) -> bytes:
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(HEADERS)
    writer.writerows(evaluation_row(item) for item in items)
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def build_json(items: list[Evaluation]) -> bytes:
    """导出保留完整结构的机器可读评估结果。"""

    payload = {
        "schema_version": "2",
        "items": [
            {
                "evaluation_id": item.id,
                "run_id": item.run_id,
                "resume_id": item.resume_id,
                "resume_name": item.resume.original_name,
                "screened_at": item.run.created_at.isoformat(),
                "status": item.status,
                "attempts": item.attempts,
                "review_status": item.review_status,
                "review_note": item.review_note,
                "updated_at": item.updated_at.isoformat(),
                "result": item.result_json,
                "error": item.error,
            }
            for item in items
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def display_value(value: Any, fallback: str = "—") -> str:
    """把空值转换为适合人类阅读的文档文本。"""

    if value is None or value == "":
        return fallback
    return str(value)


def candidate_name(item: Evaluation) -> str:
    result = item.result_json or {}
    return (result.get("candidate") or {}).get("name") or item.resume.original_name


def localized_match_level(value: str | None) -> str:
    return {"high": "高", "medium": "中", "low": "低"}.get(value or "", "未评分")


def localized_review_status(value: str) -> str:
    return {"pending": "待复核", "advance": "推进", "reject": "淘汰", "hold": "保留"}.get(value, value)


def markdown_list(items: list[str] | None) -> str:
    values = items or []
    return "\n".join(f"- {value}" for value in values) if values else "- 无"


def build_markdown(items: list[Evaluation]) -> bytes:
    """生成保留评估层次与证据的 Markdown 分析报告。"""

    sections = ["# 候选人筛选分析", "", f"> 共导出 {len(items)} 位候选人的筛选结果。"]
    for index, item in enumerate(items, 1):
        result = item.result_json or {}
        candidate = result.get("candidate") or {}
        sections.extend(
            [
                "",
                "---",
                "",
                f"## {index}. {candidate_name(item)}",
                "",
                f"- 简历文件：{item.resume.original_name}",
                f"- 筛选日期：{item.run.created_at.isoformat()}",
                f"- 当前岗位：{display_value(candidate.get('current_role'))}",
                f"- 综合得分：{display_value(result.get('overall_score'), '未评分')}",
                f"- 背景加分：{display_value(result.get('background_bonus'), '0')}",
                f"- 评分版本：{display_value(result.get('scoring_version'), '旧版')}",
                f"- 匹配等级：{localized_match_level(result.get('match_level'))}",
                f"- 置信度：{display_value(result.get('confidence'))}",
                f"- 人工状态：{localized_review_status(item.review_status)}",
                f"- 招聘备注：{display_value(item.review_note)}",
                "",
                "### 评估摘要",
                "",
                display_value(result.get("summary"), "尚未生成评估摘要。"),
                "",
                "### 维度评分与证据",
            ]
        )
        criteria = result.get("criteria") or []
        if not criteria:
            sections.extend(["", "尚未生成维度评分。"])
        for criterion in criteria:
            sections.extend(
                [
                    "",
                    f"#### {criterion.get('criterion') or '未命名维度'} · {criterion_score_label(criterion)}",
                ]
            )
            evidence = criterion.get("evidence") or []
            if evidence:
                for entry in evidence:
                    sections.extend(
                        [
                            "",
                            f"> {entry.get('text') or '无证据文本'}",
                            ">",
                            f"> 位置：{entry.get('location') or '未标注'}",
                        ]
                    )
            else:
                sections.extend(["", "未提供原文证据。"])
        for title, key in [
            ("优势", "strengths"),
            ("缺口", "gaps"),
            ("风险提示", "risk_flags"),
            ("建议面试追问", "interview_questions"),
        ]:
            sections.extend(["", f"### {title}", "", markdown_list(result.get(key))])
        if item.error:
            sections.extend(["", "### 处理错误", "", item.error])
    return ("\n".join(sections) + "\n").encode("utf-8")


INK = RGBColor(0x17, 0x3F, 0x35)
RUST = RGBColor(0xB5, 0x4C, 0x32)
GOLD = RGBColor(0x8A, 0x6A, 0x24)
MUTED = RGBColor(0x5F, 0x6B, 0x65)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
LATIN_FONT = "Calibri"
EAST_ASIA_FONT = "微软雅黑"
CONTENT_WIDTH = Cm(17.4)
MATCH_FILL = {"high": "1F6B4A", "medium": "8A6A24", "low": "8A3A28"}
STATUS_LABELS = {"met": "满足", "partial": "部分", "not_met": "不满足", "unknown": "未知"}


def set_run_font(run: Any, size: int | None = None, bold: bool | None = None, color: RGBColor | None = None) -> None:
    """统一 Word 文档中的中西文字体，优先使用各办公套件常见字体。"""

    run.font.name = LATIN_FONT
    run._element.rPr.rFonts.set(qn("w:ascii"), LATIN_FONT)
    run._element.rPr.rFonts.set(qn("w:hAnsi"), LATIN_FONT)
    run._element.rPr.rFonts.set(qn("w:eastAsia"), EAST_ASIA_FONT)
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    if color is not None:
        run.font.color.rgb = color


def _set_style_font(style: Any, *, size: float, bold: bool, color: RGBColor, latin: str = LATIN_FONT) -> None:
    style.font.name = latin
    style.font.size = Pt(size)
    style.font.bold = bold
    style.font.color.rgb = color
    r_fonts = style.element.get_or_add_rPr().get_or_add_rFonts()
    r_fonts.set(qn("w:ascii"), latin)
    r_fonts.set(qn("w:hAnsi"), latin)
    r_fonts.set(qn("w:eastAsia"), EAST_ASIA_FONT)


def _shade_cell(cell: Any, fill: str) -> None:
    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), fill)
    cell._tc.get_or_add_tcPr().append(shading)


def _set_table_widths(table: Any, widths: list[Any]) -> None:
    table.autofit = False
    table.allow_autofit = False
    total = sum(int(width.twips) for width in widths)
    tbl_pr = table._tbl.tblPr
    tbl_w = tbl_pr.find(qn("w:tblW"))
    if tbl_w is None:
        tbl_w = OxmlElement("w:tblW")
        tbl_pr.append(tbl_w)
    tbl_w.set(qn("w:w"), str(total))
    tbl_w.set(qn("w:type"), "dxa")
    grid = table._tbl.find(qn("w:tblGrid"))
    if grid is None:
        grid = OxmlElement("w:tblGrid")
        table._tbl.insert(1, grid)
    for child in list(grid):
        grid.remove(child)
    for width in widths:
        column = OxmlElement("w:gridCol")
        column.set(qn("w:w"), str(int(width.twips)))
        grid.append(column)
    for row in table.rows:
        for cell, width in zip(row.cells, widths, strict=True):
            cell.width = width


def _keep_row_together(row: Any) -> None:
    row_props = row._tr.get_or_add_trPr()
    cant_split = OxmlElement("w:cantSplit")
    row_props.append(cant_split)


def _set_table_borders(table: Any, color: str = "D9D2C5") -> None:
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), "4")
        element.set(qn("w:space"), "0")
        element.set(qn("w:color"), color)
        borders.append(element)
    table._tbl.tblPr.append(borders)


def _write_cell(
    cell: Any,
    text: str,
    *,
    bold: bool = False,
    size: int = 10,
    color: RGBColor | None = None,
    fill: str | None = None,
    align: WD_ALIGN_PARAGRAPH | None = None,
) -> None:
    if fill:
        _shade_cell(cell, fill)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    paragraph = cell.paragraphs[0]
    paragraph.alignment = align or WD_ALIGN_PARAGRAPH.LEFT
    paragraph.paragraph_format.space_before = Pt(2)
    paragraph.paragraph_format.space_after = Pt(2)
    paragraph.paragraph_format.left_indent = Cm(0.12)
    paragraph.paragraph_format.right_indent = Cm(0.12)
    paragraph.text = ""
    run = paragraph.add_run(text)
    set_run_font(run, size=size, bold=bold, color=color)


def _add_body_paragraph(document: Document, text: str, *, size: int = 11, color: RGBColor | None = None, italic: bool = False) -> Any:
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_after = Pt(8)
    paragraph.paragraph_format.line_spacing = 1.15
    run = paragraph.add_run(text)
    set_run_font(run, size=size, color=color or INK)
    run.italic = italic
    return paragraph


def _add_quote(document: Document, text: str, location: str) -> None:
    quote = document.add_paragraph()
    quote.paragraph_format.left_indent = Cm(0.4)
    quote.paragraph_format.space_after = Pt(2)
    quote.paragraph_format.line_spacing = 1.15
    borders = OxmlElement("w:pBdr")
    left = OxmlElement("w:left")
    left.set(qn("w:val"), "single")
    left.set(qn("w:sz"), "18")
    left.set(qn("w:space"), "10")
    left.set(qn("w:color"), "B54C32")
    borders.append(left)
    quote._p.get_or_add_pPr().append(borders)
    run = quote.add_run(text)
    set_run_font(run, size=10.5, color=INK)
    run.italic = True
    caption = document.add_paragraph()
    caption.paragraph_format.left_indent = Cm(0.4)
    caption.paragraph_format.space_after = Pt(10)
    location_run = caption.add_run(f"位置：{location}")
    set_run_font(location_run, size=9, color=MUTED)


def _add_bullet_list(document: Document, values: list[str] | None) -> None:
    if not values:
        _add_body_paragraph(document, "暂无", size=10, color=MUTED, italic=True)
        return
    for value in values:
        paragraph = document.add_paragraph(style="List Bullet")
        paragraph.paragraph_format.space_after = Pt(3)
        paragraph.text = ""
        run = paragraph.add_run(value)
        set_run_font(run, size=11, color=INK)


def _localized_kind(criterion: dict[str, Any]) -> str:
    kind = criterion.get("kind")
    if criterion.get("knockout") or kind == "knockout":
        return "硬性"
    if kind == "bonus":
        return "加分项"
    return "普通"


def _format_screened_at(item: Evaluation) -> str:
    moment = item.run.created_at
    return f"{moment.year}年{moment.month}月{moment.day}日"


def _format_confidence(value: Any) -> str:
    if value is None or value == "":
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if 0 <= number <= 1:
        return f"{int(round(number * 100))}%"
    return str(value)


def _configure_styles(document: Document) -> None:
    styles = document.styles
    _set_style_font(styles["Normal"], size=11, bold=False, color=INK)
    styles["Normal"].paragraph_format.line_spacing = 1.15
    styles["Normal"].paragraph_format.space_after = Pt(6)
    _set_style_font(styles["Title"], size=22, bold=True, color=INK)
    _set_style_font(styles["Heading 1"], size=16, bold=True, color=INK)
    _set_style_font(styles["Heading 2"], size=12, bold=True, color=RUST)
    _set_style_font(styles["Heading 3"], size=11, bold=True, color=INK)
    for name, before, after in (("Heading 1", 16, 8), ("Heading 2", 14, 6), ("Heading 3", 10, 4)):
        styles[name].paragraph_format.space_before = Pt(before)
        styles[name].paragraph_format.space_after = Pt(after)


def _configure_page(document: Document) -> None:
    section = document.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.left_margin = Cm(1.8)
    section.right_margin = Cm(1.8)
    section.top_margin = Cm(1.8)
    section.bottom_margin = Cm(2.0)
    footer = section.footer
    footer.is_linked_to_previous = False
    paragraph = footer.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run("PsychicFlow  ·  候选人筛选分析  ·  第 ")
    set_run_font(run, size=9, color=MUTED)
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instruction = OxmlElement("w:instrText")
    instruction.set(qn("xml:space"), "preserve")
    instruction.text = " PAGE "
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    number = OxmlElement("w:t")
    number.text = "1"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    page_run = paragraph.add_run()
    page_run._r.append(begin)
    page_run._r.append(instruction)
    page_run._r.append(separate)
    page_run._r.append(number)
    page_run._r.append(end)
    set_run_font(page_run, size=9, color=MUTED)
    suffix = paragraph.add_run(" 页")
    set_run_font(suffix, size=9, color=MUTED)


def _add_score_banner(document: Document, result: dict[str, Any], review_status: str) -> None:
    match = result.get("match_level") or "low"
    fill = MATCH_FILL.get(match, MATCH_FILL["low"])
    table = document.add_table(rows=1, cols=3)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_table_widths(table, [Cm(4.4), Cm(6.6), Cm(6.4)])
    cells = table.rows[0].cells
    _write_cell(cells[0], "综合匹配", bold=True, size=11, color=WHITE, fill=fill)
    _write_cell(
        cells[1],
        f"{display_value(result.get('overall_score'), '未评分')} / 100",
        bold=True,
        size=22,
        color=WHITE,
        fill=fill,
        align=WD_ALIGN_PARAGRAPH.CENTER,
    )
    _write_cell(
        cells[2],
        f"{localized_match_level(result.get('match_level'))}匹配  ·  {localized_review_status(review_status)}",
        bold=True,
        size=11,
        color=WHITE,
        fill=fill,
        align=WD_ALIGN_PARAGRAPH.RIGHT,
    )


def _add_meta_table(document: Document, rows: list[tuple[str, str]]) -> None:
    table = document.add_table(rows=len(rows) // 2 + len(rows) % 2, cols=4)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_table_widths(table, [Cm(3.2), Cm(5.5), Cm(3.2), Cm(5.5)])
    _set_table_borders(table)
    padded = rows + [("", "")] * (len(table.rows) * 2 - len(rows))
    for index, (label, value) in enumerate(padded):
        row = table.rows[index // 2]
        label_cell = row.cells[(index % 2) * 2]
        value_cell = row.cells[(index % 2) * 2 + 1]
        fill = "F7F3E9" if (index // 2) % 2 == 0 else "FFFFFF"
        _write_cell(label_cell, label, bold=True, size=9, color=MUTED, fill=fill)
        _write_cell(value_cell, value, size=10, color=INK, fill=fill)


def _add_criteria_table(document: Document, criteria: list[dict[str, Any]]) -> None:
    table = document.add_table(rows=1 + len(criteria), cols=5)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    widths = [Cm(5.4), Cm(2.2), Cm(3.4), Cm(2.6), Cm(3.8)]
    _set_table_widths(table, widths)
    _set_table_borders(table)
    headers = ["维度", "性质", "得分", "状态", "加权贡献"]
    for cell, header in zip(table.rows[0].cells, headers, strict=True):
        _write_cell(cell, header, bold=True, size=9, color=WHITE, fill="173F35", align=WD_ALIGN_PARAGRAPH.CENTER)
    for index, criterion in enumerate(criteria, 1):
        fill = "F7F3E9" if index % 2 else "FFFFFF"
        kind = _localized_kind(criterion)
        kind_color = RUST if kind == "硬性" else GOLD if kind == "加分项" else INK
        status = STATUS_LABELS.get(str(criterion.get("status") or ""), display_value(criterion.get("status")))
        score = criterion.get("score")
        weight = criterion.get("weight") or 0
        contribution = (
            "—"
            if score is None
            else f"{compact_number(criterion.get('weighted_score', 0))} / {weight}"
        )
        values = [
            (display_value(criterion.get("criterion"), "未命名维度"), INK, False),
            (kind, kind_color, True),
            ("待重新评分" if score is None else f"{score} / 100", INK, True),
            (status, RUST if status == "不满足" else INK, status == "不满足"),
            (contribution, MUTED, False),
        ]
        for cell, (text, color, bold) in zip(table.rows[index].cells, values, strict=True):
            _write_cell(cell, text, bold=bold, size=9, color=color, fill=fill)


def add_bullet_list(document: Document, values: list[str] | None) -> None:
    _add_bullet_list(document, values)


def build_docx(items: list[Evaluation]) -> bytes:
    """生成适合归档和人工传阅的 Word 分析报告。"""

    document = Document()
    _configure_styles(document)
    _configure_page(document)

    eyebrow = document.add_paragraph()
    eyebrow.alignment = WD_ALIGN_PARAGRAPH.CENTER
    eyebrow.paragraph_format.space_after = Pt(2)
    eyebrow_run = eyebrow.add_run("PSYCHICFLOW  ·  内部筛选报告")
    set_run_font(eyebrow_run, size=9, bold=True, color=RUST)
    title = document.add_heading("候选人筛选分析", 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_after = Pt(4)
    subtitle = document.add_paragraph(f"共导出 {len(items)} 位候选人  ·  模型提供证据，最终判断由招聘人员作出")
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.paragraph_format.space_after = Pt(16)
    for run in subtitle.runs:
        set_run_font(run, size=10, color=MUTED)

    for index, item in enumerate(items, 1):
        result = item.result_json or {}
        candidate = result.get("candidate") or {}
        document.add_heading(f"{index}. {candidate_name(item)}", level=1)
        _add_score_banner(document, result, item.review_status)
        spacer = document.add_paragraph()
        spacer.paragraph_format.space_after = Pt(6)
        _add_meta_table(
            document,
            [
                ("简历文件", item.resume.original_name),
                ("筛选日期", _format_screened_at(item)),
                ("当前岗位", display_value(candidate.get("current_role"))),
                ("背景加分", display_value(result.get("background_bonus"), "0")),
                ("置信度", _format_confidence(result.get("confidence"))),
                ("评分版本", display_value(result.get("scoring_version"), "旧版")),
                ("人工状态", localized_review_status(item.review_status)),
                ("招聘备注", display_value(item.review_note)),
            ],
        )

        document.add_heading("评估摘要", level=2)
        _add_body_paragraph(document, display_value(result.get("summary"), "尚未生成评估摘要。"))

        document.add_heading("维度评分", level=2)
        criteria = result.get("criteria") or []
        if not criteria:
            _add_body_paragraph(document, "尚未生成维度评分。", color=MUTED, italic=True)
        else:
            _add_criteria_table(document, criteria)
            document.add_heading("原文证据", level=3)
            has_evidence = False
            for criterion in criteria:
                evidence = criterion.get("evidence") or []
                if not evidence:
                    continue
                has_evidence = True
                heading = document.add_paragraph()
                heading.paragraph_format.space_before = Pt(8)
                heading.paragraph_format.space_after = Pt(4)
                run = heading.add_run(str(criterion.get("criterion") or "未命名维度"))
                set_run_font(run, size=10, bold=True, color=INK)
                kind = _localized_kind(criterion)
                if kind != "普通":
                    tag = heading.add_run(f"  {kind}")
                    set_run_font(tag, size=9, bold=True, color=RUST if kind == "硬性" else GOLD)
                for entry in evidence:
                    _add_quote(document, entry.get("text") or "无证据文本", entry.get("location") or "未标注")
            if not has_evidence:
                _add_body_paragraph(document, "未提供原文证据。", size=10, color=MUTED, italic=True)

        contrast = document.add_heading("优劣势对照", level=2)
        contrast.paragraph_format.keep_with_next = True
        insight = document.add_table(rows=1, cols=2)
        insight.alignment = WD_TABLE_ALIGNMENT.CENTER
        _set_table_widths(insight, [Cm(8.7), Cm(8.7)])
        _set_table_borders(insight)
        _write_cell(insight.rows[0].cells[0], "优势", bold=True, size=10, color=WHITE, fill="1F6B4A")
        _write_cell(insight.rows[0].cells[1], "缺口", bold=True, size=10, color=WHITE, fill="8A3A28")
        _keep_row_together(insight.rows[0])
        body = insight.add_row().cells
        for cell, key, fill in ((body[0], "strengths", "F3F7F4"), (body[1], "gaps", "F8F1EE")):
            _shade_cell(cell, fill)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
            values = result.get(key) or ["暂无"]
            cell.text = ""
            for offset, value in enumerate(values):
                paragraph = cell.paragraphs[0] if offset == 0 else cell.add_paragraph()
                paragraph.style = "List Bullet"
                paragraph.text = ""
                run = paragraph.add_run(str(value))
                set_run_font(run, size=10, color=INK)
        _keep_row_together(insight.rows[1])

        document.add_heading("需核实", level=2)
        risks = result.get("risk_flags") or []
        if risks:
            risk_table = document.add_table(rows=1, cols=1)
            _set_table_widths(risk_table, [CONTENT_WIDTH])
            _write_cell(risk_table.rows[0].cells[0], "；".join(risks), size=11, color=RUST, fill="F8EDE9")
        else:
            _add_body_paragraph(document, "暂无风险提示。", size=10, color=MUTED, italic=True)

        document.add_heading("建议面试追问", level=2)
        questions = result.get("interview_questions") or []
        if not questions:
            _add_body_paragraph(document, "暂无面试追问。", size=10, color=MUTED, italic=True)
        else:
            for order, question in enumerate(questions, 1):
                paragraph = document.add_paragraph()
                paragraph.paragraph_format.left_indent = Cm(0.4)
                paragraph.paragraph_format.space_after = Pt(4)
                number = paragraph.add_run(f"{order}.  ")
                set_run_font(number, size=11, bold=True, color=RUST)
                body_run = paragraph.add_run(question)
                set_run_font(body_run, size=11, color=INK)

        if item.error:
            document.add_heading("处理错误", level=2)
            _add_body_paragraph(document, item.error, color=RUST)
        if index < len(items):
            document.add_page_break()

    output = BytesIO()
    document.save(output)
    return output.getvalue()
