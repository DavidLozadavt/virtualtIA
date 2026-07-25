"""Etapas del pipeline de mejora de audio (una responsabilidad por módulo)."""

from services.audio.stages.denoise import (
    DenoiseStage,
    OnnxStreamEnhancer,
    SpectralGateEnhancer,
)
from services.audio.stages.dereverb import DereverbStage
from services.audio.stages.echo import EchoControlStage
from services.audio.stages.normalize import NormalizeStage
from services.audio.stages.preprocess import PreprocessStage
from services.audio.stages.speaker_focus import SpeakerFocusStage
from services.audio.stages.vad import (
    EnergyDetector,
    SileroOnnxDetector,
    VoiceGateStage,
)

__all__ = [
    "DenoiseStage",
    "DereverbStage",
    "DpdfnetEnhancer",
    "EchoControlStage",
    "EnergyDetector",
    "NormalizeStage",
    "PreprocessStage",
    "SileroOnnxDetector",
    "SpeakerFocusStage",
    "SpectralGateEnhancer",
    "VoiceGateStage",
]
