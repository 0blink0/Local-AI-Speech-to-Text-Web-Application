# 测试音频（Test Samples）

本目录存放自动化测试与手动验证用的音频样本。

## 必需文件

| 文件 | 内容 | 规格 |
| --- | --- | --- |
| `chinese.wav` | 中文普通话，1–2 句完整语句 | 16 kHz / 单声道 / 16-bit PCM WAV / 5–15 秒 |
| `english.wav` | 英语，1–2 句完整语句 | 同上 |
| `reference.txt` | 与音频逐字对应的人工参考文本 | 见下方格式 |

## 获取录音

`scripts/prepare_samples.py` 可以把**任意常见格式**（WAV / WebM / OGG / MP3 / M4A / FLAC）
统一转换为上述规格并做校验：

```bash
python scripts/prepare_samples.py --zh 原始中文录音.m4a --en 原始英文录音.mp3
python scripts/prepare_samples.py --check      # 只校验
```

脚本会打印时长、原始采样率/声道、峰值与 RMS，便于确认录音质量。

## reference.txt 格式

每行一条，`[语言码]` 开头，`#` 开头为注释：

```text
[zh] 你好，欢迎使用语音识别系统。
[en] Hello, welcome to the speech recognition system.
```

真实模型测试（`python -m pytest -q -m real_model`）会用它做相似度断言。

## 版权与隐私

录音由本项目作者自行录制提供，不含敏感个人信息，允许随本项目一同分发。
