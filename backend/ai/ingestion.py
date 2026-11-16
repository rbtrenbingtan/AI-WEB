"""PDF extraction runs in a disposable, memory-limited process with a hard deadline."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


class PDFError(ValueError):
    pass


def _memory_limit() -> None:
    # A process cap prevents a tiny compressed PDF from exhausting the application host.
    memory_bytes = 512 * 1024 * 1024
    if os.name != "nt":
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        return
    import ctypes
    from ctypes import wintypes
    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    limits = Extended()
    limits.BasicLimitInformation.LimitFlags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
    limits.ProcessMemoryLimit = memory_bytes
    if not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
        raise PDFError("无法启用 PDF 解析内存限制")


def split_pages(pages: list[dict], document_id: str) -> dict:
    """Offsets always refer to normalized page text; raw_text is also retained."""
    normalized = []
    edge_counts: Counter = Counter()
    for page in pages:
        raw = page.get("raw_text", page["text"])
        text = "\n".join(re.sub(r"[^\S\n]+", " ", line).strip() for line in raw.splitlines())
        item = {"page_index": page["page_index"], "text": text, "raw_text": raw}
        normalized.append(item)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for edge in set(lines[:1] + lines[-1:]):
            if len(edge) < 100:
                edge_counts[edge] += 1
    repeated = {line for line, count in edge_counts.items() if count >= max(3, len(pages) // 2)}
    sentences, chunks = [], []
    for page in normalized:
        local = []
        for match in re.finditer(r"[^。！？!?\n]+[。！？!?]?", page["text"]):
            pieces = [match]
            # Preserve indices while splitting long semicolon-delimited sentences.
            segments = [(match.start(), match.group())]
            if len(match.group()) > 400:
                segments = [(match.start() + m.start(), m.group()) for m in re.finditer(r"[^；;]+[；;]?", match.group())]
            for start, value in segments:
                if not value.strip() or value.strip() in repeated:
                    continue
                start += len(value) - len(value.lstrip())
                value = value.strip()
                order = len(sentences)
                sent = {"id": f"{document_id}_s_{order + 1:04d}", "document_id": document_id,
                        "page_index": page["page_index"], "ord": order, "text": value,
                        "char_start": start, "char_end": start + len(value)}
                sentences.append(sent)
                local.append(sent)
        bucket, length = [], 0
        def flush():
            nonlocal bucket, length
            if bucket:
                chunks.append({"id": f"{document_id}_c_{len(chunks) + 1:04d}", "document_id": document_id,
                               "page_index": page["page_index"], "first_sentence_ord": bucket[0]["ord"],
                               "last_sentence_ord": bucket[-1]["ord"], "text": "\n".join(s["text"] for s in bucket),
                               "sentence_ids": [s["id"] for s in bucket]})
            bucket, length = [], 0
        for sentence in local:
            if bucket and length + len(sentence["text"]) > 800:
                flush()
            bucket.append(sentence)
            length += len(sentence["text"])
            if length >= 500 and "\n\n" in page["text"][sentence["char_end"]:sentence["char_end"] + 3]:
                flush()
        flush()
    return {"page_count": len(normalized), "pages": normalized, "sentences": sentences, "chunks": chunks}


def _extract(path: Path, document_id: str) -> dict:
    _memory_limit()
    from pypdf import PdfReader
    reader = PdfReader(str(path), strict=False)
    if reader.is_encrypted:
        raise PDFError("不支持加密 PDF，请先解除密码保护")
    count = len(reader.pages)  # Metadata checked before any page text extraction.
    if not 1 <= count <= 30:
        raise PDFError("PDF 页数必须在 1—30 页之间")
    pages, warnings = [], []
    for index, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        if len(text) > 12000:
            raise PDFError(f"第 {index} 页文本超过 12000 字限制")
        visible = len(re.sub(r"\s", "", text))
        if visible < 20:
            warnings.append(f"第 {index} 页文字较少，可能是扫描页或空页")
        pages.append({"page_index": index, "text": text})
    if sum(len(re.sub(r"\s", "", p["text"])) for p in pages) < max(80, count * 20):
        raise PDFError("可提取文字过少，请上传文本版 PDF；本项目不支持 OCR")
    data = split_pages(pages, document_id)
    data["warnings"] = warnings
    return data


def parse_pdf(path: str | Path, document_id: str, timeout: float = 60) -> dict:
    path = Path(path).resolve()
    if not path.is_file() or path.stat().st_size > 10 * 1024 * 1024:
        raise PDFError("文件不存在或超过 10 MB 限制")
    with path.open("rb") as file:
        if file.read(5) != b"%PDF-":
            raise PDFError("文件不是有效的 PDF")
    try:
        process = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", str(path), document_id],
                                 capture_output=True, timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except subprocess.TimeoutExpired as exc:
        raise PDFError("PDF 解析超过 60 秒，已终止；请简化文件后重试") from exc
    if process.returncode != 0:
        raise PDFError("PDF 解析失败：文件损坏或超出解析资源限制")
    try:
        data = json.loads(process.stdout)
    except (ValueError, UnicodeError) as exc:
        raise PDFError("PDF 解析进程未返回有效数据") from exc
    if "error" in data:
        raise PDFError(data["error"])
    data["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return data


if __name__ == "__main__":
    try:
        result = _extract(Path(sys.argv[2]), sys.argv[3])
    except PDFError as exc:
        result = {"error": str(exc)}
    except Exception:
        result = {"error": "PDF 解析失败：文件损坏或超出解析资源限制"}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
