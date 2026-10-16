# 开发计划（Development Plan）

> 项目：Local AI Speech-to-Text Web Application
> 状态：**P0–P8 已完成**（2026-10-15）；计划在开发前评审通过，执行结果见 `docs/test-report.md`
> 目标：2 个自然日内交付可运行、可复现、带自动化测试与真实模型验证的本地中英文语音识别 Web 应用

---

## 决策确认（已评审）

| 编号 | 决策项 | 结论 |
| --- | --- | --- |
| Q1 | ASR 引擎 | **faster-whisper**（CTranslate2 + Whisper，CPU int8） |
| Q2 | Python 版本 | **安装 Python 3.12，项目内 `.venv` 虚拟环境开发**（本机现仅有 3.11.9） |
| Q3 | 实时识别 F6 | **实现**（WebSocket + AudioWorklet 分片方案，失败自动降级） |
| Q4 | 测试音频来源 | **由用户提供录音**，我方提供文件规格、占位与生成脚本（见 T8.1） |
| Q5 | 默认模型规模 | 初定 **`small`** → 依 TTS 实测改为 `base` → 依真实录音实测**改回 `small`**（见 §9） |

---

## 0. 现状核对

| 项目 | 现状 | 结论 |
| --- | --- | --- |
| 工作目录 `D:\opencode` | 仅有 `题目.md` + 空 git 仓库 | 从零搭建 |
| 本机 Python | 3.11.9（`D:\python311`） | **3.12 未安装**，需先安装（见 Q2 决策，winget 可用） |
| 虚拟环境 | 未创建 | 计划建立 `D:\opencode\.venv`（Python 3.12） |
| CPU | Intel x64，无 NVIDIA GPU | 满足"CPU 必须支持" |
| 运行环境要求 | Windows 11 x64 普通 CPU | 目标一致 |
| 交付期限 | 2 个自然日 | 见 §7 时间表 |

---

## 1. 关键技术决策

### 1.1 ASR 模型选型（核心决策）

| 候选 | 优点 | 缺点 | 结论 |
| --- | --- | --- | --- |
| **faster-whisper**（CTranslate2 + Whisper）| 单一模型同时支持中/英；CPU int8 推理；`pip` 装 CTranslate2 官方 Windows wheel，无需编译；`language` 参数可强制 zh/en | 非原生流式；中文准确率弱于阿里系模型 | **推荐默认** |
| sherpa-onnx（Zipformer/Paraformer）| 原生流式，实时加分项体验最好；CPU 极快 | 需分别下载中/英两套模型，集成复杂度上升 | 备选/加分 |
| vosk | 轻量、原生流式、离线友好 | 准确率明显偏低，中英分模型 | 备选（低配兜底） |

**默认方案（最终）：faster-whisper，模型规模 `small`（int8，CPU），可通过环境变量切到 `medium`/`large-v3`。**
> 计划评审时初定为 `small`；开发后期先用 `scripts/benchmark_models.py` 在合成语音上
> 实测后改为 `base`；随后把样本换成真实对话录音复测，`base` 中文相似度仅 0.611
> 而 `small` 为 0.986，于是**改回 `small`**。完整变更链路见 §9。
理由：一个模型覆盖中英文（满足 F3 与"中英混合自动检测"加分）、CPU 可用、pip 可装、离线可跑。
代价：非原生流式 → 实时功能（F6）采用"分片 + 滑动窗口重识别"实现，题目 §3 明确允许。

> **关键设计：ASR 引擎抽象层（`app/asr/base.py`）**。所有引擎实现同一接口，`ASR_ENGINE=mock|faster_whisper` 切换，保证测试可 Mock、模型可替换。

### 1.2 音频解码方案（风险点）

浏览器 MediaRecorder 在 Chrome/Edge 产出 `audio/webm;codecs=opus`。Python 侧解码需满足"不能要求面试官自行编译原生依赖"。

