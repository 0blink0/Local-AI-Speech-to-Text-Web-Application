# 测试报告（test-report.md）

> 环境：Windows 11 x64 · Intel CPU（无 NVIDIA GPU）· Python 3.12.0（项目内 `.venv`）
> 被测版本：见本仓库 commit
> 测试日期：2026-10-15

---

## 1. 测试策略

题目 §6.1 的硬性要求是"必须区分 Mock 测试和真实模型测试，不能全部依赖 Mock"。
因此测试被组织为两层，通过 `ASREngine` 抽象隔离：

| 层 | 命令 | 是否联网 | 是否需模型 | 是否需麦克风 | 用途 |
| --- | --- | --- | --- | --- | --- |
| 常规自动化 | `python -m pytest -q` | 否 | 否（Mock 引擎） | 否 | 快速回归，覆盖全部 API 与异常路径 |
| 真实模型 | `python -m pytest -q -m real_model` | 仅首次下载 | 是 | 否 | 真实 faster-whisper 的中英文识别验证 |
| 手动验证 | 见 §5 | 仅首次下载 | 是 | 部分需要 | 浏览器端到端体验与边界场景 |

保证机制：

* `pytest.ini` 中 `addopts = -m "not real_model"` —— 常规命令**永不**选中真实模型用例；
* `tests/conftest.py` 在导入应用前强制 `ASR_ENGINE=mock` —— 从根上杜绝误加载真实模型；
* 真实模型用例在模型不可用时 `pytest.skip` 而非 `fail`，保证未初始化模型的环境也能得到有意义结果。

---

## 2. 自动化测试结果（Mock 层）

```text
$ python -m pytest -q
........................................................................ [ 72%]
............................                                             [100%]
100 passed, 4 deselected in 1.48s
```

* **100 passed**：全部通过。
* **4 deselected**：`test_engine_real.py` 的 4 个真实模型用例，按设计排除。
* **1.48s**：不下载模型、不联网、不需要麦克风硬件。

### 2.1 用例分布

| 文件 | 用例数 | 覆盖范围（题目 §6.1） |
| --- | --- | --- |
| `test_health.py` | 5 | HTTP API：健康检查、页面、静态资源、结构化 404 |
| `test_transcribe_api.py` | 17 | HTTP API：正常请求、非法请求、时长、重复识别、空/过大文件 |
| `test_audio_decode.py` | 20 | 音频处理：WAV/WebM-Opus、重采样、降混、空、损坏、格式嗅探 |
| `test_languages.py` | 20 | 语言参数：中文、英文、别名、非法语言 |
| `test_engine_mock.py` | 11 | 模型集成：接口契约、注册表单例、加载失败与错误缓存 |
| `test_errors.py` | 8 | 错误处理：415/413/503/500/405 与统一错误体 |
| `test_e2e_flow.py` | 12 | 前后端流程：上传→识别、录音→识别、WebSocket 流式、前端契约 |
| `test_engine_real.py` | 4 | 真实模型（默认排除，见 §3） |

### 2.2 关键用例执行明细（节选）

| 用例 | 验证内容 | 结果 |
| --- | --- | --- |
| `test_health_returns_ok` | `/health` 严格返回 `{"status":"ok"}` | PASS |
| `test_transcribe_wav_chinese` | 中文识别返回 4 个字段且时长正确 | PASS |
| `test_duration_reflects_real_audio_length` | 5 s 音频回传 5.0 s | PASS |
| `test_transcribe_browser_recording_format` | WebM/Opus 可解码可识别 | PASS |
| `test_mono_8k_and_stereo_44k_are_resampled` | 44.1 kHz 立体声正确重采样 | PASS |
| `test_silent_audio_returns_empty_text_without_error` | 静音 → 200 + 空文本 | PASS |
| `test_oversized_file_returns_413` | 超过 10 MB → 413 `FILE_TOO_LARGE` | PASS |
| `test_corrupt_wav_raises_decode_failed` | 损坏 WAV → 400 `DECODE_FAILED` | PASS |
| `test_plain_text_raises_unsupported_format` | 非音频 → 415 `UNSUPPORTED_FORMAT` | PASS |
| `test_model_unavailable_returns_503` | 模型加载失败 → 503 且信息可读 | PASS |
| `test_ensure_ready_raises_on_load_failure_and_caches_error` | 加载失败原因被缓存，不重复阻塞 | PASS |
| `test_inference_failure_returns_500` | 推理异常 → 500 `INFERENCE_FAILED` | PASS |
| `test_can_transcribe_repeatedly` | 连续识别 3 次均成功 | PASS |
| `test_realtime_streaming_flow` | start→partial×2→stop→final 全链路 | PASS |
| `test_realtime_ignores_short_silence_and_finishes_empty` | 极短输入 → final 空文本不报错 | PASS |
| `test_frontend_contract_references_required_endpoints` | 前端确实调用规定接口并提供 zh/en 与复制 | PASS |

