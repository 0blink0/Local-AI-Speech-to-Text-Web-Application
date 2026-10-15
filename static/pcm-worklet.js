/**
 * AudioWorklet 处理器：把麦克风音频的原生 Float32 帧转发给主线程。
 *
 * 主线程负责降采样到 16 kHz、编码为 int16 PCM 并通过 WebSocket 发送。
 * 把重采样放在主线程是为了让这个 worklet 保持极简、不易出错。
 */
class PcmCaptureProcessor extends AudioWorkletProcessor {
  process(inputs, outputs, parameters) {
    const input = inputs[0];
    if (input && input.length > 0) {
      const channel = input[0];
      if (channel && channel.length) {
        // 拷贝一份再发，避免底层 buffer 被复用
        this.port.postMessage(channel.slice(0));
      }
    }
    return true;
  }
}

registerProcessor("pcm-capture", PcmCaptureProcessor);
