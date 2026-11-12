"""Configuración del bot. Nadie lee os.environ fuera de este módulo."""

from functools import lru_cache
import os

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict

    class Settings(BaseSettings):
        model_config = SettingsConfigDict(env_file=None, extra='ignore')

        TELEGRAM_BOT_TOKEN: str = ''
        TELEGRAM_BOT_SECRETO_INTERNO: str = ''
        BACKEND_URL: str = 'http://backend:8000'
        #: Minutos que vive un cambio esperando el botón Confirmar.
        BOT_MINUTOS_PENDIENTE: int = 30
        #: Para «hoy» y «mañana» en las fechas de los formularios.
        ZONA_HORARIA: str = 'America/Mexico_City'

        # --- Agente (texto libre). Sin OPENAI_API_KEY solo funcionan el menú y los comandos.
        OPENAI_API_KEY: str = ''
        OPENAI_MODEL: str = 'gpt-4o-mini'
        OPENAI_BASE_URL: str = 'https://api.openai.com/v1'
        #: Tope de tokens (entrada + salida) por día para todo el bot y por persona.
        BOT_TOKENS_DIA: int = 300_000
        BOT_TOKENS_PERSONA_DIA: int = 60_000
        #: CURP, RFC, NSS, teléfonos, correo y domicilio se enmascaran antes de llegar al modelo.
        BOT_ENMASCARAR_DATOS: bool = True
        #: Resumen diario de avisos (contratos, reingresos, candidatos). No usa IA.
        BOT_AVISOS: bool = True
        #: Notas de voz (requieren OPENAI_API_KEY). 0 las apaga.
        BOT_VOZ_MAX_SEGUNDOS: int = 120
        OPENAI_MODELO_VOZ: str = 'gpt-4o-mini-transcribe'

        @property
        def voz_activa(self) -> bool:
            return self.agente_activo and self.BOT_VOZ_MAX_SEGUNDOS > 0

        @property
        def agente_activo(self) -> bool:
            return bool(self.OPENAI_API_KEY.strip())

        def problemas(self) -> list[str]:
            faltan = []
            if not self.TELEGRAM_BOT_TOKEN.strip():
                faltan.append('TELEGRAM_BOT_TOKEN está vacío.')
            if not self.TELEGRAM_BOT_SECRETO_INTERNO.strip():
                faltan.append('TELEGRAM_BOT_SECRETO_INTERNO está vacío (debe ser el mismo que en el backend).')
            if self.agente_activo and (self.BOT_TOKENS_DIA <= 0 or self.BOT_TOKENS_PERSONA_DIA <= 0):
                faltan.append('Con OPENAI_API_KEY, BOT_TOKENS_DIA y BOT_TOKENS_PERSONA_DIA deben ser mayores que 0.')
            if self.BOT_MINUTOS_PENDIENTE < 1:
                faltan.append('BOT_MINUTOS_PENDIENTE debe ser al menos 1.')
            return faltan

except ImportError:
    class Settings:  # type: ignore[no-redef]
        def __init__(self, **kwargs):
            self.TELEGRAM_BOT_TOKEN = kwargs.get('TELEGRAM_BOT_TOKEN', os.getenv('TELEGRAM_BOT_TOKEN', ''))
            self.TELEGRAM_BOT_SECRETO_INTERNO = kwargs.get('TELEGRAM_BOT_SECRETO_INTERNO', os.getenv('TELEGRAM_BOT_SECRETO_INTERNO', ''))
            self.BACKEND_URL = kwargs.get('BACKEND_URL', os.getenv('BACKEND_URL', 'http://backend:8000'))
            self.BOT_MINUTOS_PENDIENTE = int(kwargs.get('BOT_MINUTOS_PENDIENTE', os.getenv('BOT_MINUTOS_PENDIENTE', '30')))
            self.ZONA_HORARIA = kwargs.get('ZONA_HORARIA', os.getenv('ZONA_HORARIA', 'America/Mexico_City'))
            self.OPENAI_API_KEY = kwargs.get('OPENAI_API_KEY', os.getenv('OPENAI_API_KEY', ''))
            self.OPENAI_MODEL = kwargs.get('OPENAI_MODEL', os.getenv('OPENAI_MODEL', 'gpt-4o-mini'))
            self.OPENAI_BASE_URL = kwargs.get('OPENAI_BASE_URL', os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1'))
            self.BOT_TOKENS_DIA = int(kwargs.get('BOT_TOKENS_DIA', os.getenv('BOT_TOKENS_DIA', '300000')))
            self.BOT_TOKENS_PERSONA_DIA = int(kwargs.get('BOT_TOKENS_PERSONA_DIA', os.getenv('BOT_TOKENS_PERSONA_DIA', '60000')))
            self.BOT_ENMASCARAR_DATOS = bool(kwargs.get('BOT_ENMASCARAR_DATOS', os.getenv('BOT_ENMASCARAR_DATOS', 'True') in ('1', 'True', 'true')))
            self.BOT_AVISOS = bool(kwargs.get('BOT_AVISOS', os.getenv('BOT_AVISOS', 'True') in ('1', 'True', 'true')))
            self.BOT_VOZ_MAX_SEGUNDOS = int(kwargs.get('BOT_VOZ_MAX_SEGUNDOS', os.getenv('BOT_VOZ_MAX_SEGUNDOS', '120')))
            self.OPENAI_MODELO_VOZ = kwargs.get('OPENAI_MODELO_VOZ', os.getenv('OPENAI_MODELO_VOZ', 'gpt-4o-mini-transcribe'))

        @property
        def voz_activa(self) -> bool:
            return self.agente_activo and self.BOT_VOZ_MAX_SEGUNDOS > 0

        @property
        def agente_activo(self) -> bool:
            return bool(self.OPENAI_API_KEY.strip())

        def problemas(self) -> list[str]:
            faltan = []
            if not self.TELEGRAM_BOT_TOKEN.strip():
                faltan.append('TELEGRAM_BOT_TOKEN está vacío.')
            if not self.TELEGRAM_BOT_SECRETO_INTERNO.strip():
                faltan.append('TELEGRAM_BOT_SECRETO_INTERNO está vacío (debe ser el mismo que en el backend).')
            if self.agente_activo and (self.BOT_TOKENS_DIA <= 0 or self.BOT_TOKENS_PERSONA_DIA <= 0):
                faltan.append('Con OPENAI_API_KEY, BOT_TOKENS_DIA y BOT_TOKENS_PERSONA_DIA deben ser mayores que 0.')
            if self.BOT_MINUTOS_PENDIENTE < 1:
                faltan.append('BOT_MINUTOS_PENDIENTE debe ser al menos 1.')
            return faltan


@lru_cache
def obtener_settings() -> Settings:
    return Settings()
