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
| F6 | 实时识别（加分项） | 后端已实现（AudioWorklet 采集 → 16 kHz PCM → WebSocket 分片 → 滑动窗口重识别）；**前端当前未启用**，见 §5.3 |
| 附加 | 录音回放与存档 | 录完可**立即回听**；录音自动存档到服务端 `recordings/`，可在页面回放历史录音 |

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

> **镜像源已内置**：`requirements.txt` 顶部带
> `--index-url https://pypi.tuna.tsinghua.edu.cn/simple`，因此上面这条安装命令
> **无需任何额外参数**即可在受限网络下正常拉取依赖。若要改用官方 PyPI 或其他镜像，
> 把该行注释掉即可（见 §3.4）。

> 首次启动会自动加载模型。若权重尚未下载，会经由国内镜像拉取
> （默认 `small` 模型约 460 MB，请耐心等待），这也正是题目允许的"首次安装/初始化下载"。
> 想提前下载请见下一节。

### 3.1 模型初始化（推荐，可选）

```bash
# 下载默认模型（small）到项目内 models/small/（来源固定为 ModelScope）
python scripts/download_models.py

# 换更大的模型（更准、更慢）
python scripts/download_models.py --model medium

# 指定保存目录
python scripts/download_models.py --dir D:\models
```

下载完成后**无需任何额外配置**：应用会自动发现 `models/<size>/` 下的权重
（见 `app/asr/registry.py::resolve_model_path`）。权重目录已被 `.gitignore`
排除，不会提交到仓库。

### 3.2 模型规模

默认使用 **`small`**：在真实对话录音上中文相似度 0.986（对照 `base` 仅 0.611，
详见 [`docs/test-report.md`](docs/test-report.md) §3.7），题目考核的核心是识别正确性，
因此以准确率优先。

不同规模在普通 CPU 上的取舍（实测数据见 [`docs/design.md`](docs/design.md) §4.3）：

| 模型 | 体积 | 中文相似度 | 说明 |
| --- | --- | --- | --- |
| `small`（默认） | 463 MB | **0.986** | 推荐；CPU 上约 6 倍实时 |
| `medium` | ~1.5 GB | 更高 | 慢，内存占用大 |
| `large-v3` | ~3 GB | 最高 | 不推荐在普通 CPU 上使用 |

如需更换（环境变量 `ASR_MODEL` 与 `--model` 参数均可）：

```bash
python scripts/download_models.py --model medium
set ASR_MODEL=medium
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

> 若无法访问 ModelScope，也可以手动把 CTranslate2 格式的权重放到
> `models/small/`（需含 `model.bin`、`config.json`、`tokenizer.json`），
> 或用 `ASR_MODEL=/绝对路径/到/模型目录` 指定。

### 3.3 离线运行验证

```bash
# Windows
set HF_HUB_OFFLINE=1

# macOS / Linux
export HF_HUB_OFFLINE=1

python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

在 `HF_HUB_OFFLINE=1` 下仍能正常识别，即证明识别过程不依赖外网。

### 3.4 网络与镜像（默认已配置为国内源）

本项目面向国内网络环境，**网络来源固定为国内镜像，且为唯一来源**，开箱即用：

| 环节 | 来源（唯一） | 生效位置 |
| --- | --- | --- |
| Python 依赖安装 | PyPI 清华镜像 `https://pypi.tuna.tsinghua.edu.cn/simple` | `requirements.txt` 首行 `--index-url` |
| 模型权重下载 | ModelScope `https://modelscope.cn` | `scripts/download_models.py`（来源常量见 `app/config.py::MODELSCOPE_ENDPOINT`） |
| 首次启动自动拉权重 | HF 镜像 `https://hf-mirror.com` | `app/config.py::HF_MIRROR_ENDPOINT`（由 `apply_network_source()` 固定写入） |

**设计取舍**：不保留官方源回退，也不提供切换开关。实测官方 PyPI 读超时、HuggingFace
官方域名连接被重置、镜像 CDN 仅 25 KB/s，而 ModelScope 约 6 MB/s。若在配置中保留多条
来源，反而会出现"某个源不可用时行为不一致"的问题；固定单一来源后行为可预测、问题可复现。