| 候选 | 说明 | 结论 |
| --- | --- | --- |
| **PyAV (`av`)** | wheel 内置 FFmpeg，支持 WAV / WebM / Ogg-Opus / MP3 / FLAC，无需系统 ffmpeg | **主解码器** |
| stdlib `wave` | 纯 Python，WAV 快速路径，无依赖风险 | 作为 WAV 优先路径 |
| `soundfile` | 不支持 WebM/Ogg-Opus | 不采用 |
| `pydub`/`ffmpeg-python` | 依赖外部 ffmpeg 二进制 | 不采用（违反约束） |

**方案：格式嗅探 → WAV 走 stdlib 快路径，其余走 PyAV；统一重采样为 16 kHz 单声道 float32。**

### 1.3 实时识别实现路径（加分项，已确认实现）

- 前端 `AudioWorklet` 采集 → 降采样 16 kHz 单声道 PCM → WebSocket 二进制分片（约每 1 s 一片）。
- 后端 `/ws/stream` 维护会话缓冲，收到分片后对**累积音频**做识别并回传 `partial`，停止时回传 `final`。
- 失败自动降级为 F1/F2 的整段模式，保证必做功能不受影响。

### 1.4 其他约定

- 语言处理：`zh` / `en` 强制传入引擎；空/非法值 → 400。
- 并发：单进程、模型线程锁保护（题目 §8 不要求并发能力）。
- 首次下载：`scripts/download_models.py` 预取权重到 `ASR_MODEL_DIR`（默认从 ModelScope 下载）；运行期设 `HF_HUB_OFFLINE=1` 验证断网可用。
- 错误契约：统一 `{"error": {"code": "...", "message": "..."}}` + 正确 HTTP 状态码。

---

## 2. 目标目录结构

```text
D:\opencode\
├── app\
│   ├── main.py                 # FastAPI app 工厂、异常处理器、静态挂载
│   ├── config.py               # 环境变量配置（模型、上限、超时）
│   ├── schemas.py              # Pydantic 请求/响应模型
│   ├── errors.py               # AppError 体系 → HTTP 映射
│   ├── api\
│   │   ├── routes.py           # GET /health, POST /api/transcribe, GET /
│   │   └── ws.py               # /ws/stream（加分项）
│   ├── core\
│   │   ├── audio.py            # 解码 + 重采样 + 时长
│   │   ├── languages.py        # zh/en 校验与别名
│   │   └── logging.py          # 结构化日志
│   ├── asr\
│   │   ├── base.py             # ASREngine ABC / ASRResult
│   │   ├── mock_engine.py      # 确定性桩引擎（测试用）
│   │   ├── faster_whisper_engine.py
│   │   └── registry.py         # 单例懒加载 + 线程锁 + warmup
│   └── services\
│       └── transcribe.py       # 编排：校验→解码→推理→计时→响应
├── static\
│   ├── index.html
│   ├── style.css
│   ├── app.js                  # 页面状态机、上传、结果渲染
│   ├── recorder.js             # MediaRecorder 录音 + 60s 限制
│   └── pcm-worklet.js          # AudioWorklet 采集（实时）
├── tests\
│   ├── conftest.py             # 合成音频 fixture、TestClient、mock 引擎
│   ├── test_health.py
│   ├── test_transcribe_api.py
│   ├── test_audio_decode.py
│   ├── test_languages.py
│   ├── test_errors.py
│   ├── test_engine_mock.py
│   ├── test_e2e_flow.py        # 前后端流程（TestClient 级）
│   └── test_engine_real.py     # @pytest.mark.real_model，默认 skip
├── samples\
│   ├── chinese.wav
│   ├── english.wav
│   └── reference.txt
├── scripts\
│   └── download_models.py
├── docs\
│   ├── development-plan.md     # 本文件
│   ├── requirements.md
│   ├── design.md
│   ├── test-report.md
│   └── ai-development.md
├── requirements.txt
├── requirements-dev.txt
├── pytest.ini
├── README.md
└── .gitignore
```

---

## 3. 接口契约（冻结，开发中不随意变更）

### GET `/health` → 200
```json
{ "status": "ok" }
```

### GET `/` → 200 HTML 页面

### POST `/api/transcribe`
`multipart/form-data`：`file`(File, 必需) + `language`(String, `zh`|`en`, 必需)

