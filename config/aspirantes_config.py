"""
config/aspirantes_config.py — Configuration settings for the Aspirantes WhatsApp module.

Nueva arquitectura autónoma: el módulo ya NO depende de un "Laravel Telecom Manager"
ni filtra por company_id. Se integra directamente con el backend autónomo SchoolSena,
que envía y recibe mensajes por su cuenta vía WhatsApp Cloud API (Meta).
"""

from pydantic_settings import BaseSettings
from pydantic import Field

class AspirantesSettings(BaseSettings):
    # Base URL del backend autónomo SchoolSena (procesa WhatsApp por sí mismo vía Meta).
    # Se mantiene el nombre INTELLITAXI_API_BASE como alias por compatibilidad de .env.
    SCHOOLSENA_API_BASE: str = Field(default="http://127.0.0.1:8000/api")
    INTELLITAXI_API_BASE: str = Field(default="http://127.0.0.1:8000/api")

    # Filtro opcional por company_id. 0 = deshabilitado (arquitectura autónoma, sin Telecom Manager).
    # Se conserva solo por retrocompatibilidad; por defecto ya no se exige.
    ASPIRANTES_COMPANY_ID: int = Field(default=0)

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @property
    def api_base(self) -> str:
        """URL base efectiva del backend SchoolSena."""
        return self.SCHOOLSENA_API_BASE or self.INTELLITAXI_API_BASE

aspirantes_settings = AspirantesSettings()
