# 系统设计（design.md）

---

## 1. 总体架构

```text
┌──────────────────────────────── 浏览器 (Chrome / Edge) ────────────────────────────────┐
│  index.html + style.css                                                                 │
│  app.js        页面状态机 · 结果渲染 · 复制 · 错误提示                                    │
│  recorder.js 逻辑（内联于 app.js）: MediaRecorder 录音 · 60s 上限 · 计时                  │
│  pcm-worklet.js  AudioWorklet：麦克风 Float32 帧 → 主线程                                 │
│                                                                                         │
│         ┌── MediaRecorder(WebM/Opus) ──► POST /api/transcribe ──┐                        │
│         └── 上传文件(WAV/WebM/…) ──────► POST /api/transcribe ──┤                        │
│         └── AudioWorklet → 16k PCM ───► WS /ws/stream ──────────┤                        │
└─────────────────────────────────────────────────────────────────┼───────────────────────┘
                                                                  │ HTTP / WebSocket
┌──────────────────────────────── 后端 (FastAPI / Uvicorn) ───────┼───────────────────────┐
│  app/main.py        应用工厂 · 统一异常处理 · 静态资源挂载        │                        │
│  app/api/routes.py  /health · /api/transcribe · / · /api/info    │                        │
│  app/api/ws.py      /ws/stream（实时识别）                       │                        │
│         │                                                        ▼                        │
│  app/services/transcribe.py   识别编排：校验 → 解码 → 推理 → 计时                        │
│         │                              │                                                  │
│         ▼                              ▼                                                  │
│  app/core/audio.py              app/asr/registry.py                                       │
│  解码 + 重采样 + 时长            单例 · 懒加载 · 预热 · 失败缓存                          │
│         │                              │                                                  │
│         │                              ▼                                                  │
│         │                     app/asr/base.py  ASREngine 抽象                             │
│         │                        ├── faster_whisper_engine.py  (生产)                     │
│         │                        └── mock_engine.py             (测试)                     │
│         ▼                                                                                 │
│  16 kHz 单声道 float32  ──────►  faster-whisper (CTranslate2, int8, CPU)                   │
│                                  权重目录: models/ （首次联网下载，之后离线）             │
└───────────────────────────────────────────────────────────────────────────────────────────┘
```

### 分层与依赖方向

```text
api (HTTP/WS)  ──►  services (编排)  ──►  core (音频/语言) + asr (引擎)
                                              ▲
                                        config / errors / schemas（横向基础）
```

* **api 层**只负责协议：解析请求、调用服务、序列化响应、翻译异常为 HTTP。
* **services 层**是唯一的识别入口，HTTP 与 WebSocket 共用，避免逻辑分叉。
* **asr 层**通过 `ASREngine` 抽象隔离模型，使测试可用 Mock 替换真实模型。
* **core 层**不依赖 FastAPI，可独立单元测试。

---

## 2. 核心模块设计

### 2.1 `app/core/audio.py` —— 音频解码

**问题**：浏览器 `MediaRecorder` 在 Chrome/Edge 上产出 `audio/webm;codecs=opus`，
而 Python 生态里多数库要么不支持 WebM/Ogg-Opus，要么依赖系统级 ffmpeg 二进制
（题目禁止要求评委自行安装原生依赖）。

**方案**：两级解码，统一输出 **16 kHz / 单声道 / float32**。

| 顺序 | 解码器 | 覆盖格式 | 说明 |
| --- | --- | --- | --- |
| 1 | **PyAV**（wheel 内置 FFmpeg） | WAV / WebM-Opus / OGG / MP3 / FLAC / M4A | 主路径，带高质量重采样（SoXR/FFmpeg resampler） |
| 2 | 标准库 `wave` | 纯 PCM WAV | 兜底，保证"至少支持 WAV"不依赖任何第三方库 |

**错误分类**（决定返回 400 还是 415）：

```text
空字节                       → 400 EMPTY_FILE
超出大小上限                 → 413 FILE_TOO_LARGE
已知但不支持的容器（AMR）     → 415 UNSUPPORTED_FORMAT
未知容器 + 两种解码器都失败   → 415 UNSUPPORTED_FORMAT（大概率不是音频）
已知音频容器但解不出音频帧    → 400 DECODE_FAILED（损坏或纯视频）
```

**时长计算**：直接由解码后的采样点数 / 16000 得出，避免依赖容器头里的
可能不准确的时长字段（WebM/Opus 常带编码延迟与 padding）。

