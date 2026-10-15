/* ============================================================
 * 本地语音识别 · 前端主逻辑
 *
 * 两条识别入口共用后端 /api/transcribe：
 *   1) 麦克风录音（MediaRecorder → WebM/Opus）—— 必做
 *   2) 文件上传（WAV / WebM / OGG …）—— 必做
 * 加分项：实时识别（AudioWorklet → 16 kHz PCM → WebSocket）
 * ============================================================ */
(function () {
  "use strict";

  // ----------------------------------------------------------- DOM
  const $ = (id) => document.getElementById(id);

  const els = {
    language: $("language"),
    languageHint: $("language-hint"),
    realtime: $("realtime"),

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

    realtimeCard: $("realtime-card"),
    realtimeTranscript: $("realtime-transcript"),

    errorCard: $("error-card"),
    errorMessage: $("error-message"),
    errorDetail: $("error-detail"),

    engineInfo: $("engine-info"),
  };

  const LANGUAGE_LABEL = { zh: "中文（普通话）", en: "English" };
  const MAX_SECONDS = 60;
  const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
  const WORKLET_CHUNK_SAMPLES = 8000; // 0.5s @16k

  const state = {
    phase: "idle", // idle | recording | working | done | error
    mediaRecorder: null,
    mediaStream: null,
    chunks: [],
    startedAt: 0,
    ticker: null,
    autoStopTimer: null,
    selectedFile: null,
    lastText: "",
    // 实时识别相关
    audioContext: null,
    workletNode: null,
    sourceNode: null,
    sinkNode: null,
    socket: null,
    resampler: null,
    pcmPending: [],
    streamFinalResolve: null,
    streamHadError: false,
    stopping: false,
  };

  // ----------------------------------------------------------- 状态与提示
  function setStatus(text, phase) {
    state.phase = phase;
    els.statusText.textContent = text;
    els.statusDot.dataset.state = phase;
    els.metaState.textContent = {
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

  function updateTimer() {
    const seconds = Math.min(elapsedSeconds(), MAX_SECONDS);
    els.timer.textContent = seconds.toFixed(1) + "s / " + MAX_SECONDS + "s";
    els.progressFill.style.width = Math.min(100, (seconds / MAX_SECONDS) * 100) + "%";
  }

  function startTicker() {
    state.startedAt = performance.now();
    stopTicker();
    updateTimer();
    state.ticker = setInterval(() => {
      updateTimer();
      if (elapsedSeconds() >= MAX_SECONDS) {
        // F1：超过 60 秒自动结束录音
        stopRecording(true);
      }
    }, 100);
  }

  function stopTicker() {
    if (state.ticker) clearInterval(state.ticker);
    state.ticker = null;
    if (state.autoStopTimer) clearTimeout(state.autoStopTimer);
    state.autoStopTimer = null;
  }

  function resetTimer() {
    els.timer.textContent = "0.0s / " + MAX_SECONDS + "s";
    els.progressFill.style.width = "0%";
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

  // ----------------------------------------------------------- 上传识别（共用后端）
  async function transcribeFile(blob, filename) {
    const language = els.language.value;
    const form = new FormData();
    form.append("file", blob, filename);
    form.append("language", language);

    setStatus("正在上传并识别…", "working");
    setMeta({ language, state: "working" });

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
    setStatus(
      result.text ? "识别完成" : "识别完成（未检测到有效语音）",
      "done"
    );
    return result;
  }

  // ----------------------------------------------------------- 普通录音（MediaRecorder）
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

  async function requestMicrophone() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw Object.assign(new Error("当前浏览器不支持麦克风录音，请使用最新版 Chrome 或 Edge。"), {
        code: "UNSUPPORTED",
      });
    }
    return navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
  }

  function describeMediaError(err) {
    const name = err && err.name;
    switch (name) {
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

  async function startRecording() {
    clearError();
    els.btnStart.disabled = true;
    els.btnReset.disabled = true;
    setStatus("正在请求麦克风权限…", "working");

    let stream;
    try {
      stream = await requestMicrophone();
    } catch (err) {
      showError(describeMediaError(err), err && err.name ? "错误类型：" + err.name : "");
      return;
    }
    state.mediaStream = stream;

    if (els.realtime.checked) {
      const ok = await startStreaming(stream);
      if (ok) return;
      // 实时模式失败 → 自动降级为整段模式，保证必做功能可用
      setStatus("实时模式不可用，已切换为整段录音模式。", "working");
    }

    startSegmentRecording(stream);
  }

  function startSegmentRecording(stream) {
    const mimeType = pickMimeType();
    if (typeof MediaRecorder === "undefined") {
      showError("当前浏览器不支持 MediaRecorder，无法录音。请使用最新版 Chrome 或 Edge。");
      stopMediaStream();
      return;
    }

    let recorder;
    try {
      recorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream);
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
      await transcribeFile(blob, "recording" + guessExtension(type));
      els.btnReset.disabled = false;
    };

    recorder.start(250); // 每 250ms 收集一次数据，降低长录音丢片风险
    setStatus("录音中…（最长 60 秒）", "recording");
    els.btnStop.disabled = false;
    els.btnStart.disabled = true;
    startTicker();
  }

  function guessExtension(mimeType) {
    if (!mimeType) return ".webm";
    if (mimeType.indexOf("webm") >= 0) return ".webm";
    if (mimeType.indexOf("ogg") >= 0) return ".ogg";
    if (mimeType.indexOf("mp4") >= 0) return ".m4a";
    if (mimeType.indexOf("wav") >= 0) return ".wav";
    return ".webm";
  }

  function stopRecording(auto) {
    if (state.stopping) return;
    state.stopping = true;

    if (state.phase === "recording" && els.realtime.checked && state.socket) {
      stopStreaming(auto);
      return;
    }
    if (state.mediaRecorder && state.mediaRecorder.state !== "inactive") {
      setStatus(auto ? "已达 60 秒上限，正在结束录音…" : "正在结束录音…", "working");
      els.btnStop.disabled = true;
      try {
        state.mediaRecorder.stop();
      } catch (err) {
        showError("停止录音失败。", String(err));
        cleanupRecording();
      }
      return;
    }
    state.stopping = false;
  }

  function stopMediaStream() {
    if (state.mediaStream) {
      state.mediaStream.getTracks().forEach((track) => track.stop());
      state.mediaStream = null;
    }
  }

  function cleanupRecording() {
    stopTicker();
    stopMediaStream();
    state.mediaRecorder = null;
    state.chunks = [];
    state.stopping = false;
    els.btnStop.disabled = true;
    els.btnStart.disabled = false;
  }

  // ----------------------------------------------------------- 实时识别（WebSocket）
  function createResampler(sourceRate, targetRate) {
    let remainder = new Float32Array(0);
    return function push(input) {
      const merged = new Float32Array(remainder.length + input.length);
      merged.set(remainder, 0);
      merged.set(input, remainder.length);
      const ratio = sourceRate / targetRate;
      const outLength = Math.floor(merged.length / ratio);
      if (outLength <= 0) {
        remainder = merged;
        return null;
      }
      const output = new Float32Array(outLength);
      for (let i = 0; i < outLength; i++) {
        const pos = i * ratio;
        const i0 = Math.floor(pos);
        const i1 = Math.min(i0 + 1, merged.length - 1);
        const frac = pos - i0;
        output[i] = merged[i0] * (1 - frac) + merged[i1] * frac;
      }
      remainder = merged.slice(Math.floor(outLength * ratio));
      return output;
    };
  }

  function floatToPcm16(samples) {
    const pcm = new Int16Array(samples.length);
    for (let i = 0; i < samples.length; i++) {
      const value = Math.max(-1, Math.min(1, samples[i]));
      pcm[i] = value < 0 ? value * 0x8000 : value * 0x7fff;
    }
    return pcm;
  }

  async function startStreaming(stream) {
    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) throw new Error("浏览器不支持 Web Audio API");

      const audioContext = new AudioCtx();
      await audioContext.resume();
      const sourceRate = audioContext.sampleRate;

      const protocol = location.protocol === "https:" ? "wss:" : "ws:";
      const socket = new WebSocket(protocol + "//" + location.host + "/ws/stream");
      socket.binaryType = "arraybuffer";

      await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error("实时连接超时")), 5000);
        socket.onopen = () => {
          clearTimeout(timer);
          resolve();
        };
        socket.onerror = () => {
          clearTimeout(timer);
          reject(new Error("无法建立实时识别连接"));
        };
      });

      state.audioContext = audioContext;
      state.socket = socket;
      state.sourceNode = audioContext.createMediaStreamSource(stream);
      state.resampler = createResampler(sourceRate, 16000);
      state.pcmPending = [];
      state.streamHadError = false;

      socket.onmessage = (event) => handleStreamMessage(event.data);
      socket.onerror = () => {
        state.streamHadError = true;
        showError("实时识别连接中断，已停止。可关闭「实时识别」后重新录音，或直接使用整段录音模式。");
      };
      socket.onclose = () => {
        if (state.phase === "recording") {
          stopStreaming(false);
        }
      };

      socket.send(JSON.stringify({ type: "start", language: els.language.value }));

      // AudioWorklet 优先，失败则退回 ScriptProcessorNode
      let captured = false;
      if (audioContext.audioWorklet) {
        try {
          await audioContext.audioWorklet.addModule("/static/pcm-worklet.js");
          const workletNode = new AudioWorkletNode(audioContext, "pcm-capture");
          workletNode.port.onmessage = (event) => pushPcm(event.data);
          state.workletNode = workletNode;
          captured = true;
        } catch (err) {
          console.warn("AudioWorklet 不可用，回退到 ScriptProcessorNode", err);
        }
      }

      if (!captured) {
        const processor = audioContext.createScriptProcessor(4096, 1, 1);
        processor.onaudioprocess = (event) => pushPcm(event.inputBuffer.getChannelData(0).slice(0));
        state.workletNode = processor;
      }

      // 静音 sink，保证音频图处于活动状态且不会产生回声
      const sink = audioContext.createGain();
      sink.gain.value = 0;
      state.sinkNode = sink;
      state.sourceNode.connect(state.workletNode);
      state.workletNode.connect(sink);
      sink.connect(audioContext.destination);

      els.realtimeCard.hidden = false;
      els.realtimeTranscript.textContent = "正在聆听…";
      setStatus("实时识别中…（最长 60 秒）", "recording");
      els.btnStop.disabled = false;
      els.btnStart.disabled = true;
      startTicker();
      return true;
    } catch (err) {
      await teardownStreaming({ keepStream: true });
      console.warn("实时识别启动失败", err);
      return false;
    }
  }

  function pushPcm(float32) {
    if (!state.socket || state.socket.readyState !== WebSocket.OPEN) return;
    const resampled = state.resampler ? state.resampler(float32) : null;
    if (resampled && resampled.length) {
      state.pcmPending.push(resampled);
      let total = 0;
      state.pcmPending.forEach((chunk) => (total += chunk.length));
      if (total >= WORKLET_CHUNK_SAMPLES) {
        // 注意：必须新建一个"恰好这么长"的数组再取 .buffer。
        // 若直接用 subarray(...).buffer，会把整个底层 ArrayBuffer（全部已缓冲音频）
        // 发出去，导致服务端收到重复音频、时长翻倍。
        const merged = new Float32Array(total);
        let offset = 0;
        state.pcmPending.forEach((chunk) => {
          merged.set(chunk, offset);
          offset += chunk.length;
        });
        state.pcmPending = [];
        state.socket.send(floatToPcm16(merged).buffer);
      }
    }
  }

  function handleStreamMessage(raw) {
    let message;
    try {
      message = JSON.parse(raw);
    } catch (_) {
      return;
    }

    if (message.type === "partial") {
      els.realtimeTranscript.textContent = message.text || "（继续说话…）";
      return;
    }

    if (message.type === "final") {
      const resolve = state.streamFinalResolve;
      state.streamFinalResolve = null;
      if (resolve) resolve(message);
      return;
    }

    if (message.type === "error") {
      showError(message.message || "实时识别出错。", "错误码：" + (message.code || "UNKNOWN"));
      state.streamHadError = true;
    }
  }

  async function stopStreaming(auto) {
    els.btnStop.disabled = true;
    setStatus(auto ? "已达 60 秒上限，正在结束…" : "正在获取最终结果…", "working");

    const socket = state.socket;
    if (socket && socket.readyState === WebSocket.OPEN) {
      const finalPromise = new Promise((resolve) => {
        state.streamFinalResolve = resolve;
        setTimeout(() => resolve(null), 8000); // 兜底超时
      });
      socket.send(JSON.stringify({ type: "stop" }));
      const result = await finalPromise;
      if (result) {
        setMeta({
          language: els.language.value,
          duration: result.duration_seconds,
          processing: result.processing_seconds,
        });
        setTranscript(result.text);
        setStatus(result.text ? "识别完成" : "识别完成（未检测到有效语音）", "done");
      } else if (!state.streamHadError) {
        showError("未能取得最终识别结果，请重试。");
      }
    }

    await teardownStreaming();
    stopTicker();
    state.stopping = false;
    els.realtimeCard.hidden = true;
    els.btnStart.disabled = false;
    els.btnReset.disabled = false;
  }

  async function teardownStreaming(options) {
    const keepStream = Boolean(options && options.keepStream);
    if (state.workletNode) {
      try {
        state.workletNode.port && (state.workletNode.port.onmessage = null);
        state.workletNode.disconnect();
      } catch (_) {}
      state.workletNode = null;
    }
    if (state.sourceNode) {
      try { state.sourceNode.disconnect(); } catch (_) {}
      state.sourceNode = null;
    }
    if (state.sinkNode) {
      try { state.sinkNode.disconnect(); } catch (_) {}
      state.sinkNode = null;
    }
    if (state.socket) {
      try { state.socket.close(); } catch (_) {}
      state.socket = null;
    }
    if (state.audioContext) {
      try { await state.audioContext.close(); } catch (_) {}
      state.audioContext = null;
    }
    if (!keepStream) stopMediaStream();
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
      showError("文件过大，服务端上限为 10 MB。", file.name + "（" + (file.size / 1024 / 1024).toFixed(2) + " MB）");
      state.selectedFile = null;
      els.btnUpload.disabled = true;
      return;
    }
    clearError();
    state.selectedFile = file;
    els.fileInfo.textContent = "已选择：" + file.name + "（" + (file.size / 1024).toFixed(1) + " KB）";
    els.btnUpload.disabled = false;
  }

  async function uploadSelected() {
    if (!state.selectedFile) return;
    clearError();
    els.btnUpload.disabled = true;
    const result = await transcribeFile(state.selectedFile, state.selectedFile.name);
    els.btnUpload.disabled = false;
    if (!result) setStatus("识别失败", "error");
  }

  // ----------------------------------------------------------- 重置
  async function resetAll() {
    if (state.phase === "recording") {
      stopRecording(false); // 录音中先正常停止，等本次识别完成后再重置
      return;
    }
    await teardownStreaming();
    cleanupRecording();
    clearError();
    stopTicker();
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

  window.addEventListener("beforeunload", () => {
    teardownStreaming();
  });

  // ----------------------------------------------------------- 初始化
  resetTimer();
  setMeta({ language: els.language.value, duration: null, processing: null });
  setStatus("就绪", "ready");
  els.languageHint.textContent = "当前选择：" + LANGUAGE_LABEL[els.language.value] + "。";
  loadEngineInfo();
})();
