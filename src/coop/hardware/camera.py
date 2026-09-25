"""Webcam access.  ``get_frame()`` returns JPEG bytes or raises ``CameraError``."""

from __future__ import annotations

import os
import logging

logger = logging.getLogger(__name__)


class CameraError(RuntimeError):
    pass


class OpenCvCamera:
    def __init__(self, device_index: int = 0, cv2_module=None):
        if cv2_module is None:
            try:
                import cv2 as cv2_module  # noqa: N813
            except ImportError:
                raise CameraError("OpenCV (cv2) is required for the camera: pip install opencv-python") from None
        self._cv2 = cv2_module
        self._capture = cv2_module.VideoCapture(device_index)
        if not self._capture.isOpened():
            raise CameraError(f"Unable to open video device {device_index}")

    def get_frame(self) -> bytes:
        ok, frame = self._capture.read()
        if not ok:
            raise CameraError("Failed to capture frame from camera")
        ok, buffer = self._cv2.imencode(".jpg", frame)
        if not ok:
            raise CameraError("Failed to encode frame as JPEG")
        return buffer.tobytes()

    def close(self) -> None:
        try:
            self._capture.release()
        except Exception:
            pass


# Smallest valid JPEG (1x1 black pixel) for the mock camera.
_BLACK_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c"
    "140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27"
    "393d38323c2e333432ffc0000b080001000101011100ffc4001f0000010501010101010100000000"
    "000000000102030405060708090a0bffc400b5100002010303020403050504040000017d01020300"
    "041105122131410613516107227114328191a1082342b1c11552d1f02433627282090a161718191a"
    "25262728292a3435363738393a434445464748494a535455565758595a636465666768696a737475"
    "767778797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9ba"
    "c2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda"
    "0008010100003f00fbd3ffd9"
)


_MOCK_FRAME_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mock_frame.jpg")


def _mock_frame() -> bytes:
    """A static test picture of a coop (falls back to a black pixel)."""
    try:
        with open(_MOCK_FRAME_PATH, "rb") as f:
            return f.read()
    except OSError:
        return _BLACK_JPEG


class MockCamera:
    def __init__(self, device_index: int = 0):
        self.device_index = device_index
        self._frame = _mock_frame()

    def get_frame(self) -> bytes:
        return self._frame

    def close(self) -> None:
        pass
