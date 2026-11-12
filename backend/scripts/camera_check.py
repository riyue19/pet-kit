"""Camera self-test: opens the camera for a few seconds and prints whether a face is detected.
Nothing is saved. Sit in front of the camera, then:  uv run python scripts/camera_check.py [device_index]
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from perception.camera import MODEL_FILE, Camera  # noqa: E402


def main() -> int:
    index = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    model = Path(__file__).resolve().parents[1] / "models" / "camera" / MODEL_FILE
    if not model.exists():
        print("缺少人脸检测模型，请先运行 uv run python scripts/fetch_models.py")
        return 1
    try:
        cam = Camera(model, index)
    except RuntimeError as e:
        print(e)
        return 1
    hits = 0
    try:
        for _ in range(10):  # let auto-exposure settle
            cam.read()
            time.sleep(0.1)
        for i in range(6):
            frame = cam.read()
            if frame is None:
                print(f"{i + 1}/6 读不到画面")
            else:
                face = cam.has_face(frame)
                hits += face
                print(f"{i + 1}/6 画面 {frame.shape[1]}x{frame.shape[0]}，亮度 {frame.mean():.0f}，{'检测到人脸' if face else '没有人脸'}")
            time.sleep(1)
    finally:
        cam.close()
    print("通过" if hits else "没检测到人脸：确认人在画面里、光线足够、没有遮挡镜头，或换设备号试试")
    return 0 if hits else 2


if __name__ == "__main__":
    raise SystemExit(main())
