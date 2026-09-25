"""Tests for :class:`camera.Camera` (OpenCV webcam wrapper) with a fake ``cv2``."""

import importlib
import sys
import types
from unittest import mock

import pytest


@pytest.fixture
def fake_cv2(monkeypatch):
    cv2 = types.ModuleType("cv2")
    cv2.VideoCapture = mock.Mock()
    cv2.imencode = mock.Mock(return_value=(True, mock.Mock(tobytes=lambda: b"jpeg")))
    monkeypatch.setitem(sys.modules, "cv2", cv2)
    import camera
    return cv2, importlib.reload(camera)


def test_opens_device(fake_cv2):
    cv2, camera = fake_cv2
    cam = camera.Camera(device_index=1)
    cv2.VideoCapture.assert_called_once_with(1)
    assert cam.is_init is True


def test_unopenable_device_raises_runtime_error(fake_cv2):
    cv2, camera = fake_cv2
    cv2.VideoCapture.return_value.isOpened.return_value = False
    with pytest.raises(RuntimeError, match="Unable to open video device 0"):
        camera.Camera()


def test_missing_opencv_raises_runtime_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "cv2", None)  # makes `import cv2` fail
    import camera
    camera = importlib.reload(camera)
    with pytest.raises(RuntimeError, match="OpenCV"):
        camera.Camera()


def test_failed_read_raises_runtime_error(fake_cv2):
    cv2, camera = fake_cv2
    cv2.VideoCapture.return_value.read.return_value = (False, None)
    cam = camera.Camera()
    with pytest.raises(RuntimeError, match="Failed to capture frame"):
        cam.get_frame()


def test_get_frame_returns_jpeg_bytes(fake_cv2):
    """Regression: cv2 used to be imported only inside __init__ (NameError here)."""
    cv2, camera = fake_cv2
    cv2.VideoCapture.return_value.read.return_value = (True, "frame")
    cam = camera.Camera()
    assert cam.get_frame() == b"jpeg"
