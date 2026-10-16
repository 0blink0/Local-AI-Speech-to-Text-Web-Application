# 测试音频（Test Samples）

本目录存放自动化测试与手动验证用的音频样本。

## 必需文件

| 文件 | 内容 | 规格 |
| --- | --- | --- |
| `chinese.wav` | 中文普通话，1–2 句完整语句 | 16 kHz / 单声道 / 16-bit PCM WAV / 5–15 秒 |
| `english.wav` | 英语，1–2 句完整语句 | 同上 |
| `reference.txt` | 与音频逐字对应的人工参考文本 | 见下方格式 |

## 当前样本

两份样本均为**人工合成语音**（Windows SAPI 文本转语音，中文 Huihui / 英文 Zira），
**不含真人语音与个人信息**，可随仓库自由分发。

| 文件 | 时长 | 峰值 | 来源 |
| --- | --- | --- | --- |
| `chinese.wav` | 11.21 s | 0.851 | Windows SAPI 语音合成（zh-CN, Huihui） |
| `english.wav` | 9.29 s | 0.704 | Windows SAPI 语音合成（en-US, Zira） |

实测（`model=small`，见 `docs/test-report.md`）：两份样本的识别相似度均为 **1.000**。

> **为什么不放真人录音**：开发过程中曾用作者本人的真实对话录音做过测试，结论是
> "真实录音比合成语音难得多，`base` 模型会在真实录音上明显退化"（见
> `docs/test-report.md` §3.7）。但真实录音属于个人数据，作者要求**仅用于本地测试、
> 不随仓库分发**，因此仓库里的样本统一使用合成语音；那些实测数据作为历史记录保留在
> 测试报告中。

## 获取自己的录音

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
[zh] 你好，欢迎使用本地语音识别系统。这是一段用于测试的中文音频，用来验证识别效果是否准确。
[en] Hello, welcome to the local speech recognition system. This audio sample is used to verify the accuracy of the English transcription.
```

比对时会做归一化（去标点、空白、大小写），因此标点差异不影响评分。
真实模型测试（`python -m pytest -q -m real_model`）会用它做相似度断言。

## 版权与隐私

* 仓库内样本：Windows SAPI 合成语音，无版权与隐私顾虑。
* `.gitignore` 中保留了 `samples/raw/` 规则，供开发者放置**仅本地使用**的原始录音
  （例如自测用的真人录音），这些文件不会被提交。