成功 200：
```json
{
  "text": "你好，欢迎使用语音识别系统。",
  "language": "zh",
  "duration_seconds": 5.2,
  "processing_seconds": 1.8
}
```

错误映射：

| 场景 | 状态码 | error.code |
| --- | --- | --- |
| 缺 file / 缺 language / language 非法 | 400 | `MISSING_FILE` / `INVALID_LANGUAGE` |
| 空文件 | 400 | `EMPTY_FILE` |
| 音频无法解码 | 400 | `DECODE_FAILED` |
| 格式不受支持 | 415 | `UNSUPPORTED_FORMAT` |
| 文件超 10 MB | 413 | `FILE_TOO_LARGE` |
| 模型加载失败 / 引擎不可用 | 503 | `MODEL_UNAVAILABLE` |
| 推理过程异常 | 500 | `INFERENCE_FAILED` |
| 无有效语音 | 200 | `text: ""`（不报错） |

### GET `/ws/stream`（加分项，自行新增）
客户端 → `{"language":"zh"}` 握手，随后二进制 16 kHz PCM 分片；
服务端 → `{"type":"partial","text":"..."}` / `{"type":"final","text":"..."}` / `{"type":"error",...}`。

---

## 4. 开发阶段与任务清单

> 每项含：交付物 + 验收标准。★ = 阻塞后续的关键路径任务。

### P0 环境与脚手架（约 1 h）
| ID | 任务 | 交付物 | 验收标准 |
| --- | --- | --- | --- |
| T0.0 ★ | 安装 Python 3.12（winget），创建 `D:\opencode\.venv` | 3.12 解释器 + venv | `python -V` 显示 3.12.x |
| T0.1 | 建目录骨架、`.gitignore`、`requirements.txt`、`requirements-dev.txt`、`pytest.ini` | 上述文件 | 目录结构与 §2 一致 |
| T0.2 | 依赖安装并验证 | — | `pip install -r requirements.txt` 成功；`python -c "import fastapi,av,faster_whisper"` 无错 |

### P1 后端骨架 + 接口契约（约 2 h）
| ID | 任务 | 交付物 | 验收标准 |
| --- | --- | --- | --- |
| T1.1 ★ | `config.py`（env：`ASR_ENGINE/ASR_MODEL/ASR_MODEL_DIR/ASR_COMPUTE_TYPE/MAX_UPLOAD_MB=10/HF_ENDPOINT`） | 配置模块 | 默认值可用，env 可覆盖 |
| T1.2 | `errors.py` + 全局异常处理器 | 统一错误 JSON | 任意异常都返回 §3 结构 |
| T1.3 | `schemas.py` 响应模型 | — | 字段与契约一致 |
| T1.4 ★ | `/health`、`/`、`/api/transcribe`（先接 mock 引擎） | routes | curl 三个接口均符合契约 |
| T1.5 | `main.py` app 工厂 + static 挂载 | — | `uvicorn app.main:app` 正常启动 |
| T1.6 | `test_health.py` | 测试 | 通过 |