### 2.2 `app/asr/` —— 引擎抽象与注册表

```python
class ASREngine(ABC):
    def load(self) -> None: ...                     # 幂等，可多次调用
    @property
    def is_loaded(self) -> bool: ...
    def transcribe(self, samples, sample_rate, language) -> ASRResult: ...
```

* **抽象的价值**：`python -m pytest -q` 用 `MockEngine` 全速跑通所有链路，
  真实模型测试与生产运行用 `FasterWhisperEngine`，接口完全一致。
* **注册表单例 + 懒加载**：模型权重只加载一次；`ensure_ready()` 在首次请求或
  启动预热时加载。
* **失败缓存**：加载失败原因被记住，后续请求立即返回 503，而不是每次都长时间
  阻塞（否则"模型加载失败"场景下页面会长时间无响应，违反 F4/F5）。
* **线程安全**：`threading.RLock` 保护加载与推理；CTranslate2 推理串行化。
* **预热不致命**：`lifespan` 中预热失败只记日志，服务照常启动，
  `/health` 仍返回 200（F5 要求页面不能因模型问题整体不可用）。

### 2.3 `app/services/transcribe.py` —— 识别编排

```text
normalize_language()            校验语言（含别名归一化）
ensure_ready()                  确保模型就绪（不计入 processing_seconds）
── 计时开始 ──
decode_audio()                  解码 + 重采样 + 计算时长
engine.transcribe()             推理
── 计时结束 ──
返回 TranscribeOutcome(text, language, duration_seconds, processing_seconds)
```

* `language` 回传的是**请求所选语言**（题目 §4.2 明确要求），而不是模型检测出的语言。
* 引擎抛出的任何非预期异常都被包装成 `INFERENCE_FAILED`（500），消息可读。
* HTTP 路径通过 `run_in_threadpool` 执行，避免阻塞事件循环。

### 2.4 `app/api/ws.py` —— 实时识别

Whisper 不是原生流式模型。题目 §3 明确允许"通过短音频分片实现近实时识别"，
因此采用**累积缓冲 + 滑动窗口重识别**：

```text
客户端每 ~0.5s 发一片 16k int16 PCM
  │
  ▼
服务端累积到 session.buffer
  │  当"自上次识别以来新增音频 ≥ 0.8s"且"总时长 ≥ 0.4s"
  ▼
在 threadpool 中对整个 buffer 重新识别 → 回传 {"type":"partial","text":...}
  │
  ▼
收到 {"type":"stop"} → 对完整 buffer 最后一次识别 → 回传 final
```

设计取舍：

| 取舍 | 选择 | 原因 |
| --- | --- | --- |
| 每次都全量重识别 vs 增量拼接 | **全量重识别** | 避免分片边界丢词、重复词；代价是随音频变长延迟上升，但题目不要求毫秒级 |
| 缓冲上限 | 120 s，超出丢弃最旧音频 | 防止内存无界增长 |
| 前端采样 | `AudioWorklet`，降级 `ScriptProcessorNode` | 避免已废弃 API 导致功能不可用 |
| 失败处理 | WS 建立失败 → 自动回退整段模式 | 加分项不能损害必做功能 |

### 2.5 前端状态机

```text
idle ──开始录音──► recording ──停止/60s──► working ──成功──► done
  ▲                    │                      │                │
  │                    │ 权限拒绝/设备错误      │ 4xx/5xx        │
  │                    ▼                      ▼                │
  └────────────────── error ◄─────────────────┘                │
  └───────────────────────── 重置 ◄─────────────────────────────┘
```

* 按钮禁用态与状态严格互斥（录音中不可再点开始；识别中不可重复提交）。
* `stopping` 标志防止 60 秒计时器与手动停止重复触发。
* 错误统一渲染到「出错了」卡片，包含可读消息与错误码。

---

## 3. 音频处理流程（端到端）

### 3.1 录音 → 识别（必做路径）

```text
getUserMedia()                       权限申请；失败 → 映射为中文提示
  → MediaRecorder(mimeType=audio/webm;codecs=opus)
  → ondataavailable 每 250ms 收集 Blob 分片
  → 停止（手动 或 计时器 60s）
  → new Blob(chunks) ──POST multipart──► /api/transcribe
  → 读取（限 10MB）→ 解码（PyAV/Opus→16k mono）→ 推理（faster-whisper）
  → {text, language, duration_seconds, processing_seconds}
  → 渲染结果 + 恢复按钮
```

