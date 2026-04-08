"""
Shared PDF builder for K&R C book conversion.
Each chapter team imports this and calls build_chapter_pdf().
"""

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, KeepTogether, HRFlowable
)

# Colors
NAVY = HexColor('#0B2545')
SUBNAVY = HexColor('#13315C')
CONCEPT_BG = HexColor('#EEF2F8')
CONCEPT_BORDER = NAVY
PROBLEM_BG = HexColor('#F1F8F1')
PROBLEM_BORDER = HexColor('#1B5E20')
CHECKPOINT_BG = HexColor('#FFF3F3')
CHECKPOINT_BORDER = HexColor('#8B0000')
TARGET_BG = HexColor('#EBF5FB')
TARGET_BORDER = HexColor('#1A5276')
LIGHT_GRAY = HexColor('#F5F5F5')
WHITE = white


def get_styles():
    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(
        'ChapterTitle', parent=styles['Title'],
        fontSize=22, leading=26, textColor=NAVY,
        spaceAfter=6, alignment=TA_LEFT
    ))
    styles.add(ParagraphStyle(
        'Subtitle', parent=styles['Normal'],
        fontSize=10, leading=13, textColor=HexColor('#666666'),
        spaceAfter=16, alignment=TA_LEFT
    ))
    styles.add(ParagraphStyle(
        'SectionHead', parent=styles['Heading1'],
        fontSize=15, leading=19, textColor=NAVY,
        spaceBefore=18, spaceAfter=8
    ))
    styles.add(ParagraphStyle(
        'SubHead', parent=styles['Heading2'],
        fontSize=12, leading=15, textColor=SUBNAVY,
        spaceBefore=12, spaceAfter=6
    ))
    styles.add(ParagraphStyle(
        'Body', parent=styles['Normal'],
        fontSize=10.5, leading=14, textColor=black,
        spaceAfter=6
    ))
    # Override built-in Code style
    styles['Code'].fontName = 'Courier'
    styles['Code'].fontSize = 9.5
    styles['Code'].leading = 12
    styles['Code'].textColor = black
    styles['Code'].spaceAfter = 4
    styles['Code'].leftIndent = 18
    styles.add(ParagraphStyle(
        'BoldBody', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=10.5, leading=14,
        textColor=black, spaceAfter=6
    ))
    styles.add(ParagraphStyle(
        'CalloutText', parent=styles['Normal'],
        fontSize=10.5, leading=14, textColor=black,
        leftIndent=8, rightIndent=8, spaceAfter=2
    ))
    styles.add(ParagraphStyle(
        'TableHeader', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=9.5, leading=12,
        textColor=white, alignment=TA_CENTER
    ))
    styles.add(ParagraphStyle(
        'TableCell', parent=styles['Normal'],
        fontSize=9.5, leading=12, textColor=black,
        alignment=TA_LEFT
    ))
    styles.add(ParagraphStyle(
        'CheckpointText', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=11, leading=14,
        textColor=HexColor('#8B0000'), leftIndent=8, rightIndent=8
    ))
    return styles


def make_callout(content_paragraphs, bg_color, border_color, styles):
    """Wrap paragraphs in a colored callout box."""
    data = [[content_paragraphs]]
    t = Table(data, colWidths=[6.5 * inch])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), bg_color),
        ('BOX', (0, 0), (-1, -1), 1.5, border_color),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
    ]))
    return t


def concept_box(paragraphs, styles):
    return make_callout(paragraphs, CONCEPT_BG, CONCEPT_BORDER, styles)


def problem_box(paragraphs, styles):
    return make_callout(paragraphs, PROBLEM_BG, PROBLEM_BORDER, styles)


def checkpoint_box(paragraphs, styles):
    return make_callout(paragraphs, CHECKPOINT_BG, CHECKPOINT_BORDER, styles)


def target_box(paragraphs, styles):
    return make_callout(paragraphs, TARGET_BG, TARGET_BORDER, styles)


def make_table(headers, rows, col_widths=None):
    """Build a styled table with navy header and alternating rows."""
    styles = get_styles()
    header_row = [Paragraph(h, styles['TableHeader']) for h in headers]
    data_rows = []
    for row in rows:
        data_rows.append([Paragraph(str(c), styles['TableCell']) for c in row])

    all_data = [header_row] + data_rows
    if col_widths is None:
        col_widths = [6.5 * inch / len(headers)] * len(headers)

    t = Table(all_data, colWidths=col_widths)
    style_cmds = [
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#CCCCCC')),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]
    # Alternating row colors
    for i in range(1, len(all_data)):
        if i % 2 == 0:
            style_cmds.append(('BACKGROUND', (0, i), (-1, i), LIGHT_GRAY))
        else:
            style_cmds.append(('BACKGROUND', (0, i), (-1, i), WHITE))

    t.setStyle(TableStyle(style_cmds))
    return t


def build_chapter_pdf(filename, elements):
    """Build the final PDF from a list of flowable elements."""
    doc = SimpleDocTemplate(
        filename,
        pagesize=letter,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        leftMargin=0.85 * inch,
        rightMargin=0.85 * inch
    )
    doc.build(elements)
    return filename
