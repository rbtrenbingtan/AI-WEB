"""Check local submission artifacts without extracting or modifying them.

The only file written is check-results.json (or --output). PDF content matching
is a reminder for human review, not proof that the report satisfies competition
rules. No model, remote repository, or website is contacted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import stat
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_NAMES = {
    'technical_pdf': '知证学伴-技术文档.pdf',
    'demo_video': '知证学伴-演示视频.mp4',
    'source_zip': '知证学伴-源码.zip',
    'git_bundle': '知证学伴-完整历史.bundle',
}
PDF_SECTIONS = {
    '项目概述': ('项目概述', '项目介绍', '项目定位'),
    '需求分析': ('需求分析', '用户需求'),
    '系统架构设计': ('系统架构', '技术架构', '架构设计'),
    'AI技术选型': ('AI技术选型', '模型选型', '技术选型'),
    '核心功能实现': ('核心功能', '功能实现'),
    '测试与验证': ('测试与验证', '测试结果', '验证结果'),
    '技术与应用创新': ('技术创新', '应用创新', '创新点'),
    '总结与展望': ('总结与展望', '未来展望', '后续方向'),
}
REQUIRED_SOURCE_FILES = (
    'README.md', '.env.example', 'requirements.txt', 'requirements-lock.txt',
    'Dockerfile', 'docker-compose.yml', 'backend/main.py', 'backend/db.py',
    'backend/ai/client.py', 'backend/ai/service.py', 'frontend/package.json',
    'frontend/package-lock.json', 'frontend/index.html', 'frontend/src/main.tsx',
    'frontend/src/App.tsx', 'seed/demo.sqlite', 'scripts/start.ps1', 'scripts/start.sh',
)


def artifact_result(path: Path) -> dict:
    result = {'path': str(path), 'status': 'failed', 'errors': [], 'warnings': [], 'details': {}}
    if not path.is_file():
        result['errors'].append('文件不存在')
        return result
    size = path.stat().st_size
    result['size_bytes'] = size
    if not size:
        result['errors'].append('文件为空')
        return result
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    result['sha256'] = digest.hexdigest()
    return result


def finish(result: dict) -> dict:
    result['status'] = 'failed' if result['errors'] else 'passed'
    return result


def check_pdf(path: Path) -> dict:
    result = artifact_result(path)
    if result['errors']:
        return result
    try:
        from pypdf import PdfReader

        reader = PdfReader(path)
        if reader.is_encrypted:
            result['errors'].append('技术文档为加密 PDF，评审无法直接读取')
            return result
        count = len(reader.pages)
        result['details']['page_count'] = count
        result['details']['page_limit'] = 30
        if not 1 <= count <= 30:
            result['errors'].append(f'技术文档应为 1–30 页，实际 {count} 页')
        # Limit extraction work for an accidentally selected very large report.
        text = '\n'.join((page.extract_text() or '') for page in reader.pages[:30])
        normalized = re.sub(r'\s+', '', text).casefold()
        hints = {
            title: any(re.sub(r'\s+', '', phrase).casefold() in normalized for phrase in phrases)
            for title, phrases in PDF_SECTIONS.items()
        }
        result['details']['content_hints'] = hints
        result['details']['extracted_characters'] = len(text)
        missing = [title for title, present in hints.items() if not present]
        if missing:
            result['warnings'].append('未在提取文本中发现这些章节关键词，请人工核对：' + '、'.join(missing))
        if not normalized:
            result['warnings'].append('未提取到文本，可能是扫描文档或字体编码问题，需人工检查内容')
        result['warnings'].append('章节关键词命中不证明内容完整；版式、图片、事实与赛事要求仍需逐页人工核对')
    except ImportError:
        result['errors'].append('缺少 pypdf，请在项目虚拟环境安装 requirements-lock.txt')
    except Exception as exc:
        result['errors'].append('无法读取 PDF（' + type(exc).__name__ + '）')
    return finish(result)


def check_video(path: Path) -> dict:
    result = artifact_result(path)
    if result['errors']:
        return result
    try:
        import imageio_ffmpeg

        executable = imageio_ffmpeg.get_ffmpeg_exe()
        inspected = subprocess.run(
            [executable, '-hide_banner', '-nostdin', '-i', str(path)],
            capture_output=True, timeout=45, encoding='utf-8', errors='replace',
        )
        metadata = inspected.stderr + '\n' + inspected.stdout
        # `ffmpeg -i` commonly returns 1 because no output was requested. Parse
        # the input metadata instead of treating that expected exit as failure.
        duration = re.search(r'Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)', metadata)
        seconds = None
        if duration:
            hours, minutes, secs = duration.groups()
            seconds = int(hours) * 3600 + int(minutes) * 60 + float(secs)
        result['details']['duration_seconds'] = seconds
        result['details']['duration_range_seconds'] = [300, 480]
        if seconds is None:
            result['errors'].append('ffmpeg 未能识别视频时长')
        if seconds is not None and not 300 <= seconds <= 480:
            result['errors'].append(f'视频应为 300–480 秒，实际 {seconds:.2f} 秒')
        video_streams = []
        audio_count = 0
        for line in metadata.splitlines():
            if not re.search(r'Stream\s+#\d+:\d+', line):
                continue
            if 'Audio:' in line:
                audio_count += 1
            if 'Video:' in line and 'attached pic' not in line:
                dimensions = re.search(r'(?<![\w])([1-9]\d{1,4})x([1-9]\d{1,4})(?![\w])', line)
                if dimensions:
                    width, height = map(int, dimensions.groups())
                    video_streams.append({'width': width, 'height': height})
        result['details']['video_streams'] = video_streams
        result['details']['audio_stream_count'] = audio_count
        if not any(stream['width'] >= 1920 and stream['height'] >= 1080 for stream in video_streams):
            result['errors'].append('缺少至少 1920×1080 的有效视频轨道')
        if not audio_count:
            result['errors'].append('视频没有可识别的音轨')
        input_format = re.search(r'Input #0,\s*([^,]+(?:,[^,]+)*?),\s*from ', metadata)
        format_name = input_format.group(1) if input_format else ''
        result['details']['container_format'] = format_name
        if path.suffix.lower() != '.mp4' or 'mp4' not in format_name.lower().split(','):
            result['errors'].append('文件未被识别为 MP4 容器')
        result['warnings'].append('元数据检查不证明全片可播放或解说清晰，仍需抽帧和试听；不会把合成解说当成真实模型调用')
    except ImportError:
        result['errors'].append('缺少 imageio-ffmpeg；请在当前虚拟环境安装后重试')
    except subprocess.TimeoutExpired:
        result['errors'].append('ffmpeg 检查超过 45 秒，未得到完整元数据')
    except Exception as exc:
        result['errors'].append('无法检查视频（' + type(exc).__name__ + '）')
    return finish(result)


def check_zip(path: Path) -> dict:
    result = artifact_result(path)
    if result['errors']:
        return result
    try:
        with zipfile.ZipFile(path) as archive:
            entries = [info for info in archive.infolist() if not info.is_dir()]
            names = [info.filename.replace('\\', '/') for info in entries]
            result['details']['file_count'] = len(entries)
            result['details']['uncompressed_bytes'] = sum(info.file_size for info in entries)
            unsafe = []
            for info, name in zip(entries, names):
                parts = PurePosixPath(name).parts
                if (name.startswith('/') or re.match(r'^[A-Za-z]:', name) or '..' in parts
                        or stat.S_ISLNK(info.external_attr >> 16)):
                    unsafe.append(name)
            if unsafe:
                result['errors'].append('压缩包含不安全路径或符号链接')
                result['details']['unsafe_entries'] = unsafe
            if len(set(names)) != len(names):
                result['errors'].append('压缩包有重复文件路径')
            if any(info.flag_bits & 1 for info in entries):
                result['errors'].append('压缩包包含加密文件，无法直接复现')
            # Accept both a flat source ZIP and one containing a project folder.
            prefixes = [''] + [name[:-len('README.md')] for name in names if name.endswith('/README.md')]
            prefix = max(prefixes, key=lambda p: sum(p + required in names for required in REQUIRED_SOURCE_FILES))
            logical = {name[len(prefix):] for name in names if name.startswith(prefix)}
            missing = [name for name in REQUIRED_SOURCE_FILES if name not in logical]
            result['details']['project_prefix'] = prefix
            result['details']['missing_required_files'] = missing
            if missing:
                result['errors'].append('压缩包缺少必要源码或运行文件：' + '、'.join(missing))
            forbidden = []
            for name in names:
                parts = tuple(part.casefold() for part in PurePosixPath(name).parts)
                base = parts[-1] if parts else ''
                environment = (base == '.env' or base.startswith('.env.')) and base != '.env.example'
                runtime = any(part in {'node_modules', '.venv', 'venv', 'data', '__pycache__', '.pytest_cache', '.git'} for part in parts)
                database = (base.endswith(('.sqlite', '.sqlite3', '.db', '.sqlite-wal', '.sqlite-shm')) and 'seed' not in parts)
                if environment or runtime or database:
                    forbidden.append(name)
            result['details']['forbidden_entries'] = forbidden
            if forbidden:
                result['errors'].append('压缩包包含真实环境文件、运行数据或本地依赖/缓存；仅报告路径，不读取或输出潜在凭证值')
            # CRC checks read entries without writing them to disk. Bound work
            # to avoid accidentally expanding an unrelated archive bomb.
            if sum(info.file_size for info in entries) > 512 * 1024 * 1024:
                result['errors'].append('源码解压体积超过 512 MB 检查上限，未执行 CRC 校验，请移除非源码大文件')
            if result['details']['uncompressed_bytes'] <= 512 * 1024 * 1024 and not any(info.flag_bits & 1 for info in entries):
                damaged = archive.testzip()
                result['details']['crc_verified'] = damaged is None
                if damaged:
                    result['errors'].append('ZIP CRC 校验失败：' + damaged)
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError) as exc:
        result['errors'].append('无法完整检查源码 ZIP（' + type(exc).__name__ + '）')
    return finish(result)


def check_bundle(path: Path) -> dict:
    result = artifact_result(path)
    if result['errors']:
        return result
    git = shutil.which('git')
    if not git:
        result['errors'].append('找不到 Git，无法验证开发历史包')
        return result
    try:
        with path.open('rb') as bundle:
            header = bundle.readline(4096)
            if header.strip() not in (b'# v2 git bundle', b'# v3 git bundle'):
                result['errors'].append('文件不是支持的 Git bundle 格式')
                return result
            prerequisites = []
            for _ in range(10000):
                line = bundle.readline(8192)
                if line in (b'\n', b'\r\n', b''):
                    break
                if line.startswith(b'-'):
                    prerequisites.append(line[1:].split(b' ', 1)[0].decode('ascii', errors='replace'))
            result['details']['prerequisite_commits'] = prerequisites
            if prerequisites:
                result['errors'].append('这是依赖外部前置提交的增量包，不能单独恢复完整历史')
        verified = subprocess.run([git, '-C', str(ROOT), 'bundle', 'verify', str(path)],
                                  capture_output=True, timeout=45, encoding='utf-8', errors='replace')
        result['details']['git_bundle_verify_exit_code'] = verified.returncode
        if verified.returncode:
            result['errors'].append('git bundle verify 未通过；请在有效项目 Git 仓库中检查包完整性')
        listed = subprocess.run([git, 'bundle', 'list-heads', str(path)], capture_output=True,
                                timeout=15, encoding='utf-8', errors='replace')
        refs = []
        if listed.returncode == 0:
            for line in listed.stdout.splitlines():
                match = re.fullmatch(r'([0-9a-f]{40,64})\s+(.+)', line)
                if match:
                    refs.append({'commit': match.group(1), 'ref': match.group(2)})
        result['details']['refs'] = refs
        if not refs:
            result['errors'].append('bundle 没有可恢复的提交引用')
        result['warnings'].append('仅验证本地历史包；未发布远程仓库，仍建议在独立目录克隆并按 README 复现')
    except subprocess.TimeoutExpired:
        result['errors'].append('Git 历史包验证超时')
    except OSError as exc:
        result['errors'].append('无法读取或验证 Git bundle（' + type(exc).__name__ + '）')
    return finish(result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=ROOT / 'submission', help='材料目录，默认项目 submission/')
    parser.add_argument('--output', type=Path, help='JSON 报告路径，默认材料目录下 check-results.json')
    args = parser.parse_args(argv)
    directory = args.directory.resolve()
    output = (args.output or directory / 'check-results.json').resolve()
    artifacts = {key: directory / name for key, name in ARTIFACT_NAMES.items()}
    if output in artifacts.values():
        parser.error('检查报告路径不能覆盖被检查的材料')
    checks = {}
    for key, checker in (
        ('technical_pdf', check_pdf), ('demo_video', check_video),
        ('source_zip', check_zip), ('git_bundle', check_bundle),
    ):
        try:
            checks[key] = checker(artifacts[key])
        except Exception as exc:
            checks[key] = {'path': str(artifacts[key]), 'status': 'failed',
                           'errors': ['检查未完成（' + type(exc).__name__ + '）'], 'warnings': [], 'details': {}}
        # Shared reports use portable names rather than local account/workspace paths.
        checks[key]['path'] = artifacts[key].name
    passed = all(check['status'] == 'passed' for check in checks.values())
    report = {
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'directory': '.', 'status': 'passed' if passed else 'failed',
        'scope': '本地静态材料检查；只写本报告，不修改材料、不请求模型、不发布远程或提交官网。',
        'checks': checks,
        'manual_review_required': 'PDF 关键词与媒体元数据仅作工程检查；还需人工核对比赛规则、事实、版式、画面与解说。',
    }
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    except OSError as exc:
        print('无法保存检查报告（' + type(exc).__name__ + '）', file=sys.stderr)
        return 1
    for key, check in checks.items():
        print(f"{key}: {check['status']}")
        for error in check['errors']:
            print('  ERROR: ' + error)
        for warning in check['warnings']:
            print('  NOTE: ' + warning)
    print('检查报告：' + str(output))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
