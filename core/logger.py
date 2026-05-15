import logging
import os
import sys
from logging.handlers import RotatingFileHandler

def setup_logger(name: str, log_file: str = "lyra.log", level=logging.INFO):
    """Configura un logger estándar con salida a consola y archivo rotativo."""
    
    # Asegurar que el directorio de logs existe
    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    log_path = os.path.join(log_dir, log_file)
    
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Handler para consola
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    # Handler para archivo (rotativo de 5MB, mantiene 5 backups)
    file_handler = RotatingFileHandler(
        log_path, maxBytes=5*1024*1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Evitar duplicar handlers si se llama varias veces para el mismo nombre
    if not logger.handlers:
        logger.addHandler(console_handler)
        logger.addHandler(file_handler)
        # Evitar propagación al logger raíz si ya estamos manejando la salida aquí,
        # esto previene duplicados en algunos entornos.
        logger.propagate = False
    
    return logger

# Instancia por defecto para uso rápido
logger = setup_logger("lyra")
