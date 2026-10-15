# Local AI Speech-to-Text · 本地语音识别 Web 系统

基于 **B/S 架构** 的本地语音识别系统。浏览器麦克风录音或上传音频文件，
由**本地部署的开源 ASR 模型**（faster-whisper，CPU 推理）将中文普通话或英语
转换为文字并展示在页面上。**全程不调用任何云端识别 API**，模型准备完成后可
**完全离线运行**。

---

## 1. 功能一览

| 编号 | 功能 | 说明 |
| --- | --- | --- |
| F1 | 浏览器麦克风录音 | 权限申请、开始/停止、状态与时长提示、单次最长 60 秒自动停止、可反复录音 |
| F2 | 音频文件上传 | 支持 WAV 与浏览器录音格式（WebM/OGG-Opus），与录音共用同一后端识别逻辑 |
| F3 | 本地中英文识别 | 页面可选中文/英文；中文输出中文、英文输出英文（不做翻译） |
| F4 | 结果展示 | 识别语言、状态、识别文本、音频时长、处理耗时，文本可一键复制 |
| F5 | 异常处理 | 权限拒绝、无麦克风、空文件、损坏音频、格式不支持、超限、模型失败、无有效语音均不崩溃 |
| F6 | 实时识别（加分项） | 边说边出文字：AudioWorklet 采集 → 16 kHz PCM → WebSocket 分片 → 滑动窗口重识别 |

---

## 2. 环境要求

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Windows 11 x64（开发环境 macOS / Linux 亦可） |
| Python | 3.12 |
| 浏览器 | Chrome / Edge 最新稳定版（通过 `http://127.0.0.1:8000` 访问） |
| 硬件 | 普通 CPU 即可，**不要求 NVIDIA GPU / CUDA** |
| 网络 | 仅**首次初始化模型**需要联网；之后可完全离线 |

不需要 Docker、Node.js、数据库、云服务或任何需要自行编译的原生依赖。
全部依赖均为 `pip` 可安装的 wheel。

---

## 3. 安装与启动

在项目根目录（含 `app/` 与 `requirements.txt` 的目录）执行：

```bash
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

然后浏览器打开：<http://127.0.0.1:8000>

> 首次启动会自动加载模型。若权重尚未下载，会先从 HuggingFace 拉取
> （`small` 模型约数百 MB，请耐心等待），这也正是题目允许的"首次安装/初始化下载"。
> 想提前下载或网络受限，请见下一节。

### 3.1 模型初始化（推荐，可选）

```bash
# 默认从 HuggingFace 下载 small 模型到项目内 models/small/
python scripts/download_models.py

# 国内网络推荐：改用 ModelScope（实测速度明显更快）
python scripts/download_models.py --source modelscope

# HuggingFace 网络受限时使用镜像
python scripts/download_models.py --mirror

# 换更小的模型（更快、精度略低）
python scripts/download_models.py --model base --source modelscope
```

下载完成后**无需任何额外配置**：应用会自动发现 `models/<size>/` 下的权重
（见 `app/asr/registry.py::resolve_model_path`）。权重目录已被 `.gitignore`
排除，不会提交到仓库。

> 若两个源都无法访问，也可以手动把 CTranslate2 格式的权重放到
> `models/small/`（需含 `model.bin`、`config.json`、`tokenizer.json`），
> 或用 `ASR_MODEL=/绝对路径/到/模型目录` 指定。

### 3.2 离线运行验证

```bash
# Windows
set HF_HUB_OFFLINE=1

# macOS / Linux
export HF_HUB_OFFLINE=1

python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

在 `HF_HUB_OFFLINE=1` 下仍能正常识别，即证明识别过程不依赖外网。

---

## 4. 页面使用

1. **选择识别语言**：中文（普通话）或 English。
2. **麦克风录音**：
   - 点击「开始录音」，浏览器会请求麦克风权限，请选择「允许」。
   - 状态灯与计时器显示录音进度，进度条满 60 秒会自动停止。
   - 点击「停止录音」后自动提交识别，结果显示在「识别结果」区域。
   - 勾选「实时识别」时，录音过程中会持续显示已识别文本（加分项）。
3. **文件上传**：把音频文件拖入上传区或点击选择文件，然后点「识别该文件」。
4. **复制结果**：点击结果区右上角「复制」按钮即可复制识别文本。

---

## 5. 接口说明