### 2.3 异常场景覆盖对照（题目 F5 的 8 类）

| 场景 | 对应用例 | 结果 |
| --- | --- | --- |
| 用户拒绝麦克风权限 | 前端 `NotAllowedError` 映射（手动验证 §5.5） | 见 §5 |
| 未检测到可用麦克风 | 前端 `NotFoundError` 映射 | 见 §5 |
| 上传空文件或损坏音频 | `test_empty_file_returns_400` / `test_corrupt_audio_returns_400` | PASS |
| 文件格式不受支持 | `test_unsupported_format_returns_415` | PASS |
| 录音超过 60 秒 | 前端计时器自动停止（手动验证 §5.1） | 见 §5 |
| 模型加载失败 | `test_model_unavailable_returns_503` | PASS |
| 识别过程中后端出错 | `test_inference_failure_returns_500` | PASS |
| 音频中没有可识别语音 | `test_silent_audio_returns_empty_text_without_error` | PASS |

---

## 3. 真实模型测试结果

```text
$ python -m pytest -q -m real_model -s
[zh] 相似度 : 1.000
[en] 相似度 : 1.000
4 passed, 100 deselected in 7.12s
```

### 3.1 环境与模型

| 项目 | 值 |
| --- | --- |
| 模型 | `Systran/faster-whisper-small`（CTranslate2 格式，483.5 MB） |
| 获取来源 | ModelScope（HuggingFace 官方域名在本机网络下连接被重置，见 §4 #6） |
| 本地路径 | `models/small/`（`model.bin` / `config.json` / `tokenizer.json` / `vocabulary.txt`） |
| 推理配置 | `device=cpu`，`compute_type=int8`，`beam_size=1`，`vad_filter=True` |
| 模型加载耗时 | 1.09 s（权重已在本地磁盘） |
| 离线验证 | 服务以 `HF_HUB_OFFLINE=1` 启动，模型正常加载并完成识别 → **识别过程不依赖外网** |

### 3.2 识别质量与性能

| 语言 | 音频 | 识别结果 | 人工参考文本 | 相似度 | 处理耗时 | RTF |
| --- | --- | --- | --- | --- | --- | --- |
| zh | `samples/chinese.wav`（11.33 s） | 你好,欢迎使用语音识别系统。这是本地模型的中文识别测试音频,用于验证识别效果是否准确。 | 你好，欢迎使用语音识别系统。这是本地模型的中文识别测试音频，用于验证识别效果是否准确。 | **1.000** | 1.99 s | 0.18 |
| en | `samples/english.wav`（9.29 s） | Hello, welcome to the local speech recognition system. This audio sample is used to verify the accuracy of the English transcription. | 同左 | **1.000** | 1.54 s | 0.17 |

> * 相似度 = 归一化（去标点/空白）后的 `difflib.SequenceMatcher` 比率，阈值默认 0.5，可用 `REAL_MODEL_MIN_SIMILARITY` 调整。
> * 中文唯一差异是半角逗号 `,` 与全角逗号 `，`，归一化后完全一致。
> * **RTF（实时率）= 处理耗时 / 音频时长**，0.18 表示 CPU 上约 5.5 倍实时速度，满足"s在普通 CPU 上运行"的约束。

### 3.3 真实模型 + 真实 HTTP 接口的端到端验证

用浏览器页面（Chrome）上传 `samples/chinese.wav` 与 `samples/english.wav`，页面显示：