两点使用注意：

1. `requirements.txt` 里的 `--index-url` 优先级**高于**命令行 `-i`、`PIP_INDEX_URL`
   环境变量与 pip 配置文件（已实测验证），因此依赖源只能通过修改该文件调整。
2. `apply_network_source()` 会**无条件**把 `HF_ENDPOINT` 固定为镜像，外部设置的
   `HF_ENDPOINT` 不生效——这是刻意的，以保证运行环境一致。

当前生效的依赖源与 Hub 端点可在页面底部「系统状态」，或 `GET /api/info` 的
`pypi_mirror` / `hf_endpoint` 字段查看。

---

## 4. 页面使用

1. **选择识别语言**：中文（普通话）或 English。
2. **麦克风录音**：
   - 点击「开始录音」，浏览器会请求麦克风权限，请选择「允许」。
   - 状态灯与计时器显示录音进度，进度条满 60 秒会自动停止。
   - 点击「停止录音」后**计时与进度条立即冻结**，随后自动提交识别，结果显示在「识别结果」区域。
   - **录音回放**：录完即可在「录音回放」卡片里回听本次录音；该录音同时被存档到服务端
     `recordings/` 目录，并可在此卡片回放历史录音。
3. **文件上传**：把音频文件拖入上传区或点击选择文件，然后点「识别该文件」。
   （上传的文件**不会**被存档，只有麦克风录音会。）
4. **复制结果**：点击结果区「复制」按钮即可复制识别文本。

---

## 5. 接口说明

| Method | Endpoint | 功能 |
| --- | --- | --- |
| GET | `/health` | 服务健康检查 |
| POST | `/api/transcribe` | 上传音频并识别 |
| GET | `/` | 打开 Web 页面 |
| GET | `/api/info` | 当前引擎/模型状态（额外提供，便于排障） |
| GET | `/api/recordings` | 列出已存档的麦克风录音（额外提供） |
| GET | `/api/recordings/{filename}` | 回放/下载一条存档录音（额外提供） |
| WS | `/ws/stream` | 实时识别（加分项；后端已实现，前端暂未启用，见 §5.3） |

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

### 录音存档（`/api/recordings`，额外接口）

麦克风录音会在识别前**原样**存档到服务端 `recordings/` 目录（鉴权缺失，故仅建议本机使用）。
上传的文件不存档。

```
GET /api/recordings                    → {"recordings":[{"filename","size_bytes","created_at","url"}]}
GET /api/recordings/{filename}         → 音频文件本身（可直接用于 <audio> 播放）
```

文件名形如 `20261016-142131-zh-b21d.wav`（时间戳-语言-随机串，扩展名按**内容嗅探**决定）。
读取接口对文件名做白名单校验并二次确认解析路径位于存档目录内，**防路径穿越**。
关闭方式：`SAVE_RECORDINGS=0`。

### `WS /ws/stream`（实时识别，加分项）
客户端发送 `{"type":"start","language":"zh"}` → 持续发送 16 kHz 单声道 int16 PCM
二进制分片 → 发送 `{"type":"stop"}`。服务端返回 `ready` / `started` /
`partial` / `final` / `error` 消息。

> **当前状态**：接口已实现并有自动化测试覆盖（`tests/test_e2e_flow.py`），
> 但**前端未接线**。原因见下节。

### 5.3 关于实时识别为何在界面上关闭