| Method | Endpoint | 功能 |
| --- | --- | --- |
| GET | `/health` | 服务健康检查 |
| POST | `/api/transcribe` | 上传音频并识别 |
| GET | `/` | 打开 Web 页面 |
| GET | `/api/info` | 当前引擎/模型状态（额外提供，便于排障） |
| WS | `/ws/stream` | 实时识别（加分项） |

### `GET /health`

```json
{ "status": "ok" }
```

### `POST /api/transcribe`

`multipart/form-data`，字段：`file`（音频文件）、`language`（`zh` 或 `en`）。

成功返回 `200`：

```json
{
  "text": "你好，欢迎使用语音识别系统。",
  "language": "zh",
  "duration_seconds": 5.2,
  "processing_seconds": 1.8
}
```

* `processing_seconds` 覆盖音频解码与模型推理，不含首次模型下载/加载时间。

错误响应统一为：

```json
{ "error": { "code": "INVALID_LANGUAGE", "message": "……" } }
```

| 场景 | 状态码 | code |
| --- | --- | --- |
| 未上传文件 | 400 | `MISSING_FILE` |
| `language` 缺失或非法 | 400 | `INVALID_LANGUAGE` |
| 空文件 | 400 | `EMPTY_FILE` |
| 音频无法解码 | 400 | `DECODE_FAILED` |
| 格式不受支持 | 415 | `UNSUPPORTED_FORMAT` |
| 文件超过 10 MB | 413 | `FILE_TOO_LARGE` |
| 模型不可用 | 503 | `MODEL_UNAVAILABLE` |
| 推理失败 | 500 | `INFERENCE_FAILED` |
| 无有效语音 | 200 | `text` 为空字符串 |

### `WS /ws/stream`（实时识别）

客户端发送 `{"type":"start","language":"zh"}` → 持续发送 16 kHz 单声道 int16 PCM
二进制分片 → 发送 `{"type":"stop"}`。服务端返回 `ready` / `started` /
`partial` / `final` / `error` 消息。

---

## 6. 测试

### 6.1 常规自动化测试（不联网、不下模型、不需要麦克风）

```bash
python -m pytest -q
```

全部用例使用 **Mock 引擎**，覆盖 HTTP API、音频解码、语言参数、模型集成、
错误处理与前后端流程。若尚未下载模型，这条命令**不会**触发任何下载。

### 6.2 真实本地模型测试（中英文真实识别）

```bash
# 先准备模型与样本（见下）
python scripts/download_models.py
python -m pytest -q -m real_model
```

该命令会使用真实 faster-whisper 模型识别 `samples/chinese.wav` 与
`samples/english.wav`，并与 `samples/reference.txt` 中的人工参考文本做
相似度断言（默认阈值 0.5，可用 `REAL_MODEL_MIN_SIMILARITY` 调整）。

这三条命令的分工是题目 §6.1 的硬性要求：**必须区分 Mock 测试与真实模型测试**。

### 6.3 测试音频

见 [`samples/README.md`](samples/README.md)。转换任意格式录音到规定规格：

```bash
python scripts/prepare_samples.py --zh 中文录音.m4a --en 英文录音.mp3
python scripts/prepare_samples.py --check
```

---

## 7. 配置项（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ASR_ENGINE` | `faster_whisper` | 引擎：`faster_whisper` 或 `mock`（测试用） |
| `ASR_MODEL` | `small` | 模型规模：`tiny`/`base`/`small`/`medium`/`large-v3`，或本地模型目录 |
| `ASR_MODEL_DIR` | `项目内 models/` | 权重缓存目录；`models/<size>/` 存在时自动优先使用 |
| `ASR_DEVICE` | `cpu` | 推理设备 |
| `ASR_COMPUTE_TYPE` | `int8` | 量化类型，CPU 推荐 `int8` |
| `ASR_BEAM_SIZE` | `1` | 解码 beam size，越大越慢越准 |
| `ASR_INITIAL_PROMPT_ZH` | 空 | 中文初始提示词；可用于把输出偏置为简体或注入热词（见「已知问题」） |
| `ASR_INITIAL_PROMPT_EN` | 空 | 英文初始提示词 |
| `ASR_WARMUP` | `1` | 启动时是否预热模型 |
| `MAX_UPLOAD_MB` | `10` | 上传大小上限 |
| `MAX_RECORD_SECONDS` | `60` | 单次录音上限（前端计时同步） |
| `HF_ENDPOINT` | 空 | HuggingFace 镜像，如 `https://hf-mirror.com` |
| `LOG_LEVEL` | `INFO` | 日志级别 |

