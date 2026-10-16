# 测试音频（Test Samples）

本目录存放自动化测试与手动验证用的音频样本。

## 必需文件

| 文件 | 内容 | 规格 |
| --- | --- | --- |
| `chinese.wav` | 中文普通话，1–2 句完整语句 | 16 kHz / 单声道 / 16-bit PCM WAV / 5–15 秒 |
| `english.wav` | 英语，1–2 句完整语句 | 同上 |
| `reference.txt` | 与音频逐字对应的人工参考文本 | 见下方格式 |

当前样本：

| 文件 | 时长 | 峰值 | 来源 |
| --- | --- | --- | --- |
| `chinese.wav` | 7.16 s | 0.700 | 真实录音（模拟面试录音，截取 21.04–28.20 s 并做峰值归一化） |
| `english.wav` | 9.29 s | 0.704 | Windows SAPI 语音合成（Zira, en-US） |

> 中文样本取自真实对话录音，比合成语音更贴近实际使用场景，也更能暴露模型差异
> （实测在真实录音上 `base` 的中文相似度仅 0.611，`small` 为 0.986，见
> `docs/test-report.md`）。

## 原始录音

`samples/raw/` 存放未经处理的原始录音，**已被 `.gitignore` 排除，不随仓库分发**：

* 原始文件体积大（其中一段约 28 MB）；
* 录音为真实对话，含个人信息（姓名、雇主、工作经历等），不适合随交付物公开。

`samples/chinese.wav` 只保留了其中 7 秒的通用引导语，不含任何个人信息。

## 获取录音

`scripts/prepare_samples.py` 可以把**任意常见格式**（WAV / WebM / OGG / MP3 / M4A / FLAC / AAC）
统一转换为上述规格并做校验：

```bash
python scripts/prepare_samples.py --zh 原始中文录音.m4a --en 原始英文录音.mp3
python scripts/prepare_samples.py --check      # 只校验
```

脚本会打印时长、原始采样率/声道、峰值与 RMS，便于确认录音质量。

> 实测遇到过扩展名为 `.m4a` 但内容实为裸 AAC/ADTS 流的录音。解码按**内容**判定，
> 与扩展名无关，因此这类文件同样可以处理。

## reference.txt 格式

每行一条，`[语言码]` 开头，`#` 开头为注释：

```text
[zh] 然后具体聊一下你之前的一些工作经验和你的一些项目经验，做一个简单的自我介绍。
[en] Hello, welcome to the local speech recognition system. This audio sample is used to verify the accuracy of the English transcription.
```

比对时会做归一化（去标点、空白、大小写），因此标点差异不影响评分。
真实模型测试（`python -m pytest -q -m real_model`）会用它做相似度断言。

## 版权与隐私

录音由本项目作者自行录制提供，不含敏感个人信息，允许随本项目一同分发。
原始录音不随仓库分发（见上）。
