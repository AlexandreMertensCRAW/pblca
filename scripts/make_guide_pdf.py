"""Regenerate GUIDE.pdf from GUIDE.md (reportlab, no system deps).

Renders the user guide to A4 PDF: headings in the project green,
tables with borders, fenced code blocks, footer page numbers.
Keep in sync with GUIDE.md; the .md file is the source of truth.
"""

import re

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (HRFlowable, Paragraph, Preformatted,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

GREEN = colors.HexColor("#2c5f2d")
GREY_BORDER = colors.HexColor("#999999")
HEADER_BG = colors.HexColor("#e7efe7")


def md_inline(s: str) -> str:
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`([^`]+)`",
               r'<font face="Courier" size="8.2">\1</font>', s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
               r'<link href="\2" color="#2c5f2d">\1</link>', s)
    return s


def md_inline_pdf(s: str) -> str:
    """md_inline with the internal (#anchor) links kept as plain
    text: the sommaire entries read the same, without unresolved PDF
    destinations (external links stay clickable)."""
    s = md_inline(s)
    s = re.sub(r'<link href="#[^"]*" color="#2c5f2d">([^<]*)</link>',
               r"\1", s)
    return s
    s = re.sub(r"\*(.+?)\*", r"<i>\1</i>", s)
    return s


def main() -> None:
    src = open("GUIDE.md", encoding="utf-8").read()

    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "p", parent=styles["BodyText"],
        fontSize=9.3, leading=13.5, alignment=TA_LEFT, spaceAfter=4,
        fontName="Helvetica",
    )
    s = {
        "title": ParagraphStyle(
            "t", parent=styles["Title"], fontName="Helvetica-Bold",
            fontSize=17, textColor=GREEN, spaceAfter=10,
        ),
        "h2": ParagraphStyle(
            "h2", fontName="Helvetica-Bold", fontSize=13, textColor=GREEN,
            spaceBefore=14, spaceAfter=5,
        ),
        "h3": ParagraphStyle(
            "h3", fontName="Helvetica-Bold", fontSize=11,
            textColor=colors.HexColor("#444444"),
            spaceBefore=10, spaceAfter=4,
        ),
        "p": body,
        "li": ParagraphStyle("li", parent=body, leftIndent=14, spaceAfter=2),
        "code": ParagraphStyle(
            "c", fontName="Courier", fontSize=7.8, leading=10.5,
        ),
        "tc": ParagraphStyle("tc", parent=body, fontSize=8.3, leading=11,
                             spaceAfter=0),
    }

    doc = SimpleDocTemplate(
        "GUIDE.pdf", pagesize=A4,
        leftMargin=1.8 * cm, rightMargin=1.8 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
        title="GUIDE - Guide d'utilisation complet de PBLCA",
        author="PBLCA",
    )

    def footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.drawCentredString(A4[0] / 2, 1.1 * cm,
                                 str(canvas.getPageNumber()))
        canvas.restoreState()

    story = []
    lines = src.split("\n")
    i = 0
    in_code = False
    code_buf = []
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            if in_code:
                story.append(Preformatted("\n".join(code_buf), s["code"]))
                story.append(Spacer(1, 6))
                code_buf = []
            in_code = not in_code
            i += 1
            continue
        if in_code:
            code_buf.append(line)
            i += 1
            continue
        if line.startswith("# "):
            story.append(Paragraph(md_inline_pdf(line[2:]), s["title"]))
            story.append(HRFlowable(width="100%", thickness=1.4,
                                    color=GREEN, spaceAfter=10))
        elif line.startswith("## "):
            story.append(Paragraph(md_inline_pdf(line[3:]), s["h2"]))
            story.append(HRFlowable(width="100%", thickness=0.6,
                                    color=colors.HexColor("#bbbbbb"),
                                    spaceAfter=6))
        elif line.startswith("### "):
            story.append(Paragraph(md_inline_pdf(line[4:]), s["h3"]))
        elif line.strip() == "---":
            pass
        elif line.strip().startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in
                         lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            rows = [r for r in rows if any(r)]
            if rows:
                data = [[Paragraph(md_inline_pdf(c), s["tc"]) for c in r]
                        for r in rows]
                t = Table(data, colWidths=[
                    (A4[0] - 3.6 * cm) / len(rows[0])] * len(rows[0]))
                t.setStyle(TableStyle([
                    ("GRID", (0, 0), (-1, -1), 0.5, GREY_BORDER),
                    ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ]))
                story.append(t)
                story.append(Spacer(1, 6))
            continue
        elif re.match(r"^\s*\d+\.\s", line):
            m = re.match(r"^\s*(\d+)\.\s+(.*)$", line)
            story.append(Paragraph(
                md_inline_pdf(m.group(1) + ". " + m.group(2)), s["li"]))
        elif re.match(r"^\s*[-*]\s+", line):
            m = re.match(r"^\s*[-*]\s+(.*)$", line)
            story.append(Paragraph("\u2022  " + md_inline_pdf(m.group(1)),
                                   s["li"]))
        elif line.strip():
            story.append(Paragraph(md_inline_pdf(line), s["p"]))
        i += 1

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    print("GUIDE.pdf written")


if __name__ == "__main__":
    main()