`small` 模型的实时通道采用"分片 + 累积重识别"（Whisper 非原生流式）。实测在
CPU 上处理 11 秒语音时，全部 partial 超过 60 秒才跑完，用户点「停止」后长时间
拿不到最终结果——**体验不可用**。为保证"语音录入 → 识别成功"这条主线稳定，
前端暂时只保留整段识别；后端接口与 `pcm-worklet.js` 均保留在仓库中，
后续接入更小模型或原生流式引擎（如 sherpa-onnx）即可重新启用。

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
| `ASR_MODEL` | `small` | 模型规模：`small`/`medium`/`large-v3`，或本地模型目录（见 §3.2） |
| `ASR_MODEL_DIR` | `项目内 models/` | 权重缓存目录；`models/<size>/` 存在时自动优先使用 |
| `ASR_DEVICE` | `cpu` | 推理设备 |
| `ASR_COMPUTE_TYPE` | `int8` | 量化类型，CPU 推荐 `int8` |
| `ASR_BEAM_SIZE` | `1` | 解码 beam size，越大越慢越准 |
| `ASR_INITIAL_PROMPT_ZH` | 空 | 中文初始提示词；可用于把输出偏置为简体或注入热词（见「已知问题」） |
| `ASR_INITIAL_PROMPT_EN` | 空 | 英文初始提示词 |
| `ASR_WARMUP` | `1` | 启动时是否预热模型 |
| `MAX_UPLOAD_MB` | `10` | 上传大小上限 |
| `MAX_RECORD_SECONDS` | `60` | 单次录音上限（前端计时同步） |
| `SAVE_RECORDINGS` | `1` | 是否把麦克风录音存档到服务端 |
| `RECORDINGS_DIR` | `项目内 recordings/` | 录音存档目录 |
| `RECORDINGS_LIST_LIMIT` | `50` | 列出存档录音的最大条数 |
| `HF_ENDPOINT` | 固定为 `https://hf-mirror.com` | 由 `apply_network_source()` 强制写入，不可通过环境变量覆盖 |
| `LOG_LEVEL` | `INFO` | 日志级别 |

例：换用更大的模型

```bash
python scripts/download_models.py --model medium
set ASR_MODEL=medium
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
│   │   └── ws.py              # /ws/stream 实时识别（后端保留，前端暂未接线）
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
├── recordings/                # 麦克风录音存档（运行时生成，不入库）
├── scripts/                   # 模型下载、样本准备、模型规模对比
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
| 实时识别（加分项） | WebSocket + 滑动窗口重识别 | Whisper 非原生流式，题目 §3 明确允许短分片近似实时；后端已实现，前端因 CPU 上的延迟体验暂未接线（见 §5.3） |

详细设计见 [`docs/design.md`](docs/design.md)。

---

## 10. 已知问题与限制

* Whisper 对部分音频会输出**繁体中文**（如「歡迎」）。如需统一为简体，可设置
  `ASR_INITIAL_PROMPT_ZH=以下是普通话的句子。`；但实测该提示词在**短音频**上可能诱发
  幻觉，因此默认关闭，取舍交由使用者。
* 实时识别为"分片 + 累积重识别"（Whisper 非原生流式）：每收到新分片就对整个缓冲重新
  识别，计算量随时长近似平方增长。默认 `small` 模型下，11 秒语音的全部 partial
  超过 60 秒才跑完——因此**前端已暂时关闭该功能**（后端接口保留），
  保证主线"录音 → 识别"的稳定性不被拖累（见 §5.3）。
* 单进程串行推理（题目 §8 不要求并发）；多人同时识别会排队。
* 60 秒上限只在前端录音生效；上传音频只受 10 MB 体积限制（有意为之）。
* 不支持的输入（如 AMR、纯视频文件）返回明确的 415/400 提示，不会被静默处理。
* 首次启动需联网下载权重（默认 `small` 约 460 MB）；如需完全离线部署，
  请先执行第 3.1 节。下载来源固定为国内镜像（见 §3.4）。

---

## 11. 文档索引

| 文档 | 内容 |
| --- | --- |
| [docs/requirements.md](docs/requirements.md) | 需求理解、功能拆分、假设与边界条件 |
| [docs/design.md](docs/design.md) | 系统架构、核心模块、音频流程、ASR 选型理由 |
| [docs/test-report.md](docs/test-report.md) | 测试用例、执行结果、真实模型验证、已知问题 |
| [docs/ai-development.md](docs/ai-development.md) | OpenCode 使用过程、任务拆分、问题与解决 |
| [docs/development-plan.md](docs/development-plan.md) | 开发计划与任务分解 |
