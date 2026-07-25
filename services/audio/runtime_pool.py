"""Recursos compartidos del pipeline: sesiones ONNX y ejecutor de trabajo.

Dos problemas distintos de escala, resueltos aquí para que ninguna etapa tenga
que saber de concurrencia.

1. **Pesos compartidos, estado por llamada.** Crear una `InferenceSession` por
   llamada carga una copia completa de los pesos en cada una (el supresor son
   ~10 MB): con cuarenta llamadas simultáneas son ~400 MB de copias idénticas y
   cuarenta cargas de modelo. Una sesión de ONNX Runtime es **inmutable y segura
   para invocar `run()` desde varios hilos**, y en estos modelos el estado
   recurrente **no vive en la sesión**: entra y sale como tensor en cada llamada.
   Por eso la sesión se comparte y el estado es estrictamente por llamada. No hay
   estado mutable compartido, así que no hay contaminación posible entre
   llamadas: es la única lectura correcta de "modelos aislados" que además escala.

2. **El trabajo de audio fuera del bucle de eventos.** El pipeline consume
   milisegundos de CPU por cada bloque de 20 ms; ejecutarlo dentro del bucle de
   asyncio serializa todas las llamadas del proceso y basta con dos para
   saturarlo. Se ejecuta en un ejecutor de hilos dedicado: tanto ONNX Runtime
   como numpy/scipy **liberan el GIL** durante el cómputo, así que los hilos sí
   aprovechan varios núcleos de verdad. El orden por llamada se conserva porque
   cada llamada espera su bloque antes de leer el siguiente del transporte.

El ejecutor es un pool acotado y compartido por proceso — no un hilo por
llamada: cuarenta hilos compitiendo por ocho núcleos solo añaden cambios de
contexto. Su tamaño se configura (`AUDIO_WORKER_THREADS`, 0 = núcleos
disponibles).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

logger = logging.getLogger("lyra.audio.pool")

_SESSIONS: dict[tuple[str, int], object] = {}
_SESSIONS_LOCK = threading.Lock()

_EXECUTOR: Optional[ThreadPoolExecutor] = None
_EXECUTOR_LOCK = threading.Lock()


def get_session(model_path: str | Path, threads: int = 1) -> object:
    """Sesión ONNX Runtime compartida para `model_path` (los pesos se cargan una vez).

    `threads` fija los hilos internos de ORT. El valor correcto aquí es **1**: el
    paralelismo del sistema viene de atender muchas llamadas a la vez, no de
    repartir una inferencia de una trama de 20 ms entre núcleos. Subirlo provoca
    sobre-suscripción y empeora el rendimiento agregado.
    """
    import onnxruntime

    resolved = str(Path(model_path).resolve())
    key = (resolved, int(threads))
    session = _SESSIONS.get(key)
    if session is not None:
        return session

    with _SESSIONS_LOCK:
        session = _SESSIONS.get(key)
        if session is not None:
            return session
        if not Path(resolved).is_file():
            raise FileNotFoundError(f"modelo ONNX no encontrado: {resolved}")
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = int(threads)
        options.inter_op_num_threads = int(threads)
        options.graph_optimization_level = (
            onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
        )
        # Con muchas sesiones/llamadas la arena de memoria de CPU tiende a crecer
        # y no devolver: para tramas pequeñas y de tamaño fijo no aporta nada.
        options.enable_cpu_mem_arena = False
        created = onnxruntime.InferenceSession(
            resolved, sess_options=options, providers=["CPUExecutionProvider"]
        )
        _SESSIONS[key] = created
        logger.info(
            "[audio] sesión ONNX cargada (compartida entre llamadas): %s",
            Path(resolved).name,
        )
        return created


def session_count() -> int:
    """Sesiones cargadas en el proceso (una por modelo, no por llamada)."""
    return len(_SESSIONS)


def clear_sessions() -> None:
    """Libera las sesiones cacheadas (solo pruebas / recarga de modelos)."""
    with _SESSIONS_LOCK:
        _SESSIONS.clear()


def available_cpus() -> int:
    """Núcleos que este proceso puede usar de verdad.

    `os.cpu_count()` devuelve los del **anfitrión**: en un contenedor con dos
    vCPU sobre una máquina de 64 núcleos crearía 64 hilos y el proceso pasaría
    más tiempo cambiando de contexto que calculando. Se consulta, en orden, la
    afinidad del proceso y la cuota de cgroup v2, que es la única fuente que
    refleja un límite de CPU impuesto por el contenedor.
    """
    count = None
    if hasattr(os, "process_cpu_count"):  # Python 3.13+, respeta afinidad
        count = os.process_cpu_count()
    if not count and hasattr(os, "sched_getaffinity"):
        count = len(os.sched_getaffinity(0))
    if not count:
        count = os.cpu_count() or 1
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if quota != "max":
            count = min(count, max(1, int(int(quota) / int(period))))
    except (OSError, ValueError):
        pass
    return max(1, int(count))


def default_worker_threads() -> int:
    configured = 0
    try:
        from core.config import settings

        configured = int(settings.AUDIO_WORKER_THREADS)
    except Exception:  # pragma: no cover - configuración ausente en pruebas aisladas
        configured = 0
    if configured > 0:
        return configured
    # Trabajo limitado por CPU: más hilos que núcleos no añaden rendimiento.
    return available_cpus()


def max_concurrent_calls() -> int:
    """Llamadas simultáneas que este proceso puede procesar sin degradarse.

    Se deriva del coste medido por llamada (`AUDIO_CORES_PER_CALL`). Sirve para
    rechazar la llamada número N+1 en vez de degradar las N que ya están en
    curso, que es lo que ocurre cuando el ejecutor se satura: la cola crece, el
    lector del WebSocket se frena y la latencia deriva sin techo.
    """
    try:
        from core.config import settings

        configured = int(settings.AUDIO_MAX_CONCURRENT_CALLS)
        if configured > 0:
            return configured
        cost = float(settings.AUDIO_CORES_PER_CALL)
    except Exception:  # pragma: no cover
        cost = 0.6
    return max(1, int(default_worker_threads() / max(cost, 0.01)))


def get_audio_executor() -> ThreadPoolExecutor:
    """Ejecutor compartido donde corre el procesamiento de audio de todas las llamadas."""
    global _EXECUTOR
    if _EXECUTOR is not None:
        return _EXECUTOR
    with _EXECUTOR_LOCK:
        if _EXECUTOR is None:
            workers = default_worker_threads()
            _EXECUTOR = ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="lyra-audio"
            )
            logger.info("[audio] ejecutor de audio con %d hilos", workers)
    return _EXECUTOR


def prewarm(rate: int = 8000) -> None:
    """Carga modelos y mide el retardo del supresor ANTES de la primera llamada.

    Construir el pipeline la primera vez cuesta cerca de un segundo (dos cargas
    de sesión ONNX más la medición del retardo del modelo). Si eso ocurre al
    entrar la primera llamada, bloquea el bucle de eventos y con él a todas las
    demás. Se ejecuta al arrancar el proceso, en el propio ejecutor de audio.
    """
    from services.audio import CaptureEnhancer

    import numpy as np

    started = time.perf_counter()
    # Tocar `pipeline` es lo que fuerza la construcción: el constructor es
    # deliberadamente barato y difiere la carga al primer uso.
    enhancer = CaptureEnhancer(rate=rate)  # descartable: deja sesiones y cache listos
    if enhancer.pipeline is not None:
        # Además de cargar los modelos, se hace pasar audio: así quedan calientes
        # los caminos perezosos de scipy y numpy (diseño de filtros, planes de FFT)
        # y los hilos del ejecutor. Sin esto los primeros bloques de la primera
        # llamada del proceso tardan de más y se degradan sin necesidad.
        block = int(rate * 0.02)
        tone = (
            0.05
            * np.sin(2.0 * np.pi * 220.0 * np.arange(rate) / rate)
        ).astype(np.float32)
        for start in range(0, tone.size, block):
            chunk = tone[start : start + block]
            enhancer.process(
                (chunk * 32767.0).astype("<i2").tobytes(),
                timestamp=(start + chunk.size) / rate,
            )
    logger.info(
        "[audio] pipeline precalentado en %.0f ms (sesiones=%d, hilos=%d, "
        "llamadas simultáneas máximas=%d)",
        (time.perf_counter() - started) * 1000.0,
        session_count(),
        default_worker_threads(),
        max_concurrent_calls(),
    )


async def prewarm_async(rate: int = 8000) -> None:
    """Precalienta sin bloquear el bucle de eventos (para el arranque de la app)."""
    import asyncio

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(get_audio_executor(), prewarm, rate)


def shutdown_audio_executor(wait: bool = False) -> None:
    """Cierra el ejecutor (apagado del proceso o pruebas)."""
    global _EXECUTOR
    with _EXECUTOR_LOCK:
        if _EXECUTOR is not None:
            _EXECUTOR.shutdown(wait=wait)
            _EXECUTOR = None
