import copy
from pathlib import Path

import pytest
from pypdf import PdfWriter

from backend.ai.ingestion import PDFError, parse_pdf, split_pages
from backend.ai.validate_question import validate_question
from backend.ai.validate_question import normalize


def test_normalization_preserves_mathematical_meaning():
    assert normalize('M-1 个元素') != normalize('M+1 个元素')
    assert normalize('Ｍ + １') == normalize('M+1')


@pytest.fixture
def candidate():
    return {
        'target_misconception_id': 'm1',
        'stem': '编辑器使用栈保存撤销历史，新操作记录应如何取出？',
        'options': [
            {'text': '优先取出最近压入的记录', 'is_correct': True, 'feedback': '栈遵循后进先出。'},
            {'text': '按照最早录入的顺序处理', 'is_correct': False, 'feedback': '混淆了队列。', 'misconception_id': 'm1'},
            {'text': '随机选择任意历史节点', 'is_correct': False, 'feedback': '栈只能在栈顶操作。', 'misconception_id': 'm2'},
            {'text': '必须清空全部内容后才能操作', 'is_correct': False, 'feedback': '删除一个元素即可。', 'misconception_id': 'm3'},
        ],
        'evidence_sentence_ids': ['s1'],
        'explanation': '后进先出对应最近操作先撤销。',
    }


def validate(q, **kwargs):
    return validate_question(q, {'m1','m2','m3'}, {'s1','s2'}, **kwargs)


def test_valid_question_gets_server_assigned_letters(candidate):
    r = validate(candidate)
    assert not r['errors']
    q = r['question']
    assert {o['letter'] for o in q['options']} == set('ABCD')
    assert next(o for o in q['options'] if o['letter'] == q['answer_letter'])['is_correct']


@pytest.mark.parametrize('mutation', [
    lambda q: q['options'].pop(),
    lambda q: q['options'][1].update(is_correct=True),
    lambda q: q['options'][1].update(text=q['options'][0]['text']),
    lambda q: q['options'][1].update(text='以上都正确'),
    lambda q: q['options'][1].update(misconception_id='other_topic'),
    lambda q: q.update(evidence_sentence_ids=['another_document_s1']),
    lambda q: q.update(target_misconception_id='missing'),
    lambda q: q['options'][0].update(is_correct='true'),
    lambda q: q.update(page_index=999),
])
def test_rejects_invalid_model_output(candidate, mutation):
    mutation(candidate)
    r = validate(candidate)
    assert r['errors'] and r['question'] is None


def test_near_duplicate_and_wrong_target_rejected(candidate):
    assert validate(candidate, existing_stems=[candidate['stem']])['errors']
    assert validate(candidate, target_id='m2')['errors']


def test_sentence_offsets_and_pages_are_exact():
    original = [{'page_index': 1, 'text': '第一章\n  栈在同一端插入。\n队列先入先出！'},
                {'page_index': 2, 'text': '循环队列采用取模计算；这是第二页。'}]
    result = split_pages(original, 'unit')
    for sentence in result['sentences']:
        page = result['pages'][sentence['page_index']-1]
        assert page['text'][sentence['char_start']:sentence['char_end']] == sentence['text']
    assert all(len({s['page_index'] for s in result['sentences'] if s['id'] in chunk['sentence_ids']}) == 1 for chunk in result['chunks'])
    assert len({s['id'] for s in result['sentences']}) == len(result['sentences'])


def test_rejects_fake_pdf(tmp_path):
    file = tmp_path/'fake.pdf'
    file.write_bytes(b'not a PDF')
    with pytest.raises(PDFError):
        parse_pdf(file, 'fake')


def test_rejects_scan_and_overpage(tmp_path):
    for count in (1, 31):
        writer = PdfWriter()
        for _ in range(count):
            writer.add_blank_page(width=595, height=842)
        file = tmp_path/f'blank{count}.pdf'
        writer.write(file)
        with pytest.raises(PDFError):
            parse_pdf(file, 'scan')


def test_rejects_oversize(tmp_path):
    file = tmp_path/'large.pdf'
    with file.open('wb') as f:
        f.write(b'%PDF-')
        f.truncate(10*1024*1024+1)
    with pytest.raises(PDFError):
        parse_pdf(file, 'large')


def test_parser_hard_deadline(tmp_path):
    file = tmp_path/'deadline.pdf'
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.write(file)
    with pytest.raises(PDFError, match='终止'):
        parse_pdf(file, 'deadline', timeout=0.001)