| 语言 | 页面「识别结果」文本 | 音频时长 | 处理耗时 |
| --- | --- | --- | --- |
| 中文 | 你好,欢迎使用语音识别系统。这是本地模型的中文识别测试音频,用于验证识别效果是否准确。 | 11.33 s | 2.10 s / 1.99 s |
| English | Hello, welcome to the local speech recognition system. This audio sample is used to verify the accuracy of the English transcription. | 9.29 s | 1.62 s / 1.54 s |

### 3.4 实时识别（F6）真实模型验证

在浏览器中把 6 秒真实语音按 1 秒分片经 `/ws/stream` 发送，服务端逐步返回：

| 时间点 | 服务端返回的 partial |
| --- | --- |
| 1 s | 歡迎 |
| 2 s | 歡迎使用語音 |
| 3 s | 歡迎使用語音識別系統 |
| 4 s | 歡迎使用語音識別系統 |
| 5 s | 歡迎使用語音識別系統。 |
| 6 s | 歡迎使用語音識別系統。 |

停止后 `final`：`歡迎使用語音識別系統。`（5.0 s 音频，处理耗时 1.35 s）
—— **边说边出字、逐字增长、结果正确**，符合题目 §3 对近实时识别的要求。

### 3.5 关于中文输出为繁体

真实模型对部分音频会输出繁体（如 `歡迎`）。这是 Whisper 的已知行为，不是缺陷。
本项目提供 `ASR_INITIAL_PROMPT_ZH` 环境变量用于把输出偏置为简体：

```bash
set ASR_INITIAL_PROMPT_ZH=以下是普通话的句子。
```

**但实测该提示词在短音频上会诱发幻觉**（例如对 6 秒音频输出"这次的节目是《欢迎使用语音识别系统》。"），
因此**默认留空**，把取舍交给使用者。详细对比见 §4 #7。

---

## 4. 缺陷记录（测试过程中发现并修复）

| # | 现象 | 根因 | 修复 | 回归 |
| --- | --- | --- | --- | --- |
| 1 | WebM/Opus 解码全部抛 `DECODE_FAILED` | PyAV 19 的 `av.open()` 不再接受 `metadata_errors` 参数 | 移除该参数 | WebM 用例 PASS，真实样本 PASS |
| 2 | 立体声输入被算成双倍时长 | `encode_wav` 只写单声道，交错数据被当作双倍长度单声道 | 增加 `channels` 参数 | 降混用例 PASS |
| 3 | `en_GB` 语言别名被判为非法 | 别名表缺少 `en_gb` | 补齐别名 | 参数化用例 PASS |
| 4 | Mock 引擎对静音返回文本，与真实模型行为不一致 | Mock 未模拟"无有效语音" | 全静音返回空文本 | 静音用例 PASS |
| 5 | `registry.resolve_model_path` 缺少 `Path` 导入 | 新增函数时漏加 import | 补 import | 引擎用例 PASS |
| 6 | HuggingFace 官方域名连接被重置，模型无法下载（xet-bridge CDN 实测仅 25 KB/s） | 本机网络环境限制 | 下载脚本新增 **ModelScope 源**（实测 6.4 MB/s）与 `--mirror` | 483 MB 权重约 1 分钟下载完成并通过自检 |
| 7 | 中文提示词在短音频上诱发幻觉 | Whisper 对短音频 + `initial_prompt` 的已知倾向 | 默认关闭提示词；`_MIN_TOTAL_SECONDS` 由 0.4 s 提到 1.0 s，先攒够音频再识别 | 实时识别不再出现"这次的节目是…"类幻觉（见 §3.4） |
| 8 | 手工测试脚本用 `subarray(...).buffer` 发送音频，服务端时长翻 5 倍（5 s → 25 s） | TypedArray 的 `.buffer` 返回整个底层 ArrayBuffer，而非视图范围 | 测试改用 `slice()`；并在前端 `pushPcm` 处补充注释说明为何必须新建等长数组 | 实时分片时长与服务端一致 |

> #8 虽属测试脚本问题，但暴露了一个易犯的陷阱：**浏览器端真实代码本来就新建了等长数组**，
> 因此在应用侧未受影响；该结论已通过 `test_realtime_streaming_flow`（断言 `duration_seconds == 2.0`）锁定。

---

## 5. 手动验证（题目 §6.3 的 6 项）

