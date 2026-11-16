"""Create a 1080p narrated walkthrough from actual local UI captures.

No model calls, fake UI states, or fabricated live-generation footage are used.
Narration is synthesized separately with the adjacent PowerShell script.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import wave
from PIL import Image, ImageDraw, ImageFont, ImageOps
import imageio_ffmpeg

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / 'artifacts/submission-video'
FONT = 'C:/Windows/Fonts/msyh.ttc'
BOLD = 'C:/Windows/Fonts/msyhbd.ttc'
TEAL = '#14213D'
INK = '#334155'


def font(size: int, bold: bool = False):
    return ImageFont.truetype(BOLD if bold else FONT, size)


def wrap(text: str, f, max_width: int) -> list[str]:
    lines, line = [], ''
    for char in text:
        if f.getlength(line + char) > max_width and line:
            lines.append(line)
            line = ''
        line += char
    if line:
        lines.append(line)
    return lines


def base_frame(scene: dict, index: int, count: int) -> Image.Image:
    im = Image.new('RGB', (1920, 1080), '#F8FAFC')
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, 1920, 10), fill=TEAL)
    d.text((55, 37), '知证学伴  /  '+scene['tag'], font=font(23), fill=TEAL)
    d.text((55, 82), scene['title'], font=font(46, True), fill=INK)
    d.text((1442, 43), '知证学伴 · 项目演示', font=font(25), fill=TEAL)
    d.text((1442, 88), f'{index + 1:02d} / {count:02d}', font=font(24), fill='#64748B')
    d.rounded_rectangle((48, 167, 1431, 952), radius=20, fill='white', outline='#CBD5E1', width=2)
    source = ROOT / 'docs/assets' / scene['image'] if scene.get('image') else ROOT / 'artifacts/submission-diagrams' / (scene['diagram']+'.png')
    bounds = (1367, 767) if scene.get('image') else (1340, 550)
    pic = ImageOps.contain(Image.open(source).convert('RGB'), bounds, method=Image.Resampling.LANCZOS)
    im.paste(pic, (56 + (1367 - pic.width) // 2, 175 + (767 - pic.height) // 2))
    if scene.get('diagram'):
        d.text((92, 204), '实现方案示意', font=font(29), fill=TEAL)
        d.text((92, 820), '源码中的生成与复核流程', font=font(26), fill='#64748B')
    x = 1470
    d.text((x, 200), '本段看点', font=font(26, True), fill=TEAL)
    y = 271
    for n, point in enumerate(scene['points'], 1):
        d.ellipse((x, y + 4, x + 35, y + 39), fill='#EEF2FF')
        d.text((x + 11, y + 5), str(n), font=font(21, True), fill=TEAL)
        lines = wrap(point, font(27), 355)
        d.multiline_text((x + 50, y), '\n'.join(lines), font=font(27), fill=INK, spacing=13)
        y += max(113, len(lines) * 41 + 35)
    d.line((x, 788, 1865, 788), fill='#CBD5E1', width=2)
    d.multiline_text((x, 819), '实际操作画面讲解\n演示模式 · 无真实模型调用\n普通话合成解说', font=font(23), fill='#64748B', spacing=15)
    d.rectangle((0, 977, 1920, 1080), fill=TEAL)
    d.rectangle((0, 1074, int(1920 * (index + 1) / count), 1079), fill='#FFE066')
    return im


def main() -> None:
    scenes = json.loads((ROOT / 'docs/video-storyboard.json').read_text('utf-8'))
    narration = json.loads((WORK / 'narration.json').read_text('utf-8-sig'))
    timeline = []
    params = None
    audio = []
    total = 0.0
    frames = {}
    for i, segment in enumerate(narration['segments']):
        with wave.open(str(WORK / segment['wav']), 'rb') as wav:
            new_params = (wav.getnchannels(), wav.getsampwidth(), wav.getframerate())
            if params is None:
                params = new_params
            assert params == new_params, 'All narration segments must use the same PCM format.'
            samples = wav.readframes(wav.getnframes())
            count = wav.getnframes()
        gap = int(params[2] * 0.28)
        audio.append(samples + b'\0' * gap * params[0] * params[1])
        duration = (count + gap) / params[2]
        scene_index = segment['scene']
        if scene_index not in frames:
            frames[scene_index] = base_frame(scenes[scene_index], scene_index, len(scenes))
        im = frames[scene_index].copy()
        d = ImageDraw.Draw(im)
        subtitle_font = font(29)
        lines = wrap(segment['text'], subtitle_font, 1780)
        assert len(lines) <= 2, f'Subtitle too long: {segment["text"]}'
        for row, line in enumerate(lines):
            d.text(((1920 - subtitle_font.getlength(line)) / 2, 985 + (12 if len(lines) == 1 else 0) + row * 39), line, font=subtitle_font, fill='white')
        filename = f'frame-{i:03d}.png'
        im.save(WORK / filename)
        timeline.append({'image': filename, 'start': round(total, 3), 'duration': round(duration, 3), 'scene': scene_index + 1, 'subtitle': segment['text']})
        total += duration
    assert 300 <= total <= 480, f'Narration duration {total:.2f}s does not meet 5–8 minute requirement; adjust actual narration/rate.'
    with wave.open(str(WORK / 'narration.wav'), 'wb') as wav:
        wav.setnchannels(params[0])
        wav.setsampwidth(params[1])
        wav.setframerate(params[2])
        for part in audio:
            wav.writeframes(part)
    concat = ['ffconcat version 1.0']
    for segment in timeline:
        concat.extend([f"file '{segment['image']}'", f"duration {segment['duration']:.3f}"])
    concat.append(f"file '{timeline[-1]['image']}'")
    (WORK / 'frames.ffconcat').write_text('\n'.join(concat), 'utf-8')
    metadata = {'width': 1920, 'height': 1080, 'narration_duration_seconds': round(total, 3), 'voice': narration['voice'], 'rate': narration['rate'], 'presentation': 'actual UI screenshots and architecture diagrams, synthesized Mandarin narration', 'timeline': timeline}
    (WORK / 'timeline.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), 'utf-8')
    output = ROOT / 'submission/知证学伴-演示视频.mp4'
    output.parent.mkdir(exist_ok=True)
    command = [imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-hide_banner', '-loglevel', 'warning', '-f', 'concat', '-safe', '0', '-i', 'frames.ffconcat', '-i', 'narration.wav', '-r', '24', '-c:v', 'libx264', '-preset', 'fast', '-tune', 'stillimage', '-crf', '21', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '128k', '-t', f'{total:.3f}', '-movflags', '+faststart', str(output)]
    subprocess.run(command, cwd=WORK, check=True)
    print(json.dumps({'output': str(output), 'duration': total, 'frames': len(timeline), 'voice': narration['voice']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
