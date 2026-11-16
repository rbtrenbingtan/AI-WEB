"""Build the competition report from reviewable content and actual UI captures.

Requires the project Python environment. Windows font paths can be overridden.
Every page has an explicit overflow check; rendered pages still need visual QA.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
TEAL = '#14213D'
INK = '#334155'
MUTED = '#64748B'
CREAM = '#F8FAFC'

DIAGRAMS = {
    'architecture': (
        '系统架构 · 导入生成与即时练习分离',
        [('学生浏览器', 'React / TypeScript\n资料 · 练习 · 复习 · AI 工作台'),
         ('同源 API', 'FastAPI\n会话 · 判分 · 后台任务'),
         ('数据与模型', 'SQLite / PDF / 向量缓存\n生成 · 嵌入 · 独立复核')],
        '导入时：解析 → 检索 → 生成 → 复核入库    /    练习时：抽题 → 判分 → 证据回填'),
    'retrieval': (
        'RAG 工作流 · 证据约束每一次生成',
        [('课程 PDF', '物理页码\n句子 ID 与原文偏移'),
         ('混合检索', '向量 Top-4 + 关键词补充\n强制加入误区纠正句'),
         ('受限证据包', '生成只返回句子 ID\n后端查表回填原文')],
        '向量缓存按端点、模型、维度及文本版本校验；引用归属检查与语义支持检查分开执行。'),
    'quality': (
        '质量门槛 · 通过检查才允许进入题库',
        [('生成候选', '情境题 · 四选项\n误区映射 · 证据 ID'),
         ('双层检查', '代码结构与归属校验\n独立盲答与误区映射复核'),
         ('合格题库', '一次修复后重新检查\n仍不合格则拒绝入库')],
        '记录每次失败、重试、token 与耗时；原始密钥和课程提示词不进入日志。'),
}


def diagram(name: str, out: Path, font_path: str) -> None:
    title, boxes, note = DIAGRAMS[name]
    im = Image.new('RGB', (1500, 500), '#EEF2FF')
    d = ImageDraw.Draw(im)
    ft = ImageFont.truetype(font_path, 38)
    fb = ImageFont.truetype(font_path, 32)
    fs = ImageFont.truetype(font_path, 24)
    d.text((50, 34), title, font=ft, fill=TEAL)
    for i, (head, body) in enumerate(boxes):
        x = 50 + i * 490
        d.rounded_rectangle((x, 126, x + 420, 355), radius=20, fill='white', outline='#CBD5E1', width=2)
        d.text((x + 25, 154), head, font=fb, fill=INK)
        d.multiline_text((x + 25, 218), body, font=fs, fill=MUTED, spacing=16)
        if i < 2:
            d.line((x + 433, 239, x + 475, 239), fill=TEAL, width=4)
            d.polygon([(x + 475, 239), (x + 463, 231), (x + 463, 247)], fill=TEAL)
    # Two lines avoid rendering a very long Chinese sentence outside the card.
    lines = [note[j:j + 53] for j in range(0, len(note), 53)]
    d.multiline_text((50, 399), '\n'.join(lines), font=fs, fill=MUTED, spacing=10)
    im.save(out)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--font', default='C:/Windows/Fonts/msyh.ttc')
    parser.add_argument('--bold-font', default='C:/Windows/Fonts/msyhbd.ttc')
    parser.add_argument('--backend-tests', default='artifacts/latest-tests.xml')
    parser.add_argument('--frontend-tests', type=int, required=True)
    args = parser.parse_args()
    pdfmetrics.registerFont(TTFont('Chinese', args.font))
    pdfmetrics.registerFont(TTFont('ChineseBold', args.bold_font))
    pages = json.loads((ROOT / 'docs/report-content.json').read_text('utf-8'))
    assert len(pages) <= 30
    junit = ET.parse(ROOT / args.backend_tests).getroot()
    suites = list(junit.iter('testsuite'))
    tests = sum(int(s.attrib['tests']) for s in suites)
    failed = sum(int(s.attrib.get('failures', 0)) + int(s.attrib.get('errors', 0)) for s in suites)
    assert failed == 0, 'Do not publish passing results when the latest run failed.'
    asset = ROOT / 'artifacts/submission-diagrams'
    asset.mkdir(parents=True, exist_ok=True)
    for name in DIAGRAMS:
        diagram(name, asset / f'{name}.png', args.font)
    output = ROOT / 'submission/知证学伴-技术文档.pdf'
    output.parent.mkdir(exist_ok=True)
    c = canvas.Canvas(str(output), pagesize=A4)
    c.setTitle('知证学伴 · 竞赛技术文档')
    c.setAuthor('知证学伴')
    w, h = A4
    margin = 46
    cw = w - margin * 2
    body = ParagraphStyle('body', fontName='Chinese', fontSize=10.5, leading=18, textColor=colors.HexColor(INK), wordWrap='CJK')
    heading = ParagraphStyle('heading', fontName='ChineseBold', fontSize=12, leading=20, textColor=colors.HexColor(TEAL), wordWrap='CJK')

    def paragraph(text: str, style: ParagraphStyle, y: float) -> float:
        p = Paragraph(escape(text), style)
        _, ph = p.wrap(cw, 2000)
        p.drawOn(c, margin, y - ph)
        return y - ph

    for number, page in enumerate(pages, 1):
        c.setFillColor(colors.HexColor(CREAM))
        c.rect(0, 0, w, h, fill=1, stroke=0)
        c.setFillColor(colors.HexColor(TEAL))
        c.rect(0, h - 8, w, 8, fill=1, stroke=0)
        c.setFont('Chinese', 9)
        c.drawString(margin, h - 38, '知证学伴 / 竞赛技术文档')
        c.drawRightString(w - margin, h - 38, '知证学伴')
        c.setFont('ChineseBold', 28)
        c.drawString(margin, h - 90, page['title'])
        c.setFont('Chinese', 10)
        c.setFillColor(colors.HexColor(MUTED))
        c.drawString(margin, h - 117, page['subtitle'])
        y = h - 146
        if number == 1:
            y = paragraph('本地交付版本：2026-11-17', body, y) - 15
        if page.get('dynamic') == 'tests':
            c.setFillColor(colors.HexColor('#EEF2FF'))
            c.roundRect(margin, y - 57, cw, 57, 9, fill=1, stroke=0)
            c.setFont('ChineseBold', 17)
            c.setFillColor(colors.HexColor(TEAL))
            c.drawString(margin + 17, y - 35, f'{tests} 后端测试通过  /  {args.frontend_tests} 前端测试通过')
            y -= 77
        for title, content in page['sections']:
            y = paragraph(title, heading, y) - 4
            y = paragraph(content, body, y) - 18
        if page.get('image'):
            path = ROOT / 'docs/assets' / page['image']
            iw, ih = Image.open(path).size
            image_h = cw * ih / iw
            # Images appear under the explanation, never overlapping the footer.
            assert y - image_h >= 60, (number, y, image_h, 'image overflow')
            c.drawImage(str(path), margin, y - image_h, width=cw, height=image_h)
            y -= image_h + 20
        if page.get('diagram'):
            image_h = cw / 3
            assert y - image_h >= 60, (number, 'diagram overflow')
            c.drawImage(str(asset / (page['diagram'] + '.png')), margin, y - image_h, width=cw, height=image_h)
            y -= image_h + 20
        assert y >= 42, (number, y, 'text overflow')
        c.setStrokeColor(colors.HexColor('#CBD5E1'))
        c.line(margin, 42, w - margin, 42)
        c.setFillColor(colors.HexColor(MUTED))
        c.setFont('Chinese', 8)
        c.drawString(margin, 25, '工程验证与模型质量分开报告 · 未执行的评测不填成绩')
        c.drawRightString(w - margin, 25, f'{number:02d} / {len(pages):02d}')
        c.showPage()
    c.save()
    print(json.dumps({'file': str(output), 'pages': len(pages), 'backend_tests': tests, 'frontend_tests': args.frontend_tests}, ensure_ascii=False))


if __name__ == '__main__':
    main()
