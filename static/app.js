/* ============================================================
 * 本地语音识别 · 前端主逻辑
 *
 * 两条识别入口共用后端 /api/transcribe：
 *   1) 麦克风录音（MediaRecorder → WebM/Opus）—— 必做
 *   2) 文件上传（WAV / WebM / OGG / MP3 / M4A …）—— 必做
 *
 * 说明：实时识别（加分项）的后端接口 /ws/stream 与其测试仍然保留，
 * 但前端暂不启用——整段录音的识别质量更稳定。详见 README「实时识别」一节。
 * ============================================================ */
(function () {
  "use strict";

  // ----------------------------------------------------------- DOM
  const $ = (id) => document.getElementById(id);

  const els = {
    language: $("language"),
    languageHint: $("language-hint"),

    statusDot: $("status-dot"),
    statusText: $("status-text"),
    timer: $("timer"),
    progressFill: $("progress-fill"),

    btnStart: $("btn-start"),
    btnStop: $("btn-stop"),
    btnReset: $("btn-reset"),
    audioFormatHint: $("audio-format-hint"),

    dropzone: $("dropzone"),
    fileInput: $("file-input"),
    fileInfo: $("file-info"),
    btnUpload: $("btn-upload"),

    metaLanguage: $("meta-language"),
    metaDuration: $("meta-duration"),
    metaProcessing: $("meta-processing"),
    metaState: $("meta-state"),
    transcript: $("transcript"),
    btnCopy: $("btn-copy"),
    copyHint: $("copy-hint"),

    errorCard: $("error-card"),
    errorMessage: $("error-message"),
    errorDetail: $("error-detail"),

    playbackEmpty: $("playback-empty"),
    playbackCurrent: $("playback-current"),
    playbackAudio: $("playback-audio"),
    playbackSaved: $("playback-saved"),
    archiveBlock: $("archive-block"),
    archiveList: $("archive-list"),
    archiveDir: $("archive-dir"),

    engineInfo: $("engine-info"),
  };

  const LANGUAGE_LABEL = { zh: "中文（普通话）", en: "English" };
  const MAX_SECONDS = 60;
  const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
  const TIMESLICE_MS = 250; // 录音分片收集间隔，降低长录音丢片风险

  const state = {
    phase: "idle", // idle | recording | working | done | error
    mediaRecorder: null,
    mediaStream: null,
    chunks: [],
    startedAt: 0,
    frozenSeconds: 0, // 停止录音后冻结的时长，用于展示
    ticker: null,
    stopping: false,
    selectedFile: null,
    lastText: "",
  };

  // ----------------------------------------------------------- 状态与提示
  function setStatus(text, phase) {
    state.phase = phase;
    els.statusText.textContent = text;
    els.statusDot.dataset.state = phase;
    els.metaState.textContent =
      {
        idle: "等待中",
        ready: "就绪",
        recording: "录音中",
        working: "识别中",
        done: "识别完成",
        error: "失败",
      }[phase] || text;
  }

  function showError(message, detail) {
    els.errorCard.hidden = false;
    els.errorMessage.textContent = message;
    els.errorDetail.textContent = detail || "";
    setStatus("失败：" + message, "error");
    els.btnStop.disabled = true;
    els.btnStart.disabled = false;
  }

  function clearError() {
    els.errorCard.hidden = true;
    els.errorMessage.textContent = "";
    els.errorDetail.textContent = "";
  }

  function setMeta({ language, duration, processing }) {
    if (language !== undefined) {
      els.metaLanguage.textContent = LANGUAGE_LABEL[language] || language || "—";
    }
    if (duration !== undefined) {
      els.metaDuration.textContent = duration == null ? "—" : duration.toFixed(2) + " s";
    }
    if (processing !== undefined) {
      els.metaProcessing.textContent = processing == null ? "—" : processing.toFixed(2) + " s";
    }
  }

  function setTranscript(text) {
    state.lastText = text || "";
    els.transcript.textContent = state.lastText || "（未识别到有效语音）";
    els.btnCopy.disabled = !state.lastText;
  }

  // ----------------------------------------------------------- 计时与 60s 限制
  function elapsedSeconds() {
    return state.startedAt ? (performance.now() - state.startedAt) / 1000 : 0;
  }

  function renderTimer(seconds) {
    const capped = Math.max(0, Math.min(seconds, MAX_SECONDS));
    els.timer.textContent = capped.toFixed(1) + "s / " + MAX_SECONDS + "s";
    els.progressFill.style.width = Math.min(100, (capped / MAX_SECONDS) * 100) + "%";
  }

  function startTicker() {
    state.startedAt = performance.now();
    state.frozenSeconds = 0;
    state.stopping = false;
    stopTicker();
    renderTimer(0);
    state.ticker = setInterval(() => {
      renderTimer(elapsedSeconds());
      if (elapsedSeconds() >= MAX_SECONDS) {
        stopRecording(true); // F1：超过 60 秒自动结束录音
      }
    }, 100);
  }

  function stopTicker() {
    if (state.ticker) clearInterval(state.ticker);
    state.ticker = null;
  }

  /** 停止录音时立即冻结计时与进度条，避免"已停止但进度条仍在跑"。 */
  function freezeTimer() {
    state.frozenSeconds = Math.min(elapsedSeconds(), MAX_SECONDS);
    stopTicker();
    renderTimer(state.frozenSeconds);
  }

  function resetTimer() {
    state.startedAt = 0;
    state.frozenSeconds = 0;
    stopTicker();
    renderTimer(0);
  }

  // ----------------------------------------------------------- 错误归一化
  async function readError(response) {
    let payload = null;
    try {
      payload = await response.json();
    } catch (_) {
      /* 非 JSON 响应 */
    }
    const message =
      (payload && payload.error && payload.error.message) ||
      "请求失败（HTTP " + response.status + "）";
    const code = (payload && payload.error && payload.error.code) || "HTTP_" + response.status;
    return { message, code };
  }

  // ----------------------------------------------------------- 上传识别（录音与文件共用）
  async function transcribeBlob(blob, filename, source) {
    const language = els.language.value;
    const form = new FormData();
    form.append("file", blob, filename);
    form.append("language", language);
    // source=recording 时服务端会把原始录音存档到 recordings/，便于回听与排查
    if (source) form.append("source", source);

    setStatus("正在上传并识别…", "working");
    setMeta({ language });

    let response;
    try {
      response = await fetch("/api/transcribe", { method: "POST", body: form });
    } catch (err) {
      showError("无法连接后端服务，请确认服务已启动。", String(err));
      return null;
    }

    if (!response.ok) {
      const { message, code } = await readError(response);
      showError(message, "错误码：" + code);
      return null;
    }

    const result = await response.json();
    setMeta({
      language: result.language,
      duration: result.duration_seconds,
      processing: result.processing_seconds,
    });
    setTranscript(result.text);
    setStatus(result.text ? "识别完成" : "识别完成（未检测到有效语音）", "done");
    return result;
  }

  // ----------------------------------------------------------- 麦克风录音
  function pickMimeType() {
    const candidates = [
      "audio/webm;codecs=opus",
      "audio/webm",
      "audio/ogg;codecs=opus",
      "audio/ogg",
      "audio/mp4",
    ];
    if (typeof MediaRecorder === "undefined") return "";
    for (const type of candidates) {
      if (MediaRecorder.isTypeSupported(type)) return type;
    }
    return "";
  }

  function guessExtension(mimeType) {
    if (!mimeType) return ".webm";
    if (mimeType.indexOf("webm") >= 0) return ".webm";
    if (mimeType.indexOf("ogg") >= 0) return ".ogg";
    if (mimeType.indexOf("mp4") >= 0) return ".m4a";
    if (mimeType.indexOf("wav") >= 0) return ".wav";
    return ".webm";
  }

  function describeMediaError(err) {
    switch (err && err.name) {
      case "NotAllowedError":
      case "SecurityError":
        return "麦克风权限被拒绝。请在浏览器地址栏左侧的权限图标中允许本页面使用麦克风，然后重试。";
      case "NotFoundError":
      case "DevicesNotFoundError":
        return "未检测到可用的麦克风设备，请插入麦克风后重试。";
      case "NotReadableError":
      case "TrackStartError":
        return "麦克风被其它程序占用，无法读取音频，请关闭占用程序后重试。";
      case "OverconstrainedError":
        return "当前麦克风不支持所需的音频参数，请更换设备后重试。";
      default:
        return "无法访问麦克风：" + (err && err.message ? err.message : String(err));
    }
  }

  function stopMediaStream() {
    if (state.mediaStream) {
      state.mediaStream.getTracks().forEach((track) => track.stop());
      state.mediaStream = null;
    }
  }

  function cleanupRecording() {
    stopMediaStream();
    state.mediaRecorder = null;
    state.chunks = [];
    state.stopping = false;
    els.btnStop.disabled = true;
    els.btnStart.disabled = false;
  }

  async function startRecording() {
    clearError();
    els.btnStart.disabled = true;
    els.btnReset.disabled = true;
    setStatus("正在请求麦克风权限…", "working");

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      showError("当前浏览器不支持麦克风录音，请使用最新版 Chrome 或 Edge。");
      return;
    }
    if (typeof MediaRecorder === "undefined") {
      showError("当前浏览器不支持 MediaRecorder，无法录音。请使用最新版 Chrome 或 Edge。");
      return;
    }

    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
    } catch (err) {
      showError(
        describeMediaError(err),
        err && err.name ? "错误类型：" + err.name : ""
      );
      return;
    }
    state.mediaStream = stream;

    const mimeType = pickMimeType();
    let recorder;
    try {
      recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
    } catch (err) {
      showError("无法启动录音器。", String(err));
      stopMediaStream();
      return;
    }

    state.mediaRecorder = recorder;
    state.chunks = [];
    els.audioFormatHint.textContent = "录音编码：" + (recorder.mimeType || mimeType || "浏览器默认");

    recorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) state.chunks.push(event.data);
    };

    recorder.onerror = (event) => {
      showError("录音过程中发生错误。", String((event.error && event.error.name) || event));
      freezeTimer();
      cleanupRecording();
    };

    recorder.onstop = async () => {
      const type = recorder.mimeType || mimeType || "audio/webm";
      const blob = new Blob(state.chunks, { type });
      cleanupRecording();

      if (blob.size === 0) {
        showError("没有录制到任何音频数据，请确认麦克风工作正常后重试。");
        return;
      }

      // 先把录音挂到播放器，用户可立即回听（不必等识别完成）
      showRecordingPlayback(blob);

      const result = await transcribeBlob(blob, "recording" + guessExtension(type), "recording");
      if (result) {
        els.playbackSaved.textContent = "本次录音已存档到服务端 recordings/ 目录";
        await refreshArchive();
      } else {
        els.playbackSaved.textContent = "本次录音未能识别，但已保留在本页可回听。";
      }
      els.btnReset.disabled = false;
    };

    recorder.start(TIMESLICE_MS);
    setStatus("录音中…（最长 60 秒）", "recording");
    els.btnStop.disabled = false;
    els.btnStart.disabled = true;
    startTicker();
  }

  function stopRecording(auto) {
    if (state.stopping) return;

    const recorder = state.mediaRecorder;
    if (!recorder || recorder.state === "inactive") {
      // 没有在进行中的录音：仅清理状态
      if (state.phase === "recording") {
        freezeTimer();
        cleanupRecording();
        setStatus("就绪", "ready");
      }
      return;
    }

    state.stopping = true;
    // 关键：立即冻结计时与进度条，不要让它在识别期间继续跑
    freezeTimer();
    setStatus(auto ? "已达 60 秒上限，正在识别…" : "正在识别…", "working");
    els.btnStop.disabled = true;

    try {
      recorder.stop();
    } catch (err) {
      showError("停止录音失败。", String(err));
      cleanupRecording();
    }
  }

  // ----------------------------------------------------------- 录音回放与存档
  let currentObjectUrl = null;

  function formatBytes(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / 1024 / 1024).toFixed(2) + " MB";
  }

  function formatTime(unixSeconds) {
    const d = new Date(unixSeconds * 1000);
    const pad = (n) => String(n).padStart(2, "0");
    return (
      d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) +
      " " + pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds())
    );
  }

  /** 把刚录到的音频挂到播放器上，供用户回听。 */
  function showRecordingPlayback(blob) {
    if (currentObjectUrl) URL.revokeObjectURL(currentObjectUrl);
    currentObjectUrl = URL.createObjectURL(blob);
    els.playbackAudio.src = currentObjectUrl;
    els.playbackCurrent.hidden = false;
    els.playbackEmpty.hidden = true;
    els.playbackSaved.textContent = "本次录音（尚未提交）· " + formatBytes(blob.size);  }

  /** 没有本次录音、也没有历史存档时，才显示"还没有录音"提示。 */
  function updatePlaybackEmptyHint(hasArchive) {
    const hasCurrent = !els.playbackCurrent.hidden;
    els.playbackEmpty.hidden = hasCurrent || hasArchive;
  }

  async function refreshArchive() {
    try {
      const response = await fetch("/api/recordings?limit=10");
      if (!response.ok) throw new Error("HTTP " + response.status);
      const { recordings } = await response.json();
      if (!recordings.length) {
        els.archiveBlock.hidden = true;
        updatePlaybackEmptyHint(false);
        return;
      }
      els.archiveBlock.hidden = false;
      updatePlaybackEmptyHint(true);
      els.archiveList.textContent = "";
      recordings.forEach((item) => {
        const li = document.createElement("li");

        const label = document.createElement("span");
        label.className = "archive-meta";
        label.textContent = formatTime(item.created_at) + " · " + formatBytes(item.size_bytes);

        const player = document.createElement("audio");
        player.controls = true;
        player.preload = "none";
        player.src = item.url + "?t=" + Math.floor(item.created_at);

        const name = document.createElement("code");
        name.className = "archive-name";
        name.textContent = item.filename;

        li.append(name, label, player);
        els.archiveList.appendChild(li);
      });
    } catch (err) {
      els.archiveBlock.hidden = true;
      updatePlaybackEmptyHint(false);
    }
  }

  // ----------------------------------------------------------- 文件上传
  function setSelectedFile(file) {
    if (!file) {
      state.selectedFile = null;
      els.fileInfo.textContent = "未选择文件";
      els.btnUpload.disabled = true;
      return;
    }
    if (file.size === 0) {
      showError("所选文件为空，请重新选择。", file.name);
      state.selectedFile = null;
      els.btnUpload.disabled = true;
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      showError(
        "文件过大，服务端上限为 10 MB。",
        file.name + "（" + (file.size / 1024 / 1024).toFixed(2) + " MB）"
      );
      state.selectedFile = null;
      els.btnUpload.disabled = true;
      return;
    }
    clearError();
    state.selectedFile = file;
    els.fileInfo.textContent =
      "已选择：" + file.name + "（" + (file.size / 1024).toFixed(1) + " KB）";
    els.btnUpload.disabled = false;
  }

  async function uploadSelected() {
    if (!state.selectedFile) return;
    clearError();
    els.btnUpload.disabled = true;
    const result = await transcribeBlob(state.selectedFile, state.selectedFile.name, "upload");
    els.btnUpload.disabled = false;
    if (!result) setStatus("识别失败", "error");
  }

  // ----------------------------------------------------------- 重置
  function resetAll() {
    if (state.phase === "recording") {
      stopRecording(false); // 录音中先正常停止，等本次识别完成后再重置
      return;
    }
    cleanupRecording();
    clearError();
    resetTimer();
    setSelectedFile(null);
    els.fileInput.value = "";
    setTranscript("");
    setMeta({ language: els.language.value, duration: null, processing: null });
    setStatus("就绪", "ready");
    els.btnReset.disabled = true;
  }

  // ----------------------------------------------------------- 引擎信息
  async function loadEngineInfo() {
    try {
      const response = await fetch("/api/info");
      if (!response.ok) throw new Error("HTTP " + response.status);
      const info = await response.json();
      const loaded = info.model_loaded ? "已加载" : "未加载（首次识别时会加载）";
      els.engineInfo.textContent =
        "引擎：" + info.engine +
        " · 模型：" + info.model +
        " · 设备：" + info.device + "/" + info.compute_type +
        " · 模型状态：" + loaded +
        " · 采样率：" + info.sample_rate + " Hz" +
        " · Hub 端点：" + (info.hf_endpoint || "—") +
        " · PyPI 源：" + (info.pypi_mirror || "—") +
        (info.load_error ? " · 加载错误：" + info.load_error : "");
    } catch (err) {
      els.engineInfo.textContent = "无法读取系统状态，请确认后端服务已启动。";
    }
  }

  // ----------------------------------------------------------- 事件绑定
  els.btnStart.addEventListener("click", () => {
    els.btnReset.disabled = false;
    startRecording();
  });

  els.btnStop.addEventListener("click", () => stopRecording(false));
  els.btnReset.addEventListener("click", resetAll);

  els.language.addEventListener("change", () => {
    const value = els.language.value;
    els.languageHint.textContent = "当前选择：" + LANGUAGE_LABEL[value] + "。";
    if (state.phase === "idle" || state.phase === "done") setMeta({ language: value });
  });

  els.btnUpload.addEventListener("click", uploadSelected);

  els.fileInput.addEventListener("change", (event) => {
    setSelectedFile(event.target.files && event.target.files[0]);
  });

  ["dragenter", "dragover"].forEach((type) => {
    els.dropzone.addEventListener(type, (event) => {
      event.preventDefault();
      els.dropzone.classList.add("dragover");
    });
  });
  ["dragleave", "drop"].forEach((type) => {
    els.dropzone.addEventListener(type, (event) => {
      event.preventDefault();
      els.dropzone.classList.remove("dragover");
    });
  });
  els.dropzone.addEventListener("drop", (event) => {
    const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
    if (file) setSelectedFile(file);
  });

  els.btnCopy.addEventListener("click", async () => {
    if (!state.lastText) return;
    try {
      await navigator.clipboard.writeText(state.lastText);
      els.copyHint.textContent = "已复制到剪贴板。";
    } catch (_) {
      // 降级方案：临时 textarea + execCommand
      const area = document.createElement("textarea");
      area.value = state.lastText;
      document.body.appendChild(area);
      area.select();
      try {
        document.execCommand("copy");
        els.copyHint.textContent = "已复制到剪贴板。";
      } catch (err) {
        els.copyHint.textContent = "复制失败，请手动选中文本复制。";
      }
      document.body.removeChild(area);
    }
    setTimeout(() => (els.copyHint.textContent = ""), 2500);
  });

  // ----------------------------------------------------------- 初始化
  resetTimer();
  setMeta({ language: els.language.value, duration: null, processing: null });
  setStatus("就绪", "ready");
  els.languageHint.textContent = "当前选择：" + LANGUAGE_LABEL[els.language.value] + "。";
  loadEngineInfo();
  refreshArchive();
})();
