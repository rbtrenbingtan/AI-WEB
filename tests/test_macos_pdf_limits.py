"""The macOS watchdog must terminate unsafe workers without large allocations."""
from pathlib import Path
import subprocess

import pytest

from backend.ai import ingestion


class FakeWorker:
    pid = 12345

    def __init__(self):
        self.returncode = None
        self.killed = False
        self.waited = False

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        self.waited = True
        if self.returncode is None:
            raise subprocess.TimeoutExpired(["worker"], timeout)
        return self.returncode


@pytest.fixture
def worker(monkeypatch):
    process = FakeWorker()
    monkeypatch.setattr(ingestion.subprocess, "Popen", lambda *args, **kwargs: process)
    return process


def test_macos_memory_limit_terminates_worker(monkeypatch, worker):
    monkeypatch.setattr(
        ingestion.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, str(512 * 1024 + 1), ""),
    )
    with pytest.raises(ingestion.PDFError, match="512 MiB"):
        ingestion._run_macos_worker(["worker"], 60)
    assert worker.killed and worker.waited


@pytest.mark.parametrize("sample", [
    subprocess.CompletedProcess(["ps"], 1, "", ""),
    subprocess.CompletedProcess(["ps"], 0, "not-a-number", ""),
    subprocess.CompletedProcess(["ps"], 0, "-1", ""),
])
def test_macos_monitor_failure_terminates_worker(monkeypatch, worker, sample):
    monkeypatch.setattr(ingestion.subprocess, "run", lambda *args, **kwargs: sample)
    with pytest.raises(ingestion.PDFError, match="无法监测"):
        ingestion._run_macos_worker(["worker"], 60)
    assert worker.killed and worker.waited


def test_macos_monitor_launch_failure_terminates_worker(monkeypatch, worker):
    def unavailable(*args, **kwargs):
        raise OSError("ps unavailable")

    monkeypatch.setattr(ingestion.subprocess, "run", unavailable)
    with pytest.raises(ingestion.PDFError, match="无法监测"):
        ingestion._run_macos_worker(["worker"], 60)
    assert worker.killed and worker.waited


def test_macos_total_deadline_includes_memory_sample(monkeypatch, worker):
    clock = iter([0.0, 0.0, 1.0])
    monkeypatch.setattr(ingestion.time, "monotonic", lambda: next(clock))
    observed_timeouts = []

    def sample(*args, **kwargs):
        observed_timeouts.append(kwargs["timeout"])
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(ingestion.subprocess, "run", sample)
    with pytest.raises(subprocess.TimeoutExpired):
        ingestion._run_macos_worker(["worker"], 1)
    assert observed_timeouts == [0.5]
    assert worker.killed and worker.waited


def test_macos_expired_deadline_terminates_without_sampling(monkeypatch, worker):
    clock = iter([0.0, 1.0])
    monkeypatch.setattr(ingestion.time, "monotonic", lambda: next(clock))
    with pytest.raises(subprocess.TimeoutExpired):
        ingestion._run_macos_worker(["worker"], 0.1)
    assert worker.killed and worker.waited


def test_text_pdf_extracts_with_platform_limits():
    sample = Path(__file__).resolve().parents[1] / "examples" / "sample_notes.pdf"
    result = ingestion.parse_pdf(sample, "limits-regression")
    assert result["page_count"] == 8
    assert result["sentences"] and result["chunks"]
