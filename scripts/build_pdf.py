"""Render the original eight physical pages without changing citation page indices."""
import sys
from pathlib import Path
from xml.sax.saxutils import escape
from reportlab.pdfgen.canvas import Canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.colors import HexColor

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.seed_data import PAGES


def main():
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    out=ROOT/'examples'/'sample_notes.pdf'
    out.parent.mkdir(exist_ok=True)
    c=Canvas(str(out),pagesize=(595,842))
    c.setTitle('知证学伴 · 数据结构：栈与队列（原创示例讲义）')
    c.setAuthor('知证学伴 / AI-authored, independent review pending')
    body=ParagraphStyle('body',fontName='STSong-Light',fontSize=13,leading=24,textColor=HexColor('#243e37'))
    for index,page in enumerate(PAGES,1):
        lines=page.splitlines()
        c.setFillColor(HexColor('#fafbf7'));c.rect(0,0,595,842,fill=1,stroke=0)
        c.setFillColor(HexColor('#183e34'));c.rect(0,748,595,94,fill=1,stroke=0)
        c.setFont('STSong-Light',11);c.setFillColor(HexColor('#d4e2c3'))
        c.drawString(46,806,'知证学伴 / 数据结构复习讲义')
        c.setFont('STSong-Light',22);c.setFillColor(HexColor('#ffffff'));c.drawString(46,772,lines[0])
        y=705
        for ordinal,line in enumerate(lines[1:],1):
            para=Paragraph(escape(line),body)
            width,height=para.wrap(456,620)
            if y-height<130:
                raise RuntimeError(f'Page {index} overflow; retain physical page mapping by revising layout')
            c.setFillColor(HexColor('#829b65'));c.circle(49,y-10,3,fill=1,stroke=0)
            para.drawOn(c,69,y-height)
            y-=height+23
        c.setStrokeColor(HexColor('#dce4d4'));c.line(46,105,549,105)
        c.setFillColor(HexColor('#607466'));c.setFont('STSong-Light',9)
        c.drawString(46,84,'原创演示内容 · 离线 AI 编写 · 待独立人工审定，不作为正式教材')
        c.drawString(46,66,'原文句子按物理页码引用；题库中的句子编号可用于回查上下文。')
        c.setFont('Helvetica',11);c.drawRightString(549,45,f'{index:02d} / {len(PAGES):02d}')
        c.showPage()
    c.save()
    print(out)


if __name__=='__main__': main()