| # | 验证项 | 方式 | 结果 |
| --- | --- | --- | --- |
| 1 | 浏览器麦克风录制中文并识别 | Chrome，本地服务 `127.0.0.1:8000` | 录音 / 停止 / 提交 / 展示链路已实现并通过自动化用例；**实际麦克风录音待在有麦克风的环境人工确认**（见下方说明） |
| 2 | 浏览器麦克风录制英文并识别 | 同上 | 同上 |
| 3 | 上传中文 WAV 并识别 | Chrome 上传 `samples/chinese.wav` | ✅ 识别完全正确，11.33 s / 1.99 s |
| 4 | 上传英文 WAV 并识别 | Chrome 上传 `samples/english.wav` | ✅ 识别完全正确，9.29 s / 1.54 s |
| 5 | 麦克风权限被拒绝时的表现 | Chrome 点击「开始录音」（自动化环境默认拒绝权限） | ✅ 页面显示「麦克风权限被拒绝。请在浏览器地址栏左侧的权限图标中允许本页面使用麦克风，然后重试。」并标注 `NotAllowedError`；`开始录音` 按钮恢复可用、页面未崩溃 |
| 6 | 模型准备完成后无外网情况下识别 | 以 `HF_HUB_OFFLINE=1` 启动服务，再上传两个样本 | ✅ 正常加载并正确识别，证明识别不依赖外网 |

补充验证（浏览器实测）：

| 场景 | 结果 |
| --- | --- |
| 上传非音频文件（`notaudio.txt`） | ✅ 显示「无法识别的音频格式（notaudio.txt）…」+ 错误码 `UNSUPPORTED_FORMAT`，页面可用 |
| 实时识别（6 s 真实语音分片） | ✅ partial 逐秒增长，final 正确（见 §3.4） |
| 页面控制台 | ✅ 无 JS 错误 |
| `/api/info` | ✅ 正确显示引擎、模型路径、设备、加载状态 |

> **关于第 1、2 项**：本机为无麦克风的远程环境，无法完成真人录音。但录音路径与上传路径
> **共用同一个服务端识别入口**（`services/transcribe.py::transcribe_bytes`），而上传路径已用
> 真实模型验证通过；前端录音状态机（权限、计时、60 s 自动停止、停止后提交）也已由
> `test_e2e_flow.py` 与浏览器权限拒绝用例覆盖。建议在有麦克风的机器上按 README §4 再走一遍。

---

## 6. 已知问题

1. **中文偶尔输出繁体**：Whisper 的已知行为。可用 `ASR_INITIAL_PROMPT_ZH` 偏置为简体，
   但短音频下该提示词可能诱发幻觉，故默认关闭（见 §3.5）。
2. **实时识别为"全量重识别"**：每收到新分片就对**整个缓冲**重新识别，计算量随录音时长
   近似平方增长。6 秒音频流畅（partial 间隔约 1 秒），长录音（>15 秒）在 CPU + `small`
   模型下中间结果会明显滞后。题目未要求严格毫秒级延迟，但建议实时通道改用 `base`/`tiny`。
3. **单进程串行推理**：CTranslate2 推理用锁串行化，多人同时识别会排队（题目 §8 不要求并发）。
4. **60 秒上限只在前端生效**：后端未对上传音频做时长硬限制（仅有 10 MB 体积上限），
   这是有意的——上传场景不受"录音 60 秒"约束。
5. **不支持的格式**：AMR 等已知容器直接返回 415；若文件既非已知容器又无法解码，同样返回 415
   而非 400，这是"无法确定是不是音频"时的保守选择。
6. **首次运行需联网**：模型权重需先下载（约 483 MB）。可通过 `models/` 目录预置实现完全离线部署。
7. **`/api/info` 为额外接口**：题目只要求三个接口，此接口用于排障，不影响契约。

---

## 7. 复现步骤

```bash
# 1) 准备环境
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements-dev.txt

# 2) 常规测试（不联网、不下模型）
python -m pytest -q

# 3) 模型初始化（二选一）
python scripts/download_models.py --source modelscope
python scripts/download_models.py --mirror

# 4) 真实模型测试
python -m pytest -q -m real_model

# 5) 手动验证
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# 浏览器打开 http://127.0.0.1:8000
```
