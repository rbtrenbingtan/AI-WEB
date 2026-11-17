# 本地提交材料构建

## 环境

- 项目 Python 3.11+ 虚拟环境和 Node.js 20.19+。
- `python -m pip install -r tools/submission/requirements.txt`。
- PDF 和视频默认使用 Windows 微软雅黑字体；PDF 可通过 `--font`、`--bold-font` 指定其他兼容中文字体。
- 配音脚本需要 Windows PowerShell、System.Speech 和已安装的 zh-CN 声音。本次为 Microsoft Huihui Desktop，语速 2。
- 渲染检查 PDF 使用 Poppler 的 `pdftoppm`，视频编码使用 imageio-ffmpeg 自带的 FFmpeg。

## 内容与证据

- `docs/report-content.json`：技术文档的逐页内容。
- `docs/video-storyboard.json`：视频分镜、旁白和画面指向。
- `docs/assets/`：本次本地浏览器实际验收截图。
- `artifacts/latest-tests.xml`：后端测试实际输出，不随源码包分发，可按下方命令重新产生。

## 重建

```powershell
.\.venv\Scripts\python.exe -m pytest -q --junitxml=artifacts/latest-tests.xml
npm --prefix frontend test -- --run
.\.venv\Scripts\python.exe tools/submission/build_report.py --frontend-tests 15
powershell -NoProfile -ExecutionPolicy Bypass -File tools/submission/synthesize_narration.ps1 -Rate 2
.\.venv\Scripts\python.exe tools/submission/build_video.py
```

输出为 `submission/知证学伴-技术文档.pdf`（本次 17 页）和 `submission/知证学伴-演示视频.mp4`（本次约 7 分 27 秒、1920×1080、24 fps、H.264/AAC）。
