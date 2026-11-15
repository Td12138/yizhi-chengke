import { useEffect, useRef, useState } from "react";

// 拍照后最长边上限，避免把 4K 图直接塞给后端
const CAPTURE_MAX_EDGE = 1920;
const JPEG_QUALITY = 0.92;

type Props = {
  onCapture: (file: File) => void;
  onClose: () => void;
};

export default function CameraCapture({ onCapture, onClose }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceId] = useState("");
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");

  function stopStream() {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  }

  async function start(id?: string) {
    stopStream();
    setReady(false);
    setError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: id
          ? { deviceId: { exact: id } }
          : {
              facingMode: { ideal: "environment" },
              width: { ideal: 1920 },
              height: { ideal: 1080 },
            },
        audio: false,
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play().catch(() => undefined);
      }
      setReady(true);

      const list = (await navigator.mediaDevices.enumerateDevices()).filter(
        (device) => device.kind === "videoinput"
      );
      setDevices(list);
      if (!id && list[0]?.deviceId) setDeviceId(list[0].deviceId);
    } catch (err) {
      const name = err instanceof Error ? err.name : "";
      if (name === "NotAllowedError") {
        setError("摄像头权限被拒绝：请在浏览器地址栏允许摄像头，然后重试。");
      } else if (name === "NotFoundError") {
        setError("没有检测到可用摄像头，请改用「选择图片」。");
      } else if (name === "NotReadableError") {
        setError("摄像头被其他程序占用（会议软件 / 相机），关闭后重试。");
      } else {
        setError(`无法打开摄像头：${err instanceof Error ? err.message : String(err)}`);
      }
    }
  }

  useEffect(() => {
    void start();
    return () => stopStream();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function shoot() {
    const video = videoRef.current;
    if (!video || !video.videoWidth) return;

    const scale = Math.min(1, CAPTURE_MAX_EDGE / Math.max(video.videoWidth, video.videoHeight));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(video.videoWidth * scale);
    canvas.height = Math.round(video.videoHeight * scale);

    const context = canvas.getContext("2d");
    if (!context) return;
    context.drawImage(video, 0, 0, canvas.width, canvas.height);

    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", JPEG_QUALITY)
    );
    if (!blob) {
      setError("拍照失败：画布导出为空，请重试。");
      return;
    }

    stopStream();
    onCapture(new File([blob], `camera-${Date.now()}.jpg`, { type: "image/jpeg" }));
  }

  function close() {
    stopStream();
    onClose();
  }

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") close();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="camera-backdrop" role="dialog" aria-modal="true" aria-label="拍照识别">
      <div className="camera-modal">
        <header className="camera-head">
          <h3>拍照识别</h3>
          <button type="button" className="btn btn-sm" onClick={close}>
            关闭
          </button>
        </header>

        {error ? (
          <p className="camera-error">{error}</p>
        ) : (
          <video
            ref={videoRef}
            className="camera-video"
            playsInline
            muted
            autoPlay
          />
        )}

        <div className="camera-actions">
          {devices.length > 1 && (
            <select
              className="text-input camera-device"
              value={deviceId}
              onChange={(event) => {
                setDeviceId(event.target.value);
                void start(event.target.value);
              }}
            >
              {devices.map((device, index) => (
                <option key={device.deviceId || index} value={device.deviceId}>
                  {device.label || `摄像头 ${index + 1}`}
                </option>
              ))}
            </select>
          )}
          <button
            type="button"
            className="btn btn-primary"
            disabled={!ready || Boolean(error)}
            onClick={() => void shoot()}
          >
            拍下这一页
          </button>
        </div>

        <p className="camera-hint">
          纸铺平、光线均匀、整页入框。拍完会自动开始识别。
        </p>
      </div>
    </div>
  );
}
