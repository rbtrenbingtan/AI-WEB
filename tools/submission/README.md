# 本地提交材料构建

这些工具只用于制作和检查材料，不是 Web 应用运行依赖。所有命令在项目根目录执行。视频使用真实操作截图、逐句字幕和 Windows 普通话合成讲解；不冒充连续录屏、真人配音或真实模型调用。

## 环境

- 项目 Python 3.11+ 虚拟环境和 Node.js 20.19+。
- `python -m pip install -r tools/submission/requirements.txt`。
- PDF 和视频默认使用 Windows 微软雅黑字体；PDF 可通过 `--font`、`--bold-font` 指定其他兼容中文字体。
- 配音脚本需要 Windows PowerShell、System.Speech 和已安装的 zh-CN 声音。本次为 Microsoft Huihui Desktop，语速 2。
- 渲染检查 PDF 使用 Poppler 的 `pdftoppm`，视频编码使用 imageio-ffmpeg 自带的 FFmpeg。

## 内容与证据

- `docs/report-content.json`：技术文档的逐页内容。
- `docs/video-storyboard.json`：视频分镜、旁白和画面指向。
- `docs/assets/`：本次本地浏览器实际验收截图。更改界面或演示数据后应重新实际操作并截图，不能用设计稿冒充实现。
- `artifacts/latest-tests.xml`：后端测试实际输出，不随源码包分发，可按下方命令重新产生。

## 重建

```powershell
.\.venv\Scripts\python.exe -m pytest -q --junitxml=artifacts/latest-tests.xml
npm --prefix frontend test -- --run
.\.venv\Scripts\python.exe tools/submission/build_report.py --frontend-tests 15
powershell -NoProfile -ExecutionPolicy Bypass -File tools/submission/synthesize_narration.ps1 -Rate 2
.\.venv\Scripts\python.exe tools/submission/build_video.py
```

前端计数参数必须取自当次实际测试结果。本次为后端 73、前端 15；不得只修改参数制造通过记录。PDF 生成器还会检查后端 JUnit 失败数。

输出为 `submission/知证学伴-技术文档.pdf`（本次 17 页）和 `submission/知证学伴-演示视频.mp4`（本次约 7 分 27 秒、1920×1080、24 fps、H.264/AAC）。配音速度、操作内容或字体变化可能改变时长及版式，重建后必须复验。

## 生成源码与真实历史包

先审核并正常提交本地变更，然后从实际提交导出。不要修改历史时间或伪造开发过程。

```powershell
git archive --format=zip --output=submission/知证学伴-源码.zip HEAD
git bundle create submission/知证学伴-完整历史.bundle --all
.\.venv\Scripts\python.exe tools/submission/check_package.py
```

检查器核对 PDF 页数与章节关键词、视频时长/分辨率/音轨、源码必需文件与敏感路径、bundle 完整性，并记录 SHA-256。关键词命中不等于内容已通过评审。仍需渲染查看全部 PDF 页、播放视频确认画面和解说，并从 bundle 独立克隆验证历史。

本地材料目录被 Git 忽略，以免把生成视频和重复源码提交进历史；本工具目录会正常跟踪。源码 ZIP 不带 `.git`，完整历史在 `.bundle` 中。正式参赛时仍需按比赛要求发布评审可访问的 GitHub/Gitee 仓库，并在规定截止时间前提交。