例：用更小的模型快速启动

```bash
set ASR_MODEL=base
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

---

## 8. 项目结构

```text
local-speech-to-text/
├── app/
│   ├── main.py                # FastAPI 入口与应用工厂、异常处理器
│   ├── config.py              # 环境变量配置
│   ├── errors.py              # 统一错误体系
│   ├── schemas.py             # 请求/响应模型
│   ├── api/
│   │   ├── routes.py          # /health、/api/transcribe、/、/api/info
│   │   └── ws.py              # /ws/stream 实时识别
│   ├── core/
│   │   ├── audio.py           # 解码 + 重采样 + 时长
│   │   ├── languages.py       # 语言校验与归一化
│   │   └── logging.py         # 日志
│   ├── asr/
│   │   ├── base.py            # 引擎抽象接口
│   │   ├── faster_whisper_engine.py
│   │   ├── mock_engine.py     # 测试用确定性桩引擎
│   │   └── registry.py        # 单例懒加载 + 预热
│   └── services/
│       └── transcribe.py      # 识别编排（HTTP 与 WS 共用）
├── static/                    # 前端（原生 HTML/CSS/JS，无构建步骤）
├── tests/                     # pytest 用例
├── samples/                   # 测试音频与参考文本
├── scripts/                   # 模型下载、样本准备
├── docs/                      # 需求、设计、测试报告、AI 开发记录
├── requirements.txt
└── README.md
```

---

## 9. 技术选型摘要

| 决策 | 选择 | 理由 |
| --- | --- | --- |
| ASR 模型 | **faster-whisper**（CTranslate2 版 Whisper） | 单模型覆盖中英文；CPU int8 可用；官方 Windows wheel，`pip` 免编译；支持离线 |
| 音频解码 | **PyAV**（wheel 自带 FFmpeg）+ 标准库 `wave` 兜底 | 必须直接支持 Chrome 的 `audio/webm;codecs=opus`；无需系统安装 ffmpeg |
| 后端 | FastAPI + Uvicorn | 题目要求；异步 + 自动文档 |
| 前端 | 原生 HTML/CSS/JS | 避免引入 Node.js 构建链（题目禁止将其作为运行必需） |
| 实时识别 | WebSocket + 滑动窗口重识别 | Whisper 非原生流式，题目 §3 明确允许短分片近似实时 |

详细设计见 [`docs/design.md`](docs/design.md)。

---

## 10. 已知问题与限制

* Whisper 对部分音频会输出**繁体中文**（如「歡迎」）。如需统一为简体，可设置
  `ASR_INITIAL_PROMPT_ZH=以下是普通话的句子。`；但实测该提示词在**短音频**上可能诱发
  幻觉，因此默认关闭，取舍交由使用者。
* 实时识别为"分片 + 累积重识别"（Whisper 非原生流式）：每收到新分片就对整个缓冲重新
  识别，计算量随时长近似平方增长。6 秒音频流畅，长录音（>15 秒）在 CPU + `small` 模型下
  中间结果会滞后。实时通道建议改用 `base`/`tiny`（题目不要求严格毫秒级延迟）。
* 单进程串行推理（题目 §8 不要求并发）；多人同时识别会排队。
* 60 秒上限只在前端录音生效；上传音频只受 10 MB 体积限制（有意为之）。
* 不支持的输入（如 AMR、纯视频文件）返回明确的 415/400 提示，不会被静默处理。
* 首次启动需联网下载权重（约 483 MB）；如需完全离线部署，请先执行第 3.1 节。

---

## 11. 文档索引

| 文档 | 内容 |
| --- | --- |
| [docs/requirements.md](docs/requirements.md) | 需求理解、功能拆分、假设与边界条件 |
| [docs/design.md](docs/design.md) | 系统架构、核心模块、音频流程、ASR 选型理由 |
| [docs/test-report.md](docs/test-report.md) | 测试用例、执行结果、真实模型验证、已知问题 |
| [docs/ai-development.md](docs/ai-development.md) | OpenCode 使用过程、任务拆分、问题与解决 |
| [docs/development-plan.md](docs/development-plan.md) | 开发计划与任务分解 |