### 3.2 实时路径（加分）

```text
getUserMedia → AudioContext(48k) → AudioWorkletNode(pcm-capture)
  → Float32 帧 → 主线程线性插值降采样到 16k
  → 累积到 0.5s → Int16 → WebSocket 二进制帧
  → 服务端滑窗重识别 → partial → 页面实时刷新
  → stop → final → 结果区
```

---

## 4. ASR 模型选型及理由

### 4.1 候选对比

| 候选 | 中英文 | CPU | Windows 安装 | 原生流式 | 结论 |
| --- | --- | --- | --- | --- | --- |
| **faster-whisper** | 单模型同时支持 | int8 可用，速度好 | 官方 wheel，`pip` 免编译 | 否，但可分片近似 | **采用** |
| openai-whisper | 支持 | PyTorch CPU 较慢、内存高 | wheel 大 | 否 | 不采用（重） |
| sherpa-onnx | 需中/英两套模型 | 极快 | wheel 可用 | **是** | 备选；集成成本更高 |
| vosk | 需中/英两套模型 | 很快 | wheel 可用 | 是 | 不采用（精度偏低） |
| 云端 API | — | — | — | — | **禁止** |
| Web Speech API | — | — | — | — | **禁止**（云端能力） |

### 4.2 选择 faster-whisper 的理由

1. **一个模型覆盖两种语言**：F3 要求"输出中文/英文"且支持中英混合自动检测
   （加分），Whisper 的多语言能力天然满足，无需维护两套模型与两套语言路由。
2. **CPU 友好**：CTranslate2 的 int8 量化让 `small` 模型在普通开发机 CPU 上
   可将 5–10 秒音频在数秒内转写，满足题目"普通 CPU"要求。
3. **安装无痛**：`pip install faster-whisper` 即可，依赖 `ctranslate2`
   与 `onnxruntime` 均有 Windows x64 wheel，**不触碰题目禁止的编译型原生依赖**。
4. **离线友好**：权重可预下载到指定目录（`ASR_MODEL_DIR`），之后设置
   `HF_HUB_OFFLINE=1` 即可完全离线识别。
5. **可控的精度/速度权衡**：`tiny/base/small/medium` 通过环境变量一键切换，
   低配机器可退到 `base`。

### 4.3 代价与缓解

| 代价 | 缓解措施 |
| --- | --- |
| 非原生流式 | 分片 + 累积重识别（题目允许）；实时通道建议用小模型 |
| 首次需下载数百 MB | 独立下载脚本 + HuggingFace 镜像 / ModelScope 双源 + 权重不入库 |
| 中文精度不及部分专用模型 | 默认 `small`；可通过 `ASR_MODEL` 升到 `medium` |
| 中文偶尔输出繁体 | 提供 `ASR_INITIAL_PROMPT_ZH` 提示词；但因短音频幻觉风险默认关闭（见 test-report §3.5） |

---

## 5. 错误契约与可观测性

统一错误体，前端只需要处理一种结构：

```json
{ "error": { "code": "MACHINE_READABLE", "message": "可直接展示给用户的中文说明" } }
```

由 `app/main.py` 注册的四类处理器保证覆盖：`AppError`、请求校验错误、
Starlette `HTTPException`、未捕获 `Exception`。

可观测性：

* `/api/info` 暴露引擎/模型/设备/是否已加载/加载错误，便于快速定位环境问题。
* 日志记录每次识别的语言、时长、处理耗时与字符数。
* 启动日志打印引擎配置，预热结果明确记录成功或失败原因。

---

## 6. 与题目要求的对应关系

| 题目条款 | 设计落点 |
| --- | --- |
| §4.1 `/health` | `api/routes.py::health`，严格返回 `{"status":"ok"}` |
| §4.2 `/api/transcribe` | `api/routes.py::transcribe` + `services/transcribe.py` |
| §4 页面 | `api/routes.py::index` + `static/` |
| F5 上限 10 MB | `_read_limited` + `Settings.max_upload_bytes` |
| §5.2 禁 Docker/Node/DB/CUDA | 依赖清单仅含 pip wheel；前端零构建 |
| §5.3 统一启动 | `app/main.py` 暴露 `app` 变量，`uvicorn app.main:app` 可直达 |
| §6.1 区分 Mock/真实测试 | `ASREngine` 抽象 + `mock_engine` + `test_engine_real.py` |