### P2 音频解码层（约 3 h）
| ID | 任务 | 交付物 | 验收标准 |
| --- | --- | --- | --- |
| T2.1 ★ | `core/audio.py`：格式嗅探、WAV 快路径、PyAV 解码、16k 单声道重采样、时长计算 | 解码模块 | samples/*.wav 时长误差 < 0.1 s |
| T2.2 | 错误分支：空文件 / 无法解码 / 超 10 MB / 不支持格式 | 异常类型 | 对应 §3 状态码 |
| T2.3 | `test_audio_decode.py`（合成 WAV、WebM fixture、空、损坏、静音） | 测试 | 全部通过 |

### P3 ASR 引擎抽象 + Mock（约 2 h）
| ID | 任务 | 交付物 | 验收标准 |
| --- | --- | --- | --- |
| T3.1 ★ | `asr/base.py` 接口 + `ASRResult` | 抽象层 | 接口仅依赖 numpy |
| T3.2 | `asr/mock_engine.py`（确定性输出） | 桩引擎 | 同输入同输出 |
| T3.3 | `asr/registry.py` 单例懒加载 + 线程锁 + warmup + 加载失败→503 | 注册表 | 并发调用只加载一次 |
| T3.4 | `test_engine_mock.py` | 测试 | 通过 |

### P4 真实模型集成（约 3 h）
| ID | 任务 | 交付物 | 验收标准 |
| --- | --- | --- | --- |
| T4.1 ★ | `asr/faster_whisper_engine.py`（CPU int8、强制 language、VAD 过滤静音、返回纯文本） | 引擎实现 | 真实 WAV 输出可读中/英文 |
| T4.2 | `scripts/download_models.py`（来源固定 ModelScope + 指定 `ASR_MODEL_DIR`） | 下载脚本 | 一条命令完成预取 |
| T4.3 | 离线验证 | 记录 | `HF_HUB_OFFLINE=1` 下识别成功 |
| T4.4 | 中英混合自动检测（加分，`language=None`） | — | 可选开关 |

### P5 前端（必做功能，约 4 h）
| ID | 任务 | 交付物 | 验收标准 |
| --- | --- | --- | --- |
| T5.1 ★ | `index.html` + `style.css`：语言选择、录音区、计时、上传区、状态区、结果区、复制按钮 | 页面 | 布局清晰，状态可见 |
| T5.2 ★ | `recorder.js`：getUserMedia、开始/停止、**60 s 自动停止**、时长计时 | 录音模块 | F1 全项满足 |
| T5.3 | 上传 + 调用 `/api/transcribe`，渲染 `text`/`duration_seconds`/`processing_seconds`，含 loading 与超时提示 | 请求模块 | F2/F4 满足 |
| T5.4 | 错误提示 UI（权限拒绝、无麦克风、格式不支持、后端 5xx、可重试） | 错误处理 | F5 前端侧满足 |
| T5.5 | 复制功能 | — | 一键复制成功 |

### P6 实时识别（加分项，约 4 h）
| ID | 任务 | 交付物 | 验收标准 |
| --- | --- | --- | --- |
| T6.1 | `/ws/stream`：分片接收、滑动窗口重识别、partial/final 回传 | WS 端点 | 说话时持续返回文本 |
| T6.2 | `pcm-worklet.js` + 前端 StreamingClient | 实时采集 | 约 1 s 粒度更新 |
| T6.3 | WS 不可用时自动降级整段模式 | 降级逻辑 | 必做功能不受影响 |

### P7 测试补全（约 3 h）
| ID | 任务 | 交付物 | 验收标准 |
| --- | --- | --- | --- |
| T7.1 | `conftest.py`：合成音频 fixture、TestClient、mock 引擎注入 | fixtures | 无麦克风、无模型依赖 |
| T7.2 | 覆盖题目 §6.1 六类范围（API / 音频 / 语言 / 模型 / 错误 / 前后端流程） | 测试集 | 用例映射表见 §5 |
| T7.3 | `test_engine_real.py`（`@pytest.mark.real_model`，默认 skip） | 真实测试 | `pytest -q` 全绿且**不下载模型**；`pytest -m real_model` 可跑通 |

### P8 样本、文档与交付（约 4 h）
| ID | 任务 | 交付物 | 验收标准 |
| --- | --- | --- | --- |
| T8.1 | `samples/`：我方提供**文件规格说明**（16 kHz / 单声道 / PCM WAV / 5–15 s）、目录占位、`reference.txt` 模板与校验脚本；**音频由用户提供** | 规格 + 占位 + 校验脚本 | 用户放入文件后，校验脚本通过且可并行跑真实模型测试 |
| T8.2 | `docs/requirements.md`、`design.md`、`test-report.md`、`ai-development.md` | 4 份文档 | 覆盖题目 §7.2 内容要求 |
| T8.3 | `README.md`：环境、安装、模型初始化、启动、测试、离线说明、已知问题 | README | 他人可零上下文复现 |
| T8.4 | 交付前检查清单（§5.4）+ 手动验证 6 项记录 | 记录 | 全部勾选 |

---

## 5. 测试计划

### 5.1 用例覆盖映射（题目 §6.1）
| 测试范围 | 对应用例文件 | 关键用例 |
| --- | --- | --- |
| HTTP API | `test_health.py`, `test_transcribe_api.py` | 健康检查、正常请求、非法请求 |
| 音频处理 | `test_audio_decode.py` | 正常 WAV/WebM、空文件、损坏文件 |
| 语言参数 | `test_languages.py` | zh、en、非法语言 |
| 模型集成 | `test_engine_mock.py`, `test_engine_real.py` | 调用、结果处理、引擎异常 |
| 错误处理 | `test_errors.py` | 不支持格式、超限、模型失败 503 |
| 前后端流程 | `test_e2e_flow.py` | 上传→识别→响应完整链路 |

### 5.2 Mock 与真实模型的边界（题目硬性要求）
- `python -m pytest -q`：**全 Mock**，不联网、不下载模型、不依赖麦克风，必须全绿。
- `python -m pytest -q -m real_model`：真实 faster-whisper，识别 `samples/chinese.wav` 与 `samples/english.wav`，与 `reference.txt` 做字符/词级相似度断言（阈值宽松，避免误报）。
- README 中单列两节说明。

### 5.3 手动验证（题目 §6.3，逐项留证据）
1. 麦克风录中文并识别；2. 麦克风录英文并识别；3. 上传中文 WAV 识别；4. 上传英文 WAV 识别；5. 拒绝麦克风权限时的表现；6. 断网（`HF_HUB_OFFLINE=1`）下识别。

### 5.4 交付前检查清单（题目 §9）
- [ ] 按 README 零上下文启动成功
- [ ] 浏览器可授权麦克风、录音、停止
- [ ] 录音可提交后端处理
- [ ] 真实本地模型分别识别中英文
- [ ] 结果正确展示在页面（文本 + 时长 + 耗时）
- [ ] 上传音频可独立验证
- [ ] 8 类异常场景不崩溃
- [ ] 自动化测试实际执行 + 真实模型验证有记录
- [ ] 源码 / 文档 / 测试报告 / OpenCode 过程证据齐全

---

## 6. 两天时间表（缓冲优先）

| 时段 | 安排 |
| --- | --- |
| Day1 上午 | P0 脚手架 → P1 后端骨架（mock 打通三接口） |
| Day1 下午 | P2 音频解码 → P3 引擎抽象 → P4 接入真实模型（首次下载） |
| Day1 晚间 | P5 前端录音 + 上传，跑通手动验证 1–4 |
| Day2 上午 | P6 实时识别 → P7 测试补全与真实模型测试 |
| Day2 下午 | P8 样本 + 4 份文档 + README → 手动验证 5–6 → 检查清单 → 提交 |
| 预留 | Day2 晚间作为缓冲（模型下载慢 / 解码兼容问题的兜底） |

---

## 7. 风险与应对

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| Chromium 录音为 webm/opus，Python 难解码 | 阻塞 F2 | 主用 PyAV（自带 FFmpeg），WAV 保留 stdlib 快路径；前端可提供"转 WAV 上传"备选 |
| 模型权重下载受网络限制 | 阻塞 F3 | 来源固定为国内镜像（ModelScope 下载脚本 + `HF_ENDPOINT` 锁定 `hf-mirror.com`）+ `ASR_MODEL_DIR` 本地目录 + 下载脚本单独执行 |
| CPU 推理慢 | 体验差 | 默认 `small`（int8，约 6 倍实时）；必要时可下调档位；前端显示耗时与状态 |
| 本地 Python 3.11 与要求 3.12 不一致 | 复现风险 | **已决策**：安装 3.12 并建 `.venv`，所有依赖与测试均在 3.12 venv 内执行 |
| 中文测试音频依赖用户提供 | 阻塞 T8.1 / 真实模型测试 | 我方先交付规格 + 占位 + 校验脚本；用户音频到位前，真实模型测试先用我方可生成的合成语音跑通链路 |
| faster-whisper 实时性不足 | 加分项打折 | 分片 + 滑动窗口 + tiny 模型专用于实时通道 |

---

## 8. 变更管理

- 接口契约（§3）为冻结项，变更需显式确认。
- 每个阶段完成后跑一次 `python -m pytest -q`，保持绿色再进入下一阶段。
- 每阶段一次提交（阶段 ID 作 commit 前缀），便于回滚与过程留证。

---

### 执行期间的实际变更记录

计划不是死的：以下三处按实测数据调整了原始决策，过程与证据一并留档。

| 变更 | 时间 | 原因 | 证据 |
| --- | --- | --- | --- |
| 默认模型规模 `small` → `base` | 开发后期 | 实测 `base` 中文相似度 0.974、速度约为 `small` 的 3 倍，且实时识别由"不可用"变为"可用"（11 秒音频 6.8 秒完成，`small` 超过 60 秒未完成） | `docs/test-report.md` §3.5 / §3.6、`scripts/benchmark_models.py` |
| 默认模型规模 `base` → `small`（回退） | 交付前 | 把中文样本从 TTS 合成音频换成**真实对话录音**后复测：`base` 中文相似度骤降至 **0.611**（且输出繁体），`small` 为 **0.986**。合成语音高估了小模型的可用性。该录音仅本地测试、不随仓库分发 | `docs/test-report.md` §3.7、`docs/design.md` §4.3 |
| 中文样本由真实录音换回合成语音 | 交付前 | 作者要求真实录音仅作测试用、不随仓库分发；仓库内样本改为 Windows SAPI 合成语音并重新实测（zh/en 相似度均 1.000）。真实录音的实测数据作为历史记录保留 | `samples/README.md`、`docs/test-report.md` §3.11 |
| 新增 `ASR_INITIAL_PROMPT_ZH` 但默认关闭 | 开发后期 | 提示词可把中文输出偏置为简体，但会在短音频上诱发幻觉；用数据决定默认关 | `docs/test-report.md` §3.8、§4 #7 |
| 实时识别最小缓冲 0.4 s → 1.0 s | 开发后期 | 更短的缓冲上 Whisper 容易产生幻觉 | `docs/test-report.md` §4 #7 |
| 依赖与模型下载固定为国内源 | 交付前 | 本机实测：PyPI 官方源读超时、HuggingFace 官方域名连接被重置、镜像 CDN 仅 25 KB/s；ModelScope 6.4 MB/s。保留多来源会导致"某源不可用时行为不一致"，故只保留国内镜像为唯一来源 | README §3.4、`requirements.txt` 首行、`scripts/download_models.py`、`app/config.py::apply_network_source()` |

---

## 9. 评审结论与执行约定

§1 决策确认表中的 5 项已拍板，**接口契约（§3）与目录结构（§2）视为冻结**。执行期间：

1. 变更冻结项需显式确认后再动。
2. 每完成一个阶段跑 `python -m pytest -q`，绿色才进入下一阶段。
3. 每阶段一次 commit，消息以阶段 ID 开头（如 `P2: audio decode layer`）。
4. `samples/` 目前是我方生成的临时验证音频，等待用户换成自己的录音
   （`python scripts/prepare_samples.py --zh 中文录音 --en 英文录音`）。

### 我方待办的前置动作
- [x] 安装 Python 3.12（`D:\python312`）并在 `D:\opencode\.venv` 建立虚拟环境。
- [x] 下载并校验模型权重（ModelScope 源，483.5 MB，见 `docs/test-report.md` §3.1）。

### 需用户配合的动作
- [ ] 提供你自己的 `samples/chinese.wav` 与 `samples/english.wav`（规格：16 kHz / 单声道 /
      PCM WAV / 5–15 s），以及对应人工参考文本。
      当前 `samples/` 中是我方生成的**临时验证音频**（Windows SAPI 语音合成，非真人录音）：
      `python scripts/prepare_samples.py --zh 你的中文录音 --en 你的英文录音` 即可一键替换。
- [ ] 在有麦克风的机器上补做「浏览器麦克风录制中文/英文并识别」的人工验证
      （本机为无麦克风环境，见 `docs/test-report.md` §5）。
