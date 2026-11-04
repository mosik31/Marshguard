"""Blockage classifier.

If MARSHGUARD_YOLO_WEIGHTS points to trained YOLOv8 weights (detection or classification)
and `ultralytics` is installed, real photos are classified. Class names in the model must be
plastic, hyacinth, silt, rubble. Without weights, real photos go to manual review on the
dashboard instead of receiving an invented label.
"""
from __future__ import annotations

import io
import logging
import os

log = logging.getLogger("marshguard.classifier")
CLASSES = {"plastic", "hyacinth", "silt", "rubble"}
THRESHOLD = 0.70

# Fixed outputs for the demo photos offered in the simulator page
SAMPLES = {
    "plastic":  ("plastic", 0.91),
    "hyacinth": ("hyacinth", 0.88),
    "silt":     ("silt", 0.83),
    "rubble":   ("rubble", 0.86),
    "blurry":   ("silt", 0.52),
}

_model = None


def _load():
    global _model
    if _model is not None:
        return _model
    path = os.getenv("MARSHGUARD_YOLO_WEIGHTS")
    if not path:
        return None
    try:
        from ultralytics import YOLO  # type: ignore
        _model = YOLO(path)
        log.info("Loaded YOLO weights from %s", path)
    except Exception as exc:  # pragma: no cover
        log.warning("Could not load YOLO weights: %s", exc)
        _model = False
    return _model


def model_available() -> bool:
    return bool(_load())


def classify_image(image_bytes: bytes) -> tuple[str, float] | None:
    """Return (class, confidence), or None when no model is configured."""
    model = _load()
    if not model:
        return None
    from PIL import Image  # ultralytics depends on pillow
    result = model.predict(Image.open(io.BytesIO(image_bytes)), verbose=False)[0]
    names = result.names
    if getattr(result, "probs", None) is not None:
        idx = int(result.probs.top1)
        cls, conf = names[idx], float(result.probs.top1conf)
    elif result.boxes is not None and len(result.boxes):
        i = int(result.boxes.conf.argmax())
        cls, conf = names[int(result.boxes.cls[i])], float(result.boxes.conf[i])
    else:
        return ("none", 0.0)
    cls = str(cls).lower()
    return (cls, conf) if cls in CLASSES else ("none", conf)


def classify_sample(sample: str) -> tuple[str, float]:
    return SAMPLES.get(sample, ("silt", 0.5))
