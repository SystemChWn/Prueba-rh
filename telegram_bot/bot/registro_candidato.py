"""Máquina de estados estricta para el flujo de «Generar Registro de Candidato».

Aísla el proceso de captura y validación documental por user_id.
Incluye:
- Preprocesamiento EXIF y extracción estructurada por visión OCR.
- Confirmación interactiva paso a paso de datos críticos (Nombre, CURP, RFC, NSS)
  con opción de corregir por texto o volver a tomar la foto.
- Fallback automático: solicitud de Documento CURP si no se detectó en Acta ni INE.
- Pregunta de nacionalidad con opción directa «🇲🇽 Mexicana» o especificar otra.
- Captura de fotografía del candidato (rostro) para expediente digital antes de la encuesta.
- Encuesta de reclutamiento: selección de reclutador ÚNICAMENTE para Facebook o Reclu CW.
- Mapeo exacto de sexo (en INE: M = Mujer/Femenino, H = Hombre/Masculino).
- Validación de campos en blanco y confirmación final completa.
"""

from __future__ import annotations

import base64
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from io import BytesIO
import logging
import re
from typing import Any
import unicodedata

try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
    from telegram.ext import ContextTypes
except ImportError:
    InlineKeyboardButton = InlineKeyboardMarkup = Update = ContextTypes = object  # type: ignore

try:
    from bot.backend import ClienteBackend, ErrorBackend
except ImportError:
    ClienteBackend = ErrorBackend = object  # type: ignore
from bot.borrador import Archivo
try:
    from bot.interfaz import boton, responder
except ImportError:
    def boton(texto: str, callback_data: str):
        return (texto, callback_data)

    async def responder(update, texto, reply_markup=None):
        pass

logger = logging.getLogger(__name__)

FUENTES_REGISTRO = ['Facebook', 'Posteo', 'Volante', 'Recomendación', 'Reclu CW', 'Reclu puro']
PARENTESCOS_COMUNES = ['Madre', 'Padre', 'Cónyuge / Pareja', 'Hijo / Hija', 'Hermano / Hermana', 'Familiar', 'Amigo / Conocido']
ESTADOS_CIVILES = ['SOLTERO(A)', 'CASADO(A)', 'DIVORCIADO(A)', 'VIUDO(A)', 'UNIÓN LIBRE']


class EstadoRegistro(str, Enum):
    ESPERANDO_ACTA = 'esperando_acta'
    CONFIRMANDO_DATO_PASO = 'confirmando_dato_paso'
    ESPERANDO_CORRECCION_PASO = 'esperando_correccion_paso'
    ESPERANDO_DOMICILIO = 'esperando_domicilio'
    ESPERANDO_PARENTESCO_DOMICILIO = 'esperando_parentesco_domicilio'
    ESPERANDO_INE = 'esperando_ine'
    ESPERANDO_DOC_CURP = 'esperando_doc_curp'
    ESPERANDO_CSF = 'esperando_csf'
    ESPERANDO_NSS = 'esperando_nss'
    ESPERANDO_EMERGENCIA_NOMBRE = 'esperando_emergencia_nombre'
    ESPERANDO_EMERGENCIA_PARENTESCO = 'esperando_emergencia_parentesco'
    ESPERANDO_EMERGENCIA_TELEFONO = 'esperando_emergencia_telefono'
    ESPERANDO_TEL_CANDIDATO = 'esperando_tel_candidato'
    ESPERANDO_CORREO_CANDIDATO = 'esperando_correo_candidato'
    ESPERANDO_EDO_CIVIL = 'esperando_edo_civil'
    ESPERANDO_NACIONALIDAD = 'esperando_nacionalidad'
    ESPERANDO_OTRA_NACIONALIDAD = 'esperando_otra_nacionalidad'
    ESPERANDO_FOTO_CANDIDATO = 'esperando_foto_candidato'
    CONFIRMANDO_FOTO_CANDIDATO = 'confirmando_foto_candidato'
    ESPERANDO_ENCUESTA_FUENTE = 'esperando_encuesta_fuente'
    ESPERANDO_ENCUESTA_RECLUTADOR = 'esperando_encuesta_reclutador'
    ESPERANDO_ENCUESTA_RECOMENDO = 'esperando_encuesta_recomendo'
    ESPERANDO_PROCEDENCIA = 'esperando_procedencia'
    CONFIRMAR_DATOS = 'confirmar_datos'
    EDITANDO_CAMPO = 'editando_campo'
    SOLICITANDO_FALTANTE = 'solicitando_faltante'
    ESPERANDO_NUMERO_TEMPORAL = 'esperando_numero_temporal'


@dataclass
class SesionRegistro:
    user_id: int
    estado: EstadoRegistro = EstadoRegistro.ESPERANDO_ACTA
    datos: dict[str, Any] = field(default_factory=dict)
    reclutadores: list[str] = field(default_factory=list)
    campo_editando: str | None = None
    faltantes_pendientes: list[str] = field(default_factory=list)
    # Control de confirmación paso a paso de datos críticos
    cola_confirmacion: list[str] = field(default_factory=list)
    campo_confirmando: str | None = None
    campos_confirmados: set[str] = field(default_factory=set)
    siguiente_estado_post_conf: EstadoRegistro = EstadoRegistro.ESPERANDO_DOMICILIO
    ultimo_doc_para_reintentar: str = 'Acta de Nacimiento'


#: Almacenamiento centralizado de sesiones activas de registro por telegram_id
registro_activo: dict[int, SesionRegistro] = {}


def tiene_registro_activo(user_id: int) -> bool:
    return user_id in registro_activo


def obtener_sesion(user_id: int) -> SesionRegistro | None:
    return registro_activo.get(user_id)


def cancelar_registro(user_id: int) -> bool:
    if user_id in registro_activo:
        del registro_activo[user_id]
        return True
    return False


def normalizar_texto(texto: Any) -> str:
    if not texto:
        return ''
    s = str(texto).strip().lower()
    s = ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')
    return ' '.join(s.split())


def cadenas_coinciden(a: Any, b: Any) -> bool:
    na = normalizar_texto(a).replace(' ', '')
    nb = normalizar_texto(b).replace(' ', '')
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def normalizar_sexo(valor: Any, es_ine: bool = False) -> str:
    """Mapea el sexo. En INE: 'M' es Mujer (Femenino) y 'H' es Hombre (Masculino)."""
    if not valor:
        return ''
    v = str(valor).strip().upper()
    if es_ine:
        if v in ('M', 'MUJER', 'FEMENINO', 'F'):
            return 'FEMENINO'
        if v in ('H', 'HOMBRE', 'MASCULINO'):
            return 'MASCULINO'
    else:
        if v in ('MASCULINO', 'HOMBRE', 'H'):
            return 'MASCULINO'
        if v in ('FEMENINO', 'MUJER', 'F'):
            return 'FEMENINO'
        if v == 'M':
            return 'FEMENINO'
    if 'MASC' in v or 'HOMB' in v:
        return 'MASCULINO'
    if 'FEM' in v or 'MUJ' in v:
        return 'FEMENINO'
    return v


def ordenar_nombre_natural(nombre_completo: str, nombre_pila: str = '', apellido_pat: str = '', apellido_mat: str = '') -> str:
    """
    Asegura que el nombre completo inicie por el nombre de pila:
    [Nombre(s)] [Primer Apellido] [Segundo Apellido].
    """
    if nombre_pila and apellido_pat:
        partes = [nombre_pila.strip(), apellido_pat.strip()]
        if apellido_mat and apellido_mat.strip():
            partes.append(apellido_mat.strip())
        return ' '.join(partes)

    if not nombre_completo:
        return ''

    palabras = nombre_completo.strip().split()
    if len(palabras) < 3:
        return nombre_completo.strip()

    if apellido_pat and normalizar_texto(palabras[0]) == normalizar_texto(apellido_pat):
        if apellido_mat and len(palabras) >= 3 and normalizar_texto(palabras[1]) == normalizar_texto(apellido_mat):
            apellidos = palabras[:2]
            nombres = palabras[2:]
            return ' '.join(nombres + apellidos)

    return nombre_completo.strip()


ENTIDADES_CURP = {
    'AS', 'BC', 'BS', 'CC', 'CL', 'CM', 'CS', 'CH', 'DF', 'DG',
    'GT', 'GR', 'HG', 'JC', 'MC', 'MN', 'MS', 'NT', 'NL', 'OC',
    'PL', 'QT', 'QR', 'SP', 'SL', 'SR', 'TC', 'TS', 'TL', 'VZ',
    'YN', 'ZS', 'NE'
}


def validar_curp_formato(curp: str) -> tuple[bool, str]:
    c = str(curp or '').strip().upper()
    if len(c) != 18:
        return False, f"La CURP debe tener exactamente 18 caracteres alfanuméricos (actualmente tiene {len(c)})."
    if not re.match(r'^[A-Z]{4}', c):
        return False, "Los primeros 4 caracteres deben ser letras (iniciales de apellidos y nombre)."
    if not re.match(r'^\d{6}', c[4:10]):
        return False, "Las posiciones 5 a 10 deben ser 6 dígitos numéricos de fecha (AAMMDD)."
    if c[10] not in ('H', 'M', 'X'):
        return False, "La posición 11 debe indicar sexo: 'H' (Hombre) o 'M' (Mujer)."
    ent = c[11:13]
    if ent not in ENTIDADES_CURP and not re.match(r'^[A-Z]{2}$', ent):
        return False, f"Las posiciones 12 y 13 ('{ent}') no corresponden a una entidad federativa mexicana válida."
    if not re.match(r'^[B-DF-HJ-NP-TV-Z]{3}', c[13:16]) and not re.match(r'^[A-Z]{3}', c[13:16]):
        return False, "Las posiciones 14 a 16 deben ser letras consonantes internas."
    if not re.match(r'^[0-9A-Z]{2}$', c[16:18]):
        return False, "Las posiciones 17 y 18 deben ser alfanuméricas (homoclave y dígito verificador)."
    return True, "CURP válida"


REGEX_RFC = re.compile(r'^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}$', re.IGNORECASE)


def validar_rfc_formato(rfc: str) -> tuple[bool, str]:
    r = str(rfc or '').strip().upper()
    if len(r) not in (12, 13):
        return False, f"El RFC debe tener 12 caracteres (persona moral) o 13 caracteres (persona física) (actualmente tiene {len(r)})."
    if not REGEX_RFC.match(r):
        return False, "El formato del RFC no es válido. Debe tener 3 o 4 letras iniciales, 6 dígitos de fecha (AAMMDD) y 3 caracteres de homoclave."
    return True, "RFC válido"


def procesar_foto_candidato(contenido_bytes: bytes) -> bytes:
    """Preserva la fotografía con las dimensiones y proporción naturales de la cámara de Telegram, orientando EXIF y optimizando para el expediente."""
    try:
        from PIL import Image, ImageOps
        img = Image.open(BytesIO(contenido_bytes))
        img = ImageOps.exif_transpose(img)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        max_dim = 1280
        if max(img.size) > max_dim:
            img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
        out = BytesIO()
        img.save(out, format='JPEG', quality=92, optimize=True)
        return out.getvalue()
    except Exception as exc:
        logger.warning('No se pudo procesar foto de candidato: %s', exc)
        return contenido_bytes


def recortar_foto_cuadrada_1a1(contenido_bytes: bytes, factor_y: float = 0.20) -> bytes:
    """Compatibilidad: procesa la foto optimizándola con proporción natural."""
    return procesar_foto_candidato(contenido_bytes)


def resolver_por_mayoria(valores: list[str]) -> tuple[str, bool, list[str]]:
    """
    Dada una lista de valores extraídos de diferentes documentos:
    Devuelve (ganador, hay_discrepancia, variantes_distintas).
    """
    validos = [str(v).strip() for v in valores if v and str(v).strip() and str(v).strip().lower() not in ('null', 'none', '')]
    if not validos:
        return '', False, []

    grupos: dict[str, list[str]] = {}
    for v in validos:
        norm = normalizar_texto(v)
        grupos.setdefault(norm, []).append(v)

    if len(grupos) == 1:
        primer_grupo = next(iter(grupos.values()))
        mejor = max(primer_grupo, key=lambda s: (len(s), any(c.isupper() for c in s)))
        return mejor, False, [mejor]

    conteo = {norm: len(items) for norm, items in grupos.items()}
    ordenados = sorted(conteo.items(), key=lambda x: x[1], reverse=True)

    top_norm, top_freq = ordenados[0]
    segundo_freq = ordenados[1][1] if len(ordenados) > 1 else 0

    variantes = [grupos[norm][0] for norm, _ in ordenados]

    if top_freq > segundo_freq:
        ganador = max(grupos[top_norm], key=len)
        return ganador, True, variantes

    return grupos[top_norm][0], True, variantes


def calcular_edad(fecha_iso: str | None) -> str:
    if not fecha_iso:
        return ''
    try:
        fn = datetime.strptime(str(fecha_iso).strip(), '%Y-%m-%d').date()
        hoy = date.today()
        edad = hoy.year - fn.year - ((hoy.month, hoy.day) < (fn.month, fn.day))
        if 14 <= edad <= 110:
            return str(edad)
    except Exception:
        pass
    return ''


def normalizar_numero_temporal(valor: str) -> str:
    v = str(valor or '').strip().upper()
    if v.startswith('TEMP'):
        if '-' in v:
            parte = v.split('-', 1)[1].strip()
            return f'TEMP - {parte}'
        num = v.replace('TEMP', '').strip()
        return f'TEMP - {num}' if num else 'TEMP - 001'
    return f'TEMP - {v}' if v else 'TEMP - 001'


def _teclado_paso_doc(tipo: str = 'doc') -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [boton('⏩ Omitir documento', f'reg:omitir_{tipo}'), boton('❌ Cancelar', 'reg:cancelar')]
    ])


def _corregir_orientacion(contenido: bytes, mime: str) -> bytes:
    if mime not in ('image/jpeg', 'image/png', 'image/webp'):
        return contenido
    try:
        from PIL import Image, ImageOps, ImageEnhance, ImageStat, ImageFilter
        img = Image.open(BytesIO(contenido))
        img = ImageOps.exif_transpose(img)
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        
        stat = ImageStat.Stat(img.convert('L'))
        brillo_medio = stat.mean[0] if stat.mean else 128
        if brillo_medio < 95:
            img = ImageOps.autocontrast(img, cutoff=2)
            img = ImageEnhance.Brightness(img).enhance(1.35)
            img = ImageEnhance.Contrast(img).enhance(1.15)
        elif brillo_medio > 215:
            img = ImageEnhance.Contrast(img).enhance(1.35)
        else:
            img = ImageOps.autocontrast(img, cutoff=0.5)

        # Filtro de nitidez para texto en fotos tomadas con cámara de celular en tiempo real
        img = img.filter(ImageFilter.SHARPEN)

        max_dim = 2048
        if max(img.size) > max_dim:
            img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

        buf = BytesIO()
        img.save(buf, format='JPEG', quality=95, optimize=True)
        return buf.getvalue()
    except Exception as exc:
        logger.warning('No se pudo corregir u optimizar imagen en bot: %s', exc)
        return contenido


async def _escanear_en_backend(context: ContextTypes.DEFAULT_TYPE, user_id: int, tipo_doc: str, archivo: Archivo) -> dict[str, Any]:
    backend: ClienteBackend = context.application.bot_data['backend']
    contenido_corregido = _corregir_orientacion(archivo.contenido, archivo.mime)
    archivo_envio = (archivo.nombre, contenido_corregido, archivo.mime)

    respuesta = await backend.peticion(
        user_id, 'POST', '/api/escanear-documento', timeout=90,
        data={'tipo_documento': tipo_doc},
        files=[('archivo', archivo_envio)],
    )
    datos = respuesta.datos if isinstance(respuesta.datos, dict) else {}
    return datos.get('campos') or {}


# --- INICIO Y MENSAJES DE CADA PASO ---

async def iniciar_registro(update: Update, context: ContextTypes.DEFAULT_TYPE, reclutadores: list[str] | None = None) -> None:
    user_id = update.effective_user.id
    if reclutadores is None:
        reclutadores = []
        try:
            backend: ClienteBackend = context.application.bot_data['backend']
            resp = await backend.peticion(user_id, 'GET', '/api/reclutadores-nombres')
            datos = resp.datos if isinstance(resp.datos, dict) else {}
            items = datos.get('reclutadores') or (resp.datos if isinstance(resp.datos, list) else [])
            reclutadores = [
                str(r.get('nombre') if isinstance(r, dict) else r).strip()
                for r in items if str(r).strip()
            ]
        except Exception as e:
            logger.warning('No fue posible precargar reclutadores: %s', e)

    # Filtrar que los reclutadores no contengan fuentes de encuesta
    fuentes_norm = {normalizar_texto(f) for f in FUENTES_REGISTRO}
    reclutadores_limpios = [r for r in reclutadores if normalizar_texto(r) not in fuentes_norm]

    sesion = SesionRegistro(user_id=user_id, estado=EstadoRegistro.ESPERANDO_ACTA, reclutadores=reclutadores_limpios)
    registro_activo[user_id] = sesion

    texto = (
        "✍️ **Generar Registro de Candidato**\n\n"
        "📸 Por favor envía la foto o PDF del **Acta de Nacimiento**:"
    )
    await responder(update, texto, reply_markup=_teclado_paso_doc())


# --- VALIDACIÓN INMEDIATA PASO A PASO (NOMBRE, CURP, RFC, NSS) ---

ETIQUETAS_CAMPOS = {
    'nombre_completo': 'Nombre completo',
    'curp': 'CURP',
    'rfc': 'RFC',
    'nss': 'Número de Seguro Social (NSS)',
    'fecha_nac': 'Fecha de nacimiento (AAAA-MM-DD)',
    'domicilio': 'Dirección (Calle, Número, Colonia, CP, Municipio, Estado)',
    'genero': 'Sexo / Género (MASCULINO / FEMENINO)',
    'procedencia': '¿De dónde vienes? (Lugar / Entidad de nacimiento)',
    'nacionalidad': 'Nacionalidad',
    'tel_movil': 'Teléfono móvil',
    'correo': 'Correo electrónico',
    'edo_civil': 'Estado civil',
    'emergencia': 'Contacto de emergencia (Nombre, Parentesco, Teléfono)',
    'encuesta': 'Medio de reclutamiento y Reclutador / Recomendó',
}


async def iniciar_confirmacion_critica(
    update: Update,
    sesion: SesionRegistro,
    campos: list[str],
    siguiente_estado: EstadoRegistro,
    doc_origen: str,
    prefijo: str = '',
) -> None:
    """Inicia la validación inmediata para cada dato crítico extraído."""
    sesion.cola_confirmacion = [c for c in campos if sesion.datos.get(c)]
    sesion.siguiente_estado_post_conf = siguiente_estado
    sesion.ultimo_doc_para_reintentar = doc_origen
    await avanzar_confirmacion_critica(update, sesion, prefijo=prefijo)


async def avanzar_confirmacion_critica(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    """Pide confirmación para el siguiente dato crítico en cola o continúa el flujo."""
    if not sesion.cola_confirmacion:
        sesion.campo_confirmando = None
        sig = sesion.siguiente_estado_post_conf
        await ir_al_estado(update, sesion, sig, prefijo=prefijo)
        return

    campo = sesion.cola_confirmacion.pop(0)
    sesion.campo_confirmando = campo
    sesion.estado = EstadoRegistro.CONFIRMANDO_DATO_PASO

    etiqueta = ETIQUETAS_CAMPOS.get(campo, campo)
    valor = str(sesion.datos.get(campo) or 'Sin dato').strip()

    partes = []
    if prefijo:
        partes.append(prefijo)
    partes.append(f"🔍 **Confirmación de dato extraído:**\n")
    partes.append(f"• **{etiqueta}:** `{valor}`")

    # Validación estricta de formato CURP / RFC
    formato_invalido = False
    if campo == 'curp' and valor != 'Sin dato':
        es_valida, advertencia = validar_curp_formato(valor)
        if not es_valida:
            partes.append(f"\n⚠️ **Formato CURP no válido:** {advertencia}")
            formato_invalido = True
        else:
            partes.append(f"   *(Estructura oficial de 18 caracteres correcta ✅)*")
    elif campo == 'rfc' and valor != 'Sin dato':
        es_valido, advertencia = validar_rfc_formato(valor)
        if not es_valido:
            partes.append(f"\n⚠️ **Formato RFC no válido:** {advertencia}")
            formato_invalido = True
        else:
            partes.append(f"   *(Estructura oficial correcta ✅)*")

    if formato_invalido:
        partes.append(f"\n⚠️ La clave no concuerda con la estructura oficial. Toma la foto de nuevo con mejor enfoque o escribe el dato:")
        teclado = InlineKeyboardMarkup([
            [boton('📸 Tomar foto de nuevo', 'reg:conf_paso_refoto'), boton('✏️ Corregir / Escribir', 'reg:conf_paso_corregir')],
            [boton('❌ Cancelar', 'reg:cancelar')],
        ])
    else:
        partes.append(f"\n¿Este dato es correcto?")
        teclado = InlineKeyboardMarkup([
            [boton('✅ Es correcto', 'reg:conf_paso_ok')],
            [boton('✏️ Corregir / Escribir', 'reg:conf_paso_corregir'), boton('📸 Tomar foto de nuevo', 'reg:conf_paso_refoto')],
            [boton('❌ Cancelar', 'reg:cancelar')],
        ])
    await responder(update, '\n'.join(partes), reply_markup=teclado)


async def ir_al_estado(update: Update, sesion: SesionRegistro, estado: EstadoRegistro, prefijo: str = '') -> None:
    if estado == EstadoRegistro.ESPERANDO_DOMICILIO:
        await _pedir_domicilio(update, sesion, prefijo=prefijo)
    elif estado == EstadoRegistro.ESPERANDO_INE:
        await _pedir_ine(update, sesion, prefijo=prefijo)
    elif estado == EstadoRegistro.ESPERANDO_DOC_CURP:
        await _pedir_doc_curp(update, sesion, prefijo=prefijo)
    elif estado == EstadoRegistro.ESPERANDO_CSF:
        await _pedir_csf(update, sesion, prefijo=prefijo)
    elif estado == EstadoRegistro.ESPERANDO_NSS:
        await _pedir_nss(update, sesion, prefijo=prefijo)
    elif estado == EstadoRegistro.ESPERANDO_EMERGENCIA_NOMBRE:
        await _pedir_emergencia_nombre(update, sesion, prefijo=prefijo)
    elif estado == EstadoRegistro.ESPERANDO_TEL_CANDIDATO:
        await _pedir_tel_candidato(update, sesion, prefijo=prefijo)
    elif estado == EstadoRegistro.ESPERANDO_CORREO_CANDIDATO:
        await _pedir_correo_candidato(update, sesion, prefijo=prefijo)
    elif estado == EstadoRegistro.ESPERANDO_EDO_CIVIL:
        await _pedir_edo_civil(update, sesion, prefijo=prefijo)
    elif estado == EstadoRegistro.ESPERANDO_NACIONALIDAD:
        await _pedir_nacionalidad(update, sesion, prefijo=prefijo)
    elif estado == EstadoRegistro.ESPERANDO_FOTO_CANDIDATO:
        await _pedir_foto_candidato(update, sesion, prefijo=prefijo)
    elif estado == EstadoRegistro.ESPERANDO_ENCUESTA_FUENTE:
        await _pedir_encuesta_fuente(update, sesion, prefijo=prefijo)
    else:
        await verificar_faltantes_o_confirmar(update, sesion)


async def _pedir_domicilio(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_DOMICILIO
    texto = (
        f"{prefijo}\n\n"
        "📸 Por favor envía la foto o PDF del **Comprobante de Domicilio** (CFE, agua, teléfono, etc.):"
    ).strip()
    await responder(update, texto, reply_markup=_teclado_paso_doc('domicilio'))


async def _pedir_ine(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_INE
    texto = (
        f"{prefijo}\n\n"
        "📸 Por favor envía la foto o PDF de la **Credencial de Elector (INE / IFE)** o **Licencia de Conducir**:\n"
        "*(Se utilizará únicamente para corroborar los datos de Nombre y CURP)*"
    ).strip()
    await responder(update, texto, reply_markup=_teclado_paso_doc('ine'))


async def _pedir_doc_curp(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_DOC_CURP
    texto = (
        f"{prefijo}\n\n"
        "📸 Por favor envía la foto o PDF de la **Constancia de CURP** oficial (o escríbela directamente por texto):"
    ).strip()
    teclado = InlineKeyboardMarkup([
        [boton('⏩ Omitir por ahora', 'reg:omitir_curp_doc'), boton('❌ Cancelar', 'reg:cancelar')]
    ])
    await responder(update, texto, reply_markup=teclado)


async def _pedir_csf(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_CSF
    texto = (
        f"{prefijo}\n\n"
        "📸 Por favor envía la foto o PDF de la **Constancia de Situación Fiscal (SAT)**:\n"
        "*(Únicamente se tomará el dato del RFC)*"
    ).strip()
    await responder(update, texto, reply_markup=_teclado_paso_doc('csf'))


async def _pedir_nss(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_NSS
    texto = (
        f"{prefijo}\n\n"
        "📸 Por favor envía la foto o PDF del documento del **Número de Seguro Social (IMSS)**:\n"
        "*(Únicamente se tomará el dato del NSS)*"
    ).strip()
    await responder(update, texto, reply_markup=_teclado_paso_doc('nss'))


async def _pedir_emergencia_nombre(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_EMERGENCIA_NOMBRE
    texto = (
        f"{prefijo}\n\n"
        "🚨 Escribe el **Nombre completo** de la persona para contactar en caso de emergencia:"
    ).strip()
    teclado = InlineKeyboardMarkup([[boton('⏩ Omitir contacto', 'reg:omitir_contacto_eme'), boton('❌ Cancelar', 'reg:cancelar')]])
    await responder(update, texto, reply_markup=teclado)


async def _pedir_tel_candidato(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_TEL_CANDIDATO
    texto = (
        f"{prefijo}\n\n"
        "📱 Escribe el **Teléfono móvil** del candidato (10 dígitos):"
    ).strip()
    teclado = InlineKeyboardMarkup([[boton('❌ Cancelar', 'reg:cancelar')]])
    await responder(update, texto, reply_markup=teclado)


async def _pedir_correo_candidato(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_CORREO_CANDIDATO
    texto = (
        f"{prefijo}\n\n"
        "📧 Escribe el **Correo electrónico** del candidato (o pulsa «Omitir»):"
    ).strip()
    teclado = InlineKeyboardMarkup([[boton('⏩ Omitir correo', 'reg:omitir_correo'), boton('❌ Cancelar', 'reg:cancelar')]])
    await responder(update, texto, reply_markup=teclado)


async def _pedir_edo_civil(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_EDO_CIVIL
    texto = (
        f"{prefijo}\n\n"
        "💍 Selecciona el **Estado Civil** del candidato:\n"
        "Elige una opción con los botones o escríbelo:"
    ).strip()
    filas = [[boton(opc, f'reg:edocivil:{opc}')] for opc in ESTADOS_CIVILES]
    filas.append([boton('⏩ Omitir estado civil', 'reg:omitir_edocivil'), boton('❌ Cancelar', 'reg:cancelar')])
    await responder(update, texto, reply_markup=InlineKeyboardMarkup(filas))


async def _pedir_nacionalidad(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_NACIONALIDAD
    texto = (
        f"{prefijo}\n\n"
        "🌎 ¿Cuál es la **Nacionalidad** del candidato?"
    ).strip()
    teclado = InlineKeyboardMarkup([
        [boton('🇲🇽 Mexicana', 'reg:nac:MEXICANA')],
        [boton('✏️ Otra nacionalidad', 'reg:nac:otra')],
        [boton('❌ Cancelar', 'reg:cancelar')],
    ])
    await responder(update, texto, reply_markup=teclado)


async def _pedir_foto_candidato(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_FOTO_CANDIDATO
    texto = (
        f"{prefijo}\n\n"
        "📸 **Fotografía para Expediente Digital**\n\n"
        "Por favor toma una foto de frente del candidato/empleado con la cámara de Telegram (rostro y hombros) o selecciónala de tu galería.\n"
        "Se guardará en el expediente respetando la proporción natural de la captura."
    ).strip()
    teclado = InlineKeyboardMarkup([
        [boton('⏩ Omitir foto', 'reg:omitir_foto'), boton('❌ Cancelar', 'reg:cancelar')]
    ])
    await responder(update, texto, reply_markup=teclado)


async def _pedir_encuesta_fuente(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_ENCUESTA_FUENTE
    texto = (
        f"{prefijo}\n\n"
        "**Encuesta de Reclutamiento**\n"
        "📊 ¿Por qué medio nos conoció el candidato?\n"
        "Elige una opción con los botones o escribe el medio:"
    ).strip()
    filas = [[boton(f, f'reg:f:{f}')] for f in FUENTES_REGISTRO]
    filas.append([boton('❌ Cancelar', 'reg:cancelar')])
    await responder(update, texto, reply_markup=InlineKeyboardMarkup(filas))


async def _pedir_encuesta_reclutador(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_ENCUESTA_RECLUTADOR
    texto = (
        f"{prefijo}\n\n"
        "👤 ¿Quién es el **Reclutador** asignado?\n"
        "Elige de la lista o escribe el nombre del reclutador:"
    ).strip()
    filas = [[boton(r, f'reg:rec:{r[:25]}')] for r in sesion.reclutadores[:8]]
    filas.append([boton('⏩ Sin reclutador / Omitir', 'reg:omitir'), boton('❌ Cancelar', 'reg:cancelar')])
    await responder(update, texto, reply_markup=InlineKeyboardMarkup(filas))


# --- RESOLUCIÓN Y VERIFICACIÓN DE FALTANTES ---

def ejecutar_validacion_consenso(sesion: SesionRegistro) -> tuple[dict[str, str], list[str]]:
    """
    Consolida datos entre documentos para nombre, curp, rfc, nss, sexo, fecha_nac.
    Devuelve (datos_resueltos, mensajes_consenso).
    """
    d = sesion.datos
    avisos: list[str] = []

    # 1. CURP (si ya fue confirmada previamente por el usuario, NUNCA se sobreescribe)
    if 'curp' not in sesion.campos_confirmados:
        curps = [
            d.get('curp_doc', {}).get('curp'),
            d.get('acta', {}).get('curp'),
            d.get('ine', {}).get('curp'),
            d.get('csf', {}).get('curp'),
            d.get('nss_doc', {}).get('curp'),
        ]
        curp_win, hay_disc_curp, vars_curp = resolver_por_mayoria(curps)
        if curp_win:
            d['curp'] = curp_win

    # 2. NOMBRE COMPLETO (si ya fue confirmado, NUNCA se sobreescribe)
    if 'nombre_completo' not in sesion.campos_confirmados:
        nom_acta = d.get('acta', {}).get('nombre_completo')
        nom_pila = d.get('acta', {}).get('nombre', '')
        ap_pat = d.get('acta', {}).get('primer_apellido', '')
        ap_mat = d.get('acta', {}).get('segundo_apellido', '')

        nom_curp = ordenar_nombre_natural(d.get('curp_doc', {}).get('nombre_completo', ''), nom_pila, ap_pat, ap_mat)
        nom_ine = ordenar_nombre_natural(d.get('ine', {}).get('nombre_completo', ''), nom_pila, ap_pat, ap_mat)
        nom_csf = ordenar_nombre_natural(d.get('csf', {}).get('nombre_completo', ''), nom_pila, ap_pat, ap_mat)
        nom_nss = ordenar_nombre_natural(d.get('nss_doc', {}).get('nombre_completo', ''), nom_pila, ap_pat, ap_mat)
        nom_acta_nat = ordenar_nombre_natural(nom_acta, nom_pila, ap_pat, ap_mat) if nom_acta else ''

        nombres = [nom_acta_nat, nom_curp, nom_ine, nom_csf, nom_nss]
        nom_win, _, _ = resolver_por_mayoria(nombres)
        if nom_win:
            d['nombre_completo'] = nom_win

    # 3. SEXO
    sex_acta = d.get('acta', {}).get('sexo')
    sex_curp = d.get('curp_doc', {}).get('sexo')
    sex_ine = d.get('ine', {}).get('sexo')
    sexos = [sex_acta, sex_curp, sex_ine]
    sex_win, _, _ = resolver_por_mayoria(sexos)
    if sex_win:
        d['genero'] = sex_win
        d['sexo'] = sex_win

    # 4. FECHA DE NACIMIENTO
    fn_acta = d.get('acta', {}).get('fecha_nac')
    fn_curp = d.get('curp_doc', {}).get('fecha_nac')
    fn_ine = d.get('ine', {}).get('fecha_nac')
    fn_win, _, _ = resolver_por_mayoria([fn_acta, fn_curp, fn_ine])
    if fn_win:
        d['fecha_nac'] = fn_win

    # 5. RFC (si ya fue confirmado, se respeta)
    if 'rfc' not in sesion.campos_confirmados:
        rfc_csf = d.get('csf', {}).get('rfc')
        if rfc_csf:
            d['rfc'] = rfc_csf

    # 6. NSS (si ya fue confirmado, se respeta)
    if 'nss' not in sesion.campos_confirmados:
        nss_doc = d.get('nss_doc', {}).get('nss')
        if nss_doc:
            d['nss'] = nss_doc

    # Nacionalidad por defecto Mexicana si no está establecida
    if not d.get('nacionalidad'):
        d['nacionalidad'] = 'MEXICANA'

    return d, avisos


def obtener_campos_en_blanco(sesion: SesionRegistro) -> list[str]:
    """Identifica qué datos esenciales no fueron encontrados y siguen vacíos."""
    d = sesion.datos
    faltantes = []
    if not d.get('nombre_completo'):
        faltantes.append('nombre_completo')
    if not d.get('curp'):
        faltantes.append('curp')
    if not d.get('rfc'):
        faltantes.append('rfc')
    if not d.get('nss'):
        faltantes.append('nss')
    if not d.get('fecha_nac'):
        faltantes.append('fecha_nac')
    if not d.get('calle') and not d.get('colonia') and not d.get('cp'):
        faltantes.append('domicilio')
    return faltantes


async def verificar_faltantes_o_confirmar(update: Update, sesion: SesionRegistro) -> None:
    """Verifica si faltan campos en blanco; si faltan, los pide; si no, muestra el resumen de confirmación."""
    ejecutar_validacion_consenso(sesion)
    faltantes = obtener_campos_en_blanco(sesion)
    sesion.faltantes_pendientes = faltantes

    if faltantes:
        campo = faltantes[0]
        sesion.estado = EstadoRegistro.SOLICITANDO_FALTANTE
        etiqueta = ETIQUETAS_CAMPOS.get(campo, campo)
        texto = (
            "⚠️ **Dato faltante para el expediente**\n\n"
            f"No encontramos el dato de **{etiqueta}** en los documentos proporcionados.\n"
            f"Por favor escríbelo directamente para que el registro no quede en blanco (o escribe «Omitir» si no lo tienes):"
        )
        teclado = InlineKeyboardMarkup([[boton('⏩ Omitir este dato', 'reg:omitir_faltante'), boton('❌ Cancelar', 'reg:cancelar')]])
        await responder(update, texto, reply_markup=teclado)
    else:
        await mostrar_pantalla_confirmacion(update, sesion)


async def mostrar_pantalla_confirmacion(update: Update, sesion: SesionRegistro) -> None:
    sesion.estado = EstadoRegistro.CONFIRMAR_DATOS
    d, avisos = ejecutar_validacion_consenso(sesion)

    nombre = d.get('nombre_completo') or 'Sin dato'
    curp = d.get('curp') or 'Sin dato'
    rfc = d.get('rfc') or 'Sin dato'
    nss = d.get('nss') or 'Sin dato'
    fecha_nac = d.get('fecha_nac') or 'Sin dato'
    edad = calcular_edad(fecha_nac)
    edad_str = f" ({edad} años)" if edad else ""
    sexo = d.get('genero') or d.get('sexo') or 'Sin dato'
    lugar = d.get('procedencia') or 'Sin dato'
    nacionalidad = d.get('nacionalidad') or 'MEXICANA'

    calle = d.get('calle') or ''
    num_ext = d.get('num_ext') or ''
    colonia = d.get('colonia') or ''
    municipio = d.get('municipio') or ''
    estado_dir = d.get('estado') or ''
    cp = d.get('cp') or ''
    partes_dir = filter(None, [calle, f"Ext. {num_ext}" if num_ext else None, f"Col. {colonia}" if colonia else None, f"CP {cp}" if cp else None, municipio, estado_dir])
    dir_str = ', '.join(partes_dir) or 'Sin dato'

    tel = d.get('tel_movil') or 'Sin dato'
    correo = d.get('correo') or 'Sin dato'
    edo_civil = d.get('edo_civil') or 'Sin dato'
    nom_eme = d.get('nom_eme1') or 'Sin dato'
    par_eme = d.get('par_eme1') or 'Sin parentesco'
    tel_eme = d.get('tel_eme1') or 'Sin teléfono'
    foto_estado = "Adjunta ✅" if d.get('fotografia_base64') else "No proporcionada"

    fuente = d.get('fuente') or 'Sin dato'
    rec = d.get('nombre_reclutador') or 'Sin asignar'
    recomendo = d.get('nombre_recomendador')

    lineas_resumen = [
        "📋 **Resumen de Registro de Candidato:**",
        "",
        f"• **Nombre:** {nombre}",
        f"• **CURP:** `{curp}`",
        f"• **RFC:** `{rfc}`",
        f"• **NSS:** `{nss}`",
        f"• **Fecha nac.:** {fecha_nac}{edad_str}",
        f"• **Sexo:** {sexo}",
        f"• **¿De dónde vienes? (Lugar):** {lugar}",
        f"• **Nacionalidad:** {nacionalidad}",
        f"• **Dirección:** {dir_str}",
        f"• **Teléfono candidato:** {tel}",
        f"• **Correo:** {correo}",
        f"• **Estado Civil:** {edo_civil}",
        f"• **Contacto de emergencia:** {nom_eme} ({par_eme}) - {tel_eme}",
        f"• **Fotografía de expediente:** {foto_estado}",
        f"• **Medio de reclutamiento:** {fuente}",
    ]
    if 'recomen' in fuente.lower() and recomendo:
        lineas_resumen.append(f"• **Recomendado por:** {recomendo}")
    elif fuente in ('Facebook', 'Reclu CW') or rec != 'Sin asignar':
        lineas_resumen.append(f"• **Reclutador asignado:** {rec}")

    lineas_resumen.append("")
    lineas_resumen.append("¿Todos estos datos son correctos?")

    teclado = InlineKeyboardMarkup([
        [boton('✅ Sí, todo correcto', 'reg:confirmar_si')],
        [boton('✏️ Modificar un dato', 'reg:editar_menu'), boton('❌ Cancelar', 'reg:cancelar')],
    ])
    await responder(update, '\n'.join(lineas_resumen), reply_markup=teclado)


async def mostrar_menu_edicion(update: Update, sesion: SesionRegistro) -> None:
    texto = (
        "✏️ **Elige qué dato deseas corregir o modificar:**\n"
        "Al tocar un botón te mostraré el valor actual y podrás escribir el nuevo valor:"
    )
    filas = [
        [boton('Nombre completo', 'reg:edit:nombre_completo'), boton('CURP', 'reg:edit:curp')],
        [boton('RFC', 'reg:edit:rfc'), boton('NSS', 'reg:edit:nss')],
        [boton('Fecha de nacimiento', 'reg:edit:fecha_nac'), boton('Sexo / Género', 'reg:edit:genero')],
        [boton('Dirección completa', 'reg:edit:domicilio'), boton('📍 De dónde vienes', 'reg:edit:procedencia')],
        [boton('Nacionalidad', 'reg:edit:nacionalidad'), boton('Teléfono candidato', 'reg:edit:tel_movil')],
        [boton('Correo electrónico', 'reg:edit:correo'), boton('Estado Civil', 'reg:edit:edo_civil')],
        [boton('Contacto emergencia', 'reg:edit:emergencia'), boton('Medio / Reclutador', 'reg:edit:encuesta')],
        [boton('⬅️ Volver a confirmar', 'reg:volver_confirmar')],
    ]
    await responder(update, texto, reply_markup=InlineKeyboardMarkup(filas))


# --- MANEJADOR DE ARCHIVOS (OCR Y FOTOGRAFÍA) ---

async def atender_archivo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    sesion = registro_activo.get(user_id)
    if not sesion:
        return False

    mensaje = update.effective_message
    if mensaje.photo:
        foto = mensaje.photo[-1]
        nombre, tamano, file_id = 'foto.jpg', foto.file_size or 0, foto.file_id
    elif mensaje.document:
        doc = mensaje.document
        nombre, tamano, file_id = doc.file_name or 'documento.pdf', doc.file_size or 0, doc.file_id
    else:
        await responder(update, '⚠️ Por favor envía una imagen (foto) o archivo PDF.')
        return True

    if tamano > 10 * 1024 * 1024:
        await responder(update, '⚠️ El archivo no puede superar 10 MB. Envía uno más ligero.')
        return True

    try:
        tg_file = await context.bot.get_file(file_id)
        contenido = bytes(await tg_file.download_as_bytearray())
    except Exception as exc:
        logger.error('Error descargando archivo de Telegram: %s', exc)
        await responder(update, '⚠️ Ocurrió un error al descargar tu archivo. Inténtalo de nuevo.')
        return True

    mime = 'application/pdf' if nombre.lower().endswith('.pdf') else 'image/jpeg'
    archivo = Archivo(campo='archivo', nombre=nombre, contenido=contenido, mime=mime)

    estado = sesion.estado

    # FOTO DEL CANDIDATO (EXPEDIENTE DIGITAL)
    if estado == EstadoRegistro.ESPERANDO_FOTO_CANDIDATO:
        foto_proc = procesar_foto_candidato(contenido)
        b64_foto = f"data:image/jpeg;base64,{base64.b64encode(foto_proc).decode('ascii')}"
        sesion.datos['fotografia_base64'] = b64_foto
        sesion.estado = EstadoRegistro.CONFIRMANDO_FOTO_CANDIDATO

        await update.effective_message.reply_photo(
            photo=foto_proc,
            caption=(
                "📸 **Vista previa de la fotografía para el expediente digital**\n\n"
                "La foto se guardará con la proporción natural capturada por tu cámara de Telegram.\n\n"
                "¿La fotografía se ve clara y bien enfocada o deseas tomar otra?"
            ),
            reply_markup=InlineKeyboardMarkup([
                [boton('✅ Se ve bien, continuar', 'reg:foto_ok')],
                [boton('📸 Tomar otra foto', 'reg:foto_retomar'), boton('⏩ Omitir foto', 'reg:omitir_foto')],
                [boton('❌ Cancelar', 'reg:cancelar')],
            ])
        )
        return True

    # REINTENTO POR FOTO TRAS RECHAZAR UN DATO
    if estado in (EstadoRegistro.CONFIRMANDO_DATO_PASO, EstadoRegistro.ESPERANDO_CORRECCION_PASO):
        doc_tipo = sesion.ultimo_doc_para_reintentar or 'Acta de Nacimiento'
        await responder(update, '🔍 Analizando...')
        try:
            leidos = await _escanear_en_backend(context, user_id, doc_tipo, archivo)
            if not leidos or not any(leidos.values()):
                raise ValueError(f'No se detectaron campos legibles en {doc_tipo}.')
        except Exception as exc:
            await responder(
                update,
                f'⚠️ No se pudo leer claramente {doc_tipo}.\n'
                'Puedes escribir el dato por texto o intentar con otra foto:',
                reply_markup=InlineKeyboardMarkup([
                    [boton('✏️ Escribir dato', 'reg:conf_paso_corregir'), boton('❌ Cancelar', 'reg:cancelar')]
                ])
            )
            return True

        campo = sesion.campo_confirmando
        if campo and leidos.get(campo):
            val_nuevo = str(leidos.get(campo)).strip()
            formato_invalido = False
            adv_formato = ""
            if campo == 'nombre_completo':
                val_nuevo = ordenar_nombre_natural(val_nuevo)
            elif campo == 'curp':
                val_nuevo = val_nuevo.upper()
                ok_curp, err_curp = validar_curp_formato(val_nuevo)
                if not ok_curp:
                    formato_invalido = True
                    adv_formato = f"\n⚠️ **Formato CURP no válido:** {err_curp}"
            elif campo == 'rfc':
                val_nuevo = val_nuevo.upper()
                ok_rfc, err_rfc = validar_rfc_formato(val_nuevo)
                if not ok_rfc:
                    formato_invalido = True
                    adv_formato = f"\n⚠️ **Formato RFC no válido:** {err_rfc}"
            sesion.datos[campo] = val_nuevo
            sesion.estado = EstadoRegistro.CONFIRMANDO_DATO_PASO
            etiqueta = ETIQUETAS_CAMPOS.get(campo, campo)
            if formato_invalido:
                texto = (
                    f"⚠️ **Dato detectado nuevamente:**\n"
                    f"• **{etiqueta}:** `{val_nuevo}`{adv_formato}\n\n"
                    "La clave no cumple con la estructura oficial. Toma otra foto o escríbela:"
                )
                teclado = InlineKeyboardMarkup([
                    [boton('📸 Tomar foto de nuevo', 'reg:conf_paso_refoto'), boton('✏️ Corregir / Escribir', 'reg:conf_paso_corregir')],
                    [boton('❌ Cancelar', 'reg:cancelar')],
                ])
            else:
                texto = (
                    f"✅ **Dato detectado nuevamente:**\n"
                    f"• **{etiqueta}:** `{val_nuevo}`\n\n"
                    f"¿Este dato es correcto ahora?"
                )
                teclado = InlineKeyboardMarkup([
                    [boton('✅ Es correcto', 'reg:conf_paso_ok')],
                    [boton('✏️ Corregir / Escribir', 'reg:conf_paso_corregir'), boton('📸 Tomar foto de nuevo', 'reg:conf_paso_refoto')],
                    [boton('❌ Cancelar', 'reg:cancelar')],
                ])
            await responder(update, texto, reply_markup=teclado)
            return True

    # ACTA DE NACIMIENTO
    if estado == EstadoRegistro.ESPERANDO_ACTA:
        await responder(update, '🔍 Analizando...')
        try:
            leidos = await _escanear_en_backend(context, user_id, 'Acta de Nacimiento', archivo)
            if not leidos or not any(leidos.values()):
                raise ValueError('No se pudieron detectar campos legibles en el Acta.')
        except Exception as exc:
            logger.warning('Falla en escaneo de Acta: %s', exc)
            await responder(
                update,
                '⚠️ No pude leer el documento enviado.\n'
                'Por favor envía una foto clara y enfocada del **Acta de Nacimiento**, o pulsa «Omitir» si no cuentas con ella.',
                reply_markup=_teclado_paso_doc(),
            )
            return True

        curp = str(leidos.get('curp') or '').strip().upper()
        nom_pila = str(leidos.get('nombre') or '').strip()
        ap_pat = str(leidos.get('primer_apellido') or '').strip()
        ap_mat = str(leidos.get('segundo_apellido') or '').strip()
        nombre_raw = str(leidos.get('nombre_completo') or '').strip()
        nombre_nat = ordenar_nombre_natural(nombre_raw, nom_pila, ap_pat, ap_mat)

        sexo = normalizar_sexo(leidos.get('genero') or leidos.get('sexo'))
        fecha_nac = str(leidos.get('fecha_nac') or leidos.get('fecha_nacimiento') or '').strip()
        lugar_nac = str(leidos.get('lugar_nacimiento') or '').strip()
        ent_nac = str(leidos.get('entidad_nacimiento') or '').strip()
        lugar_reg = str(leidos.get('lugar_registro') or '').strip()
        ent_reg = str(leidos.get('entidad_registro') or '').strip()

        # Validación estricta de que el documento enviado sea un Acta de Nacimiento
        es_acta_valida = bool(curp or (nombre_nat and (fecha_nac or lugar_nac or ent_nac or lugar_reg or ent_reg)))
        if not es_acta_valida:
            await responder(
                update,
                '⚠️ El documento enviado no parece ser un **Acta de Nacimiento** válida.\n'
                'Por favor asegúrate de enviar la foto o PDF del **Acta de Nacimiento** oficial, o pulsa «Omitir».',
                reply_markup=_teclado_paso_doc(),
            )
            return True

        # Construir procedencia: de dónde vienes
        partes_proc = []
        if lugar_nac and lugar_nac.lower() not in ('null', 'none'):
            partes_proc.append(lugar_nac)
        if ent_nac and ent_nac.lower() not in ('null', 'none') and ent_nac not in partes_proc:
            partes_proc.append(ent_nac)
        if not partes_proc:
            if lugar_reg and lugar_reg.lower() not in ('null', 'none'):
                partes_proc.append(lugar_reg)
            if ent_reg and ent_reg.lower() not in ('null', 'none') and ent_reg not in partes_proc:
                partes_proc.append(ent_reg)
        procedencia = ', '.join(partes_proc)

        sesion.datos['acta'] = {
            'curp': curp,
            'nombre': nom_pila,
            'primer_apellido': ap_pat,
            'segundo_apellido': ap_mat,
            'nombre_completo': nombre_nat,
            'sexo': sexo,
            'fecha_nac': fecha_nac,
            'lugar_nacimiento': lugar_nac,
            'entidad_nacimiento': ent_nac,
            'lugar_registro': lugar_reg,
            'entidad_registro': ent_reg,
        }
        if nombre_nat:
            sesion.datos['nombre_completo'] = nombre_nat
        if curp:
            sesion.datos['curp'] = curp
        if sexo:
            sesion.datos['sexo'] = sexo
            sesion.datos['genero'] = sexo
        if fecha_nac:
            sesion.datos['fecha_nac'] = fecha_nac
        if procedencia:
            sesion.datos['procedencia'] = procedencia

        resumen = (
            "✅ **Acta de Nacimiento leída:**\n"
            f"• **Nombre:** {nombre_nat or 'No legible'}\n"
            f"• **CURP:** {curp or 'No legible'}\n"
            f"• **Sexo:** {sexo or 'No legible'}\n"
            f"• **Fecha nac.:** {fecha_nac or 'No legible'}\n"
            f"• **¿De dónde vienes? (Procedencia):** {procedencia or 'No detectado'}"
        )

        # Campos críticos a confirmar inmediatamente
        criticos = []
        if nombre_nat:
            criticos.append('nombre_completo')
        if curp:
            criticos.append('curp')

        if criticos:
            await iniciar_confirmacion_critica(
                update, sesion, criticos,
                siguiente_estado=EstadoRegistro.ESPERANDO_DOMICILIO,
                doc_origen='Acta de Nacimiento',
                prefijo=resumen
            )
        else:
            await _pedir_domicilio(update, sesion, prefijo=resumen)
        return True

    # COMPROBANTE DE DOMICILIO
    elif estado == EstadoRegistro.ESPERANDO_DOMICILIO:
        await responder(update, '🔍 Analizando...')
        try:
            leidos = await _escanear_en_backend(context, user_id, 'Comprobante de Domicilio', archivo)
            if not leidos or not any(leidos.values()):
                raise ValueError('No se pudieron detectar campos legibles en el Comprobante.')
        except Exception as exc:
            logger.warning('Falla en escaneo de Domicilio: %s', exc)
            await responder(
                update,
                '⚠️ No pude leer el comprobante de domicilio.\n'
                'Por favor envía una foto con mejor iluminación y nítida, o pulsa «Omitir».',
                reply_markup=_teclado_paso_doc('domicilio'),
            )
            return True

        prop = str(leidos.get('nombre_propietario') or '').strip()
        calle = str(leidos.get('calle') or '').strip()
        num_ext = str(leidos.get('num_ext') or leidos.get('numero_exterior') or '').strip()
        num_int = str(leidos.get('num_int') or leidos.get('numero_interior') or '').strip()
        colonia = str(leidos.get('colonia') or '').strip()
        municipio = str(leidos.get('municipio') or '').strip()
        estado_dir = str(leidos.get('estado') or '').strip()
        cp = str(leidos.get('cp') or leidos.get('codigo_postal') or '').strip()

        # Validación estricta: debe contener al menos un elemento de dirección
        es_dom_valido = bool(calle or cp or colonia or municipio)
        if not es_dom_valido:
            await responder(
                update,
                '⚠️ El documento enviado no parece ser un **Comprobante de Domicilio** válido (recibo de luz CFE, agua, teléfono, etc.).\n'
                'Por favor envía un recibo de servicios donde se aprecie claramente la dirección del cliente o suministro, o pulsa «Omitir».',
                reply_markup=_teclado_paso_doc('domicilio'),
            )
            return True

        sesion.datos['domicilio'] = {
            'nombre_propietario': prop,
            'calle': calle,
            'num_ext': num_ext,
            'num_int': num_int,
            'colonia': colonia,
            'municipio': municipio,
            'estado': estado_dir,
            'cp': cp,
        }
        sesion.datos['calle'] = calle
        sesion.datos['num_ext'] = num_ext
        sesion.datos['num_int'] = num_int
        sesion.datos['colonia'] = colonia
        sesion.datos['municipio'] = municipio
        sesion.datos['estado'] = estado_dir
        sesion.datos['cp'] = cp
        # Proteger dirección para que ningún otro documento la modifique
        sesion.campos_confirmados.add('domicilio')

        partes_dir = filter(None, [calle, f"Ext. {num_ext}" if num_ext else None, f"Int. {num_int}" if num_int else None, f"Col. {colonia}" if colonia else None, f"C.P. {cp}" if cp else None, municipio, estado_dir])
        dir_resumen = ', '.join(partes_dir) or 'Dirección detectada'

        cand_nombre = sesion.datos.get('nombre_completo', '')
        es_titular = bool(prop and cand_nombre and cadenas_coinciden(prop, cand_nombre))

        if es_titular:
            sesion.datos['parentesco_propietario'] = 'Titular / Mismo'
            resumen = (
                "✅ **Comprobante de Domicilio analizado:**\n"
                f"• **Titular:** {prop} (Coincide con candidato ✅)\n"
                f"• **Dirección:** {dir_resumen}"
            )
            await _pedir_ine(update, sesion, prefijo=resumen)
        elif prop:
            sesion.estado = EstadoRegistro.ESPERANDO_PARENTESCO_DOMICILIO
            resumen = (
                "✅ **Comprobante de Domicilio analizado:**\n"
                f"• **Titular:** {prop}\n"
                f"• **Dirección:** {dir_resumen}\n\n"
                f"❓ El comprobante está a nombre de «{prop}».\n"
                f"¿Qué parentesco tienes con esta persona? (ej. Madre, Padre, Cónyuge, Familiar, o escribe «Propio» si eres tú):"
            )
            teclado = InlineKeyboardMarkup([[boton('⏩ Omitir parentesco', 'reg:omitir_domicilio'), boton('❌ Cancelar', 'reg:cancelar')]])
            await responder(update, resumen, reply_markup=teclado)
        else:
            sesion.datos['parentesco_propietario'] = 'No especificado'
            resumen = (
                "✅ **Comprobante de Domicilio analizado:**\n"
                f"• **Dirección:** {dir_resumen}"
            )
            await _pedir_ine(update, sesion, prefijo=resumen)
        return True

    # CREDENCIAL DE ELECTOR (INE / IFE) O LICENCIA DE CONDUCIR
    elif estado == EstadoRegistro.ESPERANDO_INE:
        await responder(update, '🔍 Analizando...')
        try:
            leidos = await _escanear_en_backend(context, user_id, 'INE', archivo)
            if not leidos or not any(leidos.values()):
                raise ValueError('No se pudieron detectar campos legibles en la identificación.')
        except Exception as exc:
            logger.warning('Falla en escaneo de INE / Licencia: %s', exc)
            await responder(
                update,
                '⚠️ No pude leer la identificación (INE / Licencia). Envía una foto más clara o pulsa «Omitir».',
                reply_markup=_teclado_paso_doc('ine'),
            )
            return True

        ine_nom_raw = str(leidos.get('nombre_completo') or '').strip()
        ine_nom = ordenar_nombre_natural(
            ine_nom_raw,
            sesion.datos.get('acta', {}).get('nombre', ''),
            sesion.datos.get('acta', {}).get('primer_apellido', ''),
            sesion.datos.get('acta', {}).get('segundo_apellido', '')
        )
        ine_curp = str(leidos.get('curp') or '').strip().upper()
        ine_sexo = normalizar_sexo(leidos.get('sexo') or leidos.get('genero'), es_ine=True)
        ine_fn = str(leidos.get('fecha_nac') or leidos.get('fecha_nacimiento') or '').strip()
        ine_dom = str(leidos.get('domicilio') or '').strip()

        # Validación estricta: debe contener al menos CURP o datos personales clave
        es_ine_valida = bool(ine_curp or (ine_nom and (ine_fn or ine_dom or ine_sexo)))
        if not es_ine_valida:
            await responder(
                update,
                '⚠️ El documento enviado no parece ser una **Credencial de Elector (INE / IFE)** o **Licencia de Conducir** válida.\n'
                'Por favor asegúrate de enviar la foto o PDF de la credencial oficial vigente, o pulsa «Omitir».',
                reply_markup=_teclado_paso_doc('ine'),
            )
            return True

        sesion.datos['ine'] = {'nombre_completo': ine_nom, 'curp': ine_curp, 'sexo': ine_sexo, 'fecha_nac': ine_fn, 'domicilio': ine_dom}

        # La INE / Licencia corrobora los datos; no sobrescribe datos ya confirmados
        if ine_nom and 'nombre_completo' not in sesion.campos_confirmados and not sesion.datos.get('nombre_completo'):
            sesion.datos['nombre_completo'] = ine_nom
        if ine_curp and 'curp' not in sesion.campos_confirmados and not sesion.datos.get('curp'):
            ok_curp_ine, _ = validar_curp_formato(ine_curp)
            if ok_curp_ine:
                sesion.datos['curp'] = ine_curp
        if ine_sexo and not sesion.datos.get('genero'):
            sesion.datos['genero'] = ine_sexo
            sesion.datos['sexo'] = ine_sexo
        if ine_fn and not sesion.datos.get('fecha_nac'):
            sesion.datos['fecha_nac'] = ine_fn
        # Si la dirección no se ha tomado y no está confirmada por comprobante, usar la de INE
        if 'domicilio' not in sesion.campos_confirmados and not sesion.datos.get('calle') and ine_dom:
            sesion.datos['calle'] = ine_dom

        lineas = ['✅ **Identificación oficial (INE / Licencia) analizada:**']
        if sesion.datos.get('nombre_completo'):
            if ine_nom:
                lineas.append(f'• **Nombre:** {sesion.datos.get("nombre_completo")} *(Corroborado con identificación ✅)*')
            else:
                lineas.append(f'• **Nombre:** {sesion.datos.get("nombre_completo")}')
        elif ine_nom:
            lineas.append(f'• **Nombre:** {ine_nom}')

        if sesion.datos.get('curp'):
            if ine_curp:
                lineas.append(f'• **CURP:** {sesion.datos.get("curp")} *(Corroborado con identificación ✅)*')
            else:
                lineas.append(f'• **CURP:** {sesion.datos.get("curp")}')
        elif ine_curp:
            lineas.append(f'• **CURP:** {ine_curp}')

        if sesion.datos.get('genero'):
            lineas.append(f'• **Sexo:** {sesion.datos.get("genero")}')
        if sesion.datos.get('fecha_nac'):
            lineas.append(f'• **Fecha nac.:** {sesion.datos.get("fecha_nac")}')

        resumen = '\n'.join(lineas)

        # Determinar siguiente paso: si aún no hay CURP confirmada o válida, pedir documento de CURP
        tiene_curp = bool(sesion.datos.get('curp') or ('curp' in sesion.campos_confirmados))
        siguiente = EstadoRegistro.ESPERANDO_CSF if tiene_curp else EstadoRegistro.ESPERANDO_DOC_CURP

        criticos = []
        if ine_nom and 'nombre_completo' not in sesion.campos_confirmados and not sesion.datos.get('nombre_completo'):
            criticos.append('nombre_completo')
        if ine_curp and 'curp' not in sesion.campos_confirmados and not sesion.datos.get('curp'):
            criticos.append('curp')

        if criticos:
            await iniciar_confirmacion_critica(
                update, sesion, criticos,
                siguiente_estado=siguiente,
                doc_origen='Identificación oficial',
                prefijo=resumen
            )
        else:
            await ir_al_estado(update, sesion, siguiente, prefijo=resumen)
        return True

    # DOCUMENTO DE CURP
    elif estado == EstadoRegistro.ESPERANDO_DOC_CURP:
        await responder(update, '🔍 Analizando...')
        try:
            leidos = await _escanear_en_backend(context, user_id, 'Documento CURP', archivo)
            if not leidos or not any(leidos.values()):
                raise ValueError('No se detectaron campos legibles en el documento de CURP.')
        except Exception as exc:
            logger.warning('Falla en escaneo de CURP: %s', exc)
            sesion.campo_confirmando = 'curp'
            sesion.ultimo_doc_para_reintentar = 'Constancia de CURP'
            await responder(
                update,
                '⚠️ No pude leer con claridad la constancia de CURP.\n'
                'Por favor envía una foto más nítida o escribe la CURP directamente por texto:',
                reply_markup=InlineKeyboardMarkup([
                    [boton('📸 Tomar foto de nuevo', 'reg:conf_paso_refoto'), boton('✏️ Escribir CURP', 'reg:conf_paso_corregir')],
                    [boton('⏩ Omitir', 'reg:omitir_curp_doc'), boton('❌ Cancelar', 'reg:cancelar')]
                ])
            )
            return True

        curp_extraida = str(leidos.get('curp') or '').strip().upper()
        nom_curp = ordenar_nombre_natural(str(leidos.get('nombre_completo') or '').strip())
        sex_curp = normalizar_sexo(leidos.get('genero') or leidos.get('sexo'))
        fn_curp = str(leidos.get('fecha_nac') or '').strip()

        # Validación estricta: debe contener una clave CURP con formato oficial de 18 caracteres
        es_valida_curp, err_curp = validar_curp_formato(curp_extraida)
        if not es_valida_curp:
            sesion.campo_confirmando = 'curp'
            sesion.ultimo_doc_para_reintentar = 'Constancia de CURP'
            await responder(
                update,
                f'⚠️ La clave detectada (`{curp_extraida or "No detectada"}`) no cumple con la estructura oficial de 18 caracteres ({err_curp}).\n'
                'Por favor toma una foto más clara y enfocada de la constancia de CURP o escribe la CURP directamente por texto:',
                reply_markup=InlineKeyboardMarkup([
                    [boton('📸 Tomar foto de nuevo', 'reg:conf_paso_refoto'), boton('✏️ Escribir CURP', 'reg:conf_paso_corregir')],
                    [boton('⏩ Omitir', 'reg:omitir_curp_doc'), boton('❌ Cancelar', 'reg:cancelar')]
                ])
            )
            return True

        sesion.datos['curp_doc'] = {
            'curp': curp_extraida,
            'nombre_completo': nom_curp,
            'sexo': sex_curp,
            'fecha_nac': fn_curp,
        }
        if 'curp' not in sesion.campos_confirmados:
            sesion.datos['curp'] = curp_extraida
        if nom_curp and 'nombre_completo' not in sesion.campos_confirmados and not sesion.datos.get('nombre_completo'):
            sesion.datos['nombre_completo'] = nom_curp

        resumen = (
            "✅ **Documento de CURP analizado:**\n"
            f"• **CURP:** {curp_extraida}\n"
            f"• **Nombre:** {nom_curp or 'No especificado'}"
        )

        criticos = ['curp'] if 'curp' not in sesion.campos_confirmados else []
        if criticos:
            await iniciar_confirmacion_critica(
                update, sesion, criticos,
                siguiente_estado=EstadoRegistro.ESPERANDO_CSF,
                doc_origen='Documento CURP',
                prefijo=resumen
            )
        else:
            await _pedir_csf(update, sesion, prefijo=resumen)
        return True

    # CONSTANCIA DE SITUACIÓN FISCAL (SAT)
    elif estado == EstadoRegistro.ESPERANDO_CSF:
        await responder(update, '🔍 Analizando...')
        try:
            leidos = await _escanear_en_backend(context, user_id, 'Constancia de Situación Fiscal', archivo)
            if not leidos or not any(leidos.values()):
                raise ValueError('No se pudieron detectar campos legibles en la CSF.')
        except Exception as exc:
            logger.warning('Falla en escaneo de CSF: %s', exc)
            sesion.campo_confirmando = 'rfc'
            sesion.ultimo_doc_para_reintentar = 'Constancia de Situación Fiscal'
            await responder(
                update,
                '⚠️ No pude leer la Constancia Fiscal. Envía una foto más legible o pulsa «Omitir».',
                reply_markup=_teclado_paso_doc('csf'),
            )
            return True

        # ÚNICAMENTE extraer RFC de la Constancia Fiscal. NUNCA tomar dirección, CURP ni nombre.
        rfc = str(leidos.get('rfc') or '').strip().upper()

        # Validación estricta: debe contener RFC de 12 o 13 caracteres con formato oficial
        es_valido_rfc, err_rfc = validar_rfc_formato(rfc)
        if not es_valido_rfc:
            sesion.campo_confirmando = 'rfc'
            sesion.ultimo_doc_para_reintentar = 'Constancia de Situación Fiscal'
            await responder(
                update,
                f'⚠️ El RFC detectado en la Constancia Fiscal (`{rfc or "No detectado"}`) no es válido ({err_rfc}).\n'
                'Por favor asegúrate de enviar la constancia oficial del SAT, toma una foto más nítida o escribe tu RFC por texto:',
                reply_markup=InlineKeyboardMarkup([
                    [boton('📸 Tomar foto de nuevo', 'reg:conf_paso_refoto'), boton('✏️ Escribir RFC', 'reg:conf_paso_corregir')],
                    [boton('⏩ Omitir constancia', 'reg:omitir_csf'), boton('❌ Cancelar', 'reg:cancelar')]
                ])
            )
            return True

        sesion.datos['csf'] = {'rfc': rfc}
        if 'rfc' not in sesion.campos_confirmados:
            sesion.datos['rfc'] = rfc

        resumen = (
            "✅ **Constancia de Situación Fiscal analizada:**\n"
            f"• **RFC:** {rfc}"
        )
        criticos = ['rfc'] if (rfc and 'rfc' not in sesion.campos_confirmados) else []

        if criticos:
            await iniciar_confirmacion_critica(
                update, sesion, criticos,
                siguiente_estado=EstadoRegistro.ESPERANDO_NSS,
                doc_origen='Constancia de Situación Fiscal',
                prefijo=resumen
            )
        else:
            await _pedir_nss(update, sesion, prefijo=resumen)
        return True

    # NÚMERO DE SEGURO SOCIAL (IMSS)
    elif estado == EstadoRegistro.ESPERANDO_NSS:
        await responder(update, '🔍 Analizando...')
        try:
            leidos = await _escanear_en_backend(context, user_id, 'Número de Seguro Social', archivo)
            if not leidos or not any(leidos.values()):
                raise ValueError('No se pudieron detectar campos legibles en el documento de NSS.')
        except Exception as exc:
            logger.warning('Falla en escaneo de NSS: %s', exc)
            sesion.campo_confirmando = 'nss'
            sesion.ultimo_doc_para_reintentar = 'Número de Seguro Social'
            await responder(
                update,
                '⚠️ No pude leer el NSS. Envía una foto más clara o pulsa «Omitir».',
                reply_markup=_teclado_paso_doc('nss'),
            )
            return True

        # ÚNICAMENTE extraer NSS (11 dígitos numéricos). NUNCA tomar CURP ni nombre.
        nss_raw = str(leidos.get('nss') or '').strip()
        nss = ''.join(c for c in nss_raw if c.isdigit())

        # Validación estricta: debe contener un NSS de 11 dígitos
        if not nss or len(nss) != 11:
            sesion.campo_confirmando = 'nss'
            sesion.ultimo_doc_para_reintentar = 'Número de Seguro Social'
            await responder(
                update,
                '⚠️ El documento enviado no parece ser el documento del **Número de Seguro Social (IMSS)** (no se detectó un NSS válido de 11 dígitos numéricos).\n'
                'Por favor asegúrate de enviar la constancia oficial del IMSS, toma una foto más nítida o escribe tu NSS por texto:',
                reply_markup=InlineKeyboardMarkup([
                    [boton('📸 Tomar foto de nuevo', 'reg:conf_paso_refoto'), boton('✏️ Escribir NSS', 'reg:conf_paso_corregir')],
                    [boton('⏩ Omitir NSS', 'reg:omitir_nss'), boton('❌ Cancelar', 'reg:cancelar')]
                ])
            )
            return True

        sesion.datos['nss_doc'] = {'nss': nss}
        if 'nss' not in sesion.campos_confirmados:
            sesion.datos['nss'] = nss

        resumen = (
            "✅ **Número de Seguro Social analizado:**\n"
            f"• **NSS:** {nss}"
        )
        criticos = ['nss'] if (nss and 'nss' not in sesion.campos_confirmados) else []

        if criticos:
            await iniciar_confirmacion_critica(
                update, sesion, criticos,
                siguiente_estado=EstadoRegistro.ESPERANDO_EMERGENCIA_NOMBRE,
                doc_origen='Número de Seguro Social',
                prefijo=resumen
            )
        else:
            await _pedir_emergencia_nombre(update, sesion, prefijo=resumen)
        return True

    else:
        await responder(
            update,
            'En este paso se espera una respuesta en texto o pulsar un botón. '
            'Por favor responde según lo solicitado o presiona «Cancelar» para descartar.'
        )
        return True


# --- MANEJADOR DE TEXTO LIBRE ---

async def atender_texto(update: Update, context: ContextTypes.DEFAULT_TYPE, texto_libre: str | None = None) -> bool:
    user_id = update.effective_user.id
    sesion = registro_activo.get(user_id)
    if not sesion:
        return False

    try:
        texto = (texto_libre if texto_libre is not None else update.effective_message.text or '').strip()

        if texto.lower() in ('/cancelar', 'cancelar', 'salir'):
            cancelar_registro(user_id)
            await responder(update, '✖️ Registro de candidato cancelado.')
            return True

        estado = sesion.estado

        # CORRECCIÓN EN TEXTO DE DATO CRÍTICO (PASO A PASO)
        if estado == EstadoRegistro.ESPERANDO_CORRECCION_PASO:
            campo = sesion.campo_confirmando
            if campo:
                val_limpio = texto.strip()
                aviso_formato = ""
                formato_invalido = False
                if campo == 'nombre_completo':
                    val_limpio = ordenar_nombre_natural(val_limpio)
                elif campo == 'curp':
                    val_limpio = val_limpio.upper()
                    ok_curp, err_curp = validar_curp_formato(val_limpio)
                    if not ok_curp:
                        formato_invalido = True
                        aviso_formato = f"\n⚠️ **Formato CURP no válido:** {err_curp}"
                elif campo == 'rfc':
                    val_limpio = val_limpio.upper()
                    ok_rfc, err_rfc = validar_rfc_formato(val_limpio)
                    if not ok_rfc:
                        formato_invalido = True
                        aviso_formato = f"\n⚠️ **Formato RFC no válido:** {err_rfc}"
                elif campo == 'nss':
                    val_limpio = ''.join(c for c in val_limpio if c.isdigit())
                    if len(val_limpio) != 11:
                        formato_invalido = True
                        aviso_formato = "\n⚠️ **Formato NSS no válido:** Debe contener exactamente 11 dígitos numéricos."
                sesion.datos[campo] = val_limpio
                sesion.estado = EstadoRegistro.CONFIRMANDO_DATO_PASO
                etiqueta = ETIQUETAS_CAMPOS.get(campo, campo)
                if formato_invalido:
                    texto_conf = (
                        f"⚠️ **Dato ingresado:**\n"
                        f"• **{etiqueta}:** `{val_limpio}`{aviso_formato}\n\n"
                        "La clave no cumple con la estructura oficial. Por favor escríbela de nuevo o toma otra foto:"
                    )
                    teclado = InlineKeyboardMarkup([
                        [boton('✏️ Corregir / Escribir', 'reg:conf_paso_corregir'), boton('📸 Tomar foto de nuevo', 'reg:conf_paso_refoto')],
                        [boton('❌ Cancelar', 'reg:cancelar')],
                    ])
                else:
                    texto_conf = (
                        f"✅ **Dato corregido:**\n"
                        f"• **{etiqueta}:** `{val_limpio}`\n\n"
                        f"¿Este dato ya está correcto?"
                    )
                    teclado = InlineKeyboardMarkup([
                        [boton('✅ Es correcto', 'reg:conf_paso_ok')],
                        [boton('✏️ Corregir / Escribir', 'reg:conf_paso_corregir'), boton('📸 Tomar foto de nuevo', 'reg:conf_paso_refoto')],
                        [boton('❌ Cancelar', 'reg:cancelar')],
                    ])
                await responder(update, texto_conf, reply_markup=teclado)
                return True

        # CAPTURA DE CURP POR TEXTO SI ESTABA EN ESPERANDO_DOC_CURP
        if estado == EstadoRegistro.ESPERANDO_DOC_CURP:
            if texto.lower() in ('omitir', '/omitir', 'no tengo'):
                await procesar_omision(update, context, sesion)
                return True
            curp_text = texto.strip().upper()
            sesion.datos['curp'] = curp_text
            ok_curp, err_curp = validar_curp_formato(curp_text)
            pref = f"✅ CURP ingresada: `{curp_text}`"
            if not ok_curp:
                pref += f"\n⚠️ {err_curp}"
            await iniciar_confirmacion_critica(
                update, sesion, ['curp'],
                siguiente_estado=EstadoRegistro.ESPERANDO_CSF,
                doc_origen='Documento CURP',
                prefijo=pref
            )
            return True

        # PASO PROCEDENCIA (¿DE DÓNDE VIENES?) POR TEXTO
        if estado == EstadoRegistro.ESPERANDO_PROCEDENCIA:
            sesion.datos['procedencia'] = texto.strip()
            await responder(update, f"✅ Procedencia registrada: {texto.strip()}")
            await mostrar_pantalla_confirmacion(update, sesion)
            return True

        # FOTO DE CANDIDATO OMITIDA POR TEXTO
        if estado == EstadoRegistro.ESPERANDO_FOTO_CANDIDATO:
            if texto.lower() in ('omitir', '/omitir', 'no', 'no tengo'):
                sesion.datos['fotografia_base64'] = None
                await _pedir_encuesta_fuente(update, sesion, prefijo='⏩ Fotografía para expediente omitida.')
                return True
            await responder(
                update,
                "📸 Para la foto del candidato envía una imagen con la cámara de tu celular o selecciona una foto de tu galería, "
                "o pulsa «Omitir foto» si no la tienes en este momento.",
                reply_markup=InlineKeyboardMarkup([
                    [boton('⏩ Omitir foto', 'reg:omitir_foto'), boton('❌ Cancelar', 'reg:cancelar')]
                ])
            )
            return True

        # Si está esperando una foto pero manda texto:
        if estado in (EstadoRegistro.ESPERANDO_ACTA, EstadoRegistro.ESPERANDO_DOMICILIO,
                      EstadoRegistro.ESPERANDO_INE, EstadoRegistro.ESPERANDO_CSF, EstadoRegistro.ESPERANDO_NSS):
            if texto.lower() in ('omitir', '/omitir', 'no tengo'):
                await procesar_omision(update, context, sesion)
                return True
            await responder(
                update,
                '📸 Para este paso envía la foto o PDF del documento correspondiente, '
                'o pulsa «Omitir» si no lo tienes.',
                reply_markup=_teclado_paso_doc()
            )
            return True

        # PASO 2.1: PARENTESCO DE DOMICILIO
        if estado == EstadoRegistro.ESPERANDO_PARENTESCO_DOMICILIO:
            sesion.datos['parentesco_propietario'] = texto
            await _pedir_ine(update, sesion, prefijo=f'✅ Parentesco guardado: {texto}')
            return True

        # PASO 6.1: CONTACTO EMERGENCIA - NOMBRE
        elif estado == EstadoRegistro.ESPERANDO_EMERGENCIA_NOMBRE:
            if texto.lower() in ('omitir', 'no'):
                sesion.datos['nom_eme1'] = ''
                sesion.datos['par_eme1'] = ''
                sesion.datos['tel_eme1'] = ''
                await _pedir_tel_candidato(update, sesion, prefijo='⏩ Contacto de emergencia omitido.')
                return True
            sesion.datos['nom_eme1'] = texto
            sesion.estado = EstadoRegistro.ESPERANDO_EMERGENCIA_PARENTESCO
            teclado = InlineKeyboardMarkup([
                [boton(p, f'reg:par:{p}')] for p in PARENTESCOS_COMUNES
            ] + [[boton('⏩ Omitir parentesco', 'reg:omitir_par_eme'), boton('❌ Cancelar', 'reg:cancelar')]])
            await responder(
                update,
                f"¿Qué **parentesco** tienes con {texto}?\nElige una opción con los botones o escríbelo:",
                reply_markup=teclado
            )
            return True

        # PASO 6.2: CONTACTO EMERGENCIA - PARENTESCO
        elif estado == EstadoRegistro.ESPERANDO_EMERGENCIA_PARENTESCO:
            sesion.datos['par_eme1'] = texto
            sesion.estado = EstadoRegistro.ESPERANDO_EMERGENCIA_TELEFONO
            teclado = InlineKeyboardMarkup([[boton('⏩ Omitir teléfono', 'reg:omitir_tel_eme'), boton('❌ Cancelar', 'reg:cancelar')]])
            await responder(update, f"Escribe el **teléfono de contacto** de emergencia para {sesion.datos.get('nom_eme1')}:", reply_markup=teclado)
            return True

        # PASO 6.3: CONTACTO EMERGENCIA - TELÉFONO
        elif estado == EstadoRegistro.ESPERANDO_EMERGENCIA_TELEFONO:
            sesion.datos['tel_eme1'] = texto
            await _pedir_tel_candidato(update, sesion, prefijo='✅ Contacto de emergencia registrado.')
            return True

        # PASO 6.4: TELÉFONO MÓVIL DEL CANDIDATO
        elif estado == EstadoRegistro.ESPERANDO_TEL_CANDIDATO:
            sesion.datos['tel_movil'] = texto
            await _pedir_correo_candidato(update, sesion, prefijo='✅ Teléfono móvil registrado.')
            return True

        # PASO 6.5: CORREO ELECTRÓNICO DEL CANDIDATO
        elif estado == EstadoRegistro.ESPERANDO_CORREO_CANDIDATO:
            if texto.lower() not in ('omitir', 'no', 'no tengo'):
                sesion.datos['correo'] = texto
                pref = f'✅ Correo guardado: {texto}'
            else:
                sesion.datos['correo'] = ''
                pref = '⏩ Correo electrónico omitido.'
            await _pedir_edo_civil(update, sesion, prefijo=pref)
            return True

        # PASO 6.6: ESTADO CIVIL DEL CANDIDATO
        elif estado == EstadoRegistro.ESPERANDO_EDO_CIVIL:
            if texto.lower() not in ('omitir', 'no'):
                sesion.datos['edo_civil'] = texto.strip().upper()
                pref = f'✅ Estado Civil guardado: {texto}'
            else:
                sesion.datos['edo_civil'] = ''
                pref = '⏩ Estado Civil omitido.'
            await _pedir_nacionalidad(update, sesion, prefijo=pref)
            return True

        # PASO 6.7: OTRA NACIONALIDAD
        elif estado == EstadoRegistro.ESPERANDO_OTRA_NACIONALIDAD:
            nac = texto.strip().upper() or 'MEXICANA'
            sesion.datos['nacionalidad'] = nac
            await _pedir_foto_candidato(update, sesion, prefijo=f'✅ Nacionalidad registrada: {nac}')
            return True

        # PASO 7.1: ENCUESTA FUENTE
        elif estado == EstadoRegistro.ESPERANDO_ENCUESTA_FUENTE:
            if any(cadenas_coinciden(texto, r) for r in sesion.reclutadores):
                await responder(
                    update,
                    f"⚠️ «{texto}» es el nombre de un reclutador, no el medio de reclutamiento.\n"
                    "Por favor selecciona primero por qué medio nos conoció el candidato:",
                    reply_markup=InlineKeyboardMarkup([[boton(f, f'reg:f:{f}')] for f in FUENTES_REGISTRO] + [[boton('❌ Cancelar', 'reg:cancelar')]])
                )
                return True

            sesion.datos['fuente'] = texto
            fuente_norm = normalizar_texto(texto)
            if fuente_norm in ('facebook', 'reclu cw', 'reclutamiento cheong woon', 'cheong woon'):
                await _pedir_encuesta_reclutador(update, sesion, prefijo=f'✅ Medio de reclutamiento: {texto}')
            elif 'recomen' in fuente_norm:
                sesion.estado = EstadoRegistro.ESPERANDO_ENCUESTA_RECOMENDO
                sesion.datos['nombre_reclutador'] = ''
                texto_rec = (
                    f"✅ Medio: **Recomendación**\n\n"
                    f"🤝 Escribe el **Nombre del empleado de la empresa que te recomendó**\n"
                    f"*(o escribe «Omitir» si no lo recuerdas)*:"
                )
                teclado = InlineKeyboardMarkup([[boton('⏩ Omitir recomendador', 'reg:omitir_rec'), boton('❌ Cancelar', 'reg:cancelar')]])
                await responder(update, texto_rec, reply_markup=teclado)
            else:
                sesion.datos['nombre_reclutador'] = ''
                sesion.datos['nombre_recomendador'] = ''
                await verificar_faltantes_o_confirmar(update, sesion)
            return True

        # PASO 7.1.1: ENCUESTA RECOMENDADOR
        elif estado == EstadoRegistro.ESPERANDO_ENCUESTA_RECOMENDO:
            if texto.lower() not in ('omitir', 'no'):
                sesion.datos['nombre_recomendador'] = texto.strip()
                sesion.datos['nombre_empleado_encuesta'] = texto.strip()
                pref = f'✅ Empleado que recomendó: {texto.strip()}'
            else:
                sesion.datos['nombre_recomendador'] = ''
                pref = '⏩ Recomendador omitido.'
            await verificar_faltantes_o_confirmar(update, sesion)
            return True

        # PASO 7.2: ENCUESTA RECLUTADOR
        elif estado == EstadoRegistro.ESPERANDO_ENCUESTA_RECLUTADOR:
            sesion.datos['nombre_reclutador'] = texto
            await verificar_faltantes_o_confirmar(update, sesion)
            return True

        # PASO: SOLICITANDO DATO EN BLANCO FALTANTE
        elif estado == EstadoRegistro.SOLICITANDO_FALTANTE:
            if sesion.faltantes_pendientes:
                campo = sesion.faltantes_pendientes.pop(0)
                if texto.lower() not in ('omitir', 'no'):
                    if campo == 'nombre_completo':
                        sesion.datos['nombre_completo'] = ordenar_nombre_natural(texto)
                    elif campo == 'domicilio':
                        sesion.datos['calle'] = texto
                    elif campo in ('curp', 'rfc'):
                        sesion.datos[campo] = texto.strip().upper()
                    else:
                        sesion.datos[campo] = texto

            if sesion.faltantes_pendientes:
                siguiente = sesion.faltantes_pendientes[0]
                etiqueta = ETIQUETAS_CAMPOS.get(siguiente, siguiente)
                teclado = InlineKeyboardMarkup([[boton('⏩ Omitir este dato', 'reg:omitir_faltante'), boton('❌ Cancelar', 'reg:cancelar')]])
                await responder(update, f"Ahora escribe el dato para **{etiqueta}**:", reply_markup=teclado)
            else:
                await mostrar_pantalla_confirmacion(update, sesion)
            return True

        # PASO: EDITANDO UN CAMPO SELECCIONADO POR EL USUARIO
        elif estado == EstadoRegistro.EDITANDO_CAMPO:
            campo = sesion.campo_editando
            if campo:
                if campo == 'nombre_completo':
                    sesion.datos['nombre_completo'] = ordenar_nombre_natural(texto)
                elif campo == 'domicilio':
                    sesion.datos['calle'] = texto
                elif campo == 'genero':
                    sesion.datos['genero'] = normalizar_sexo(texto)
                    sesion.datos['sexo'] = sesion.datos['genero']
                elif campo == 'emergencia':
                    sesion.datos['nom_eme1'] = texto
                elif campo == 'encuesta':
                    sesion.datos['fuente'] = texto
                elif campo in ('curp', 'rfc'):
                    sesion.datos[campo] = texto.strip().upper()
                else:
                    sesion.datos[campo] = texto
                sesion.campo_editando = None

            await responder(update, f"✅ Dato actualizado correctamente.")
            await mostrar_pantalla_confirmacion(update, sesion)
            return True

        # PASO FINAL: NÚMERO TEMPORAL Y GUARDADO
        elif estado == EstadoRegistro.ESPERANDO_NUMERO_TEMPORAL:
            no_temp = normalizar_numero_temporal(texto)
            sesion.datos['numero_temporal'] = no_temp
            await finalizar_registro(update, context, sesion)
            return True

    except Exception as exc:
        logger.error('Error en atender_texto de registro: %s', exc, exc_info=True)
        await responder(update, '⚠️ Ocurrió un detalle al procesar el texto. Por favor intenta de nuevo o pulsa los botones:')
        return True

    return False


# --- MANEJADOR DE BOTONES INLINE (CALLBACK QUERIES) ---

async def al_boton(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    consulta = update.callback_query
    await consulta.answer()
    user_id = consulta.from_user.id
    sesion = registro_activo.get(user_id)
    if not sesion:
        await consulta.edit_message_text('Este registro ya concluyó o fue cancelado.')
        return

    data = consulta.data or ''
    if data == 'reg:cancelar':
        cancelar_registro(user_id)
        await consulta.edit_message_text('✖️ Registro de candidato cancelado.')
        return

    # Botones específicos de omisión por paso (evitan omitir pasos posteriores por desfasamiento)
    if data == 'reg:omitir_acta':
        if sesion.estado == EstadoRegistro.ESPERANDO_ACTA:
            await _pedir_domicilio(update, sesion, prefijo='⏩ Acta de Nacimiento omitida.')
        return

    if data == 'reg:omitir_domicilio':
        if sesion.estado in (EstadoRegistro.ESPERANDO_DOMICILIO, EstadoRegistro.ESPERANDO_PARENTESCO_DOMICILIO):
            await _pedir_ine(update, sesion, prefijo='⏩ Comprobante de domicilio omitido.')
        return

    if data == 'reg:omitir_ine':
        if sesion.estado == EstadoRegistro.ESPERANDO_INE:
            tiene_curp = bool(sesion.datos.get('curp') or sesion.datos.get('acta', {}).get('curp'))
            siguiente = EstadoRegistro.ESPERANDO_CSF if tiene_curp else EstadoRegistro.ESPERANDO_DOC_CURP
            await ir_al_estado(update, sesion, siguiente, prefijo='⏩ Identificación (INE / Licencia) omitida.')
        return

    if data == 'reg:omitir_curp_doc':
        if sesion.estado == EstadoRegistro.ESPERANDO_DOC_CURP:
            await _pedir_csf(update, sesion, prefijo='⏩ Documento de CURP omitido.')
        return

    if data == 'reg:omitir_csf':
        if sesion.estado == EstadoRegistro.ESPERANDO_CSF:
            await _pedir_nss(update, sesion, prefijo='⏩ Constancia Fiscal omitida.')
        return

    if data == 'reg:omitir_nss':
        if sesion.estado == EstadoRegistro.ESPERANDO_NSS:
            await _pedir_emergencia_nombre(update, sesion, prefijo='⏩ NSS omitido.')
        return

    if data == 'reg:omitir_contacto_eme':
        if sesion.estado == EstadoRegistro.ESPERANDO_EMERGENCIA_NOMBRE:
            sesion.datos['nom_eme1'] = ''
            sesion.datos['par_eme1'] = ''
            sesion.datos['tel_eme1'] = ''
            await _pedir_tel_candidato(update, sesion, prefijo='⏩ Contacto de emergencia omitido.')
        return

    if data == 'reg:omitir_par_eme':
        if sesion.estado == EstadoRegistro.ESPERANDO_EMERGENCIA_PARENTESCO:
            sesion.datos['par_eme1'] = 'No especificado'
            sesion.estado = EstadoRegistro.ESPERANDO_EMERGENCIA_TELEFONO
            teclado = InlineKeyboardMarkup([[boton('⏩ Omitir teléfono', 'reg:omitir_tel_eme'), boton('❌ Cancelar', 'reg:cancelar')]])
            await responder(update, 'Escribe el teléfono de contacto de emergencia:', reply_markup=teclado)
        return

    if data == 'reg:omitir_tel_eme':
        if sesion.estado == EstadoRegistro.ESPERANDO_EMERGENCIA_TELEFONO:
            sesion.datos['tel_eme1'] = ''
            await _pedir_tel_candidato(update, sesion, prefijo='⏩ Teléfono de emergencia omitido.')
        return

    if data == 'reg:omitir_correo':
        if sesion.estado == EstadoRegistro.ESPERANDO_CORREO_CANDIDATO:
            sesion.datos['correo'] = ''
            await _pedir_edo_civil(update, sesion, prefijo='⏩ Correo electrónico omitido.')
        return

    if data == 'reg:omitir_edocivil':
        if sesion.estado == EstadoRegistro.ESPERANDO_EDO_CIVIL:
            sesion.datos['edo_civil'] = ''
            await _pedir_nacionalidad(update, sesion, prefijo='⏩ Estado Civil omitido.')
        return

    if data == 'reg:omitir_rec':
        if sesion.estado == EstadoRegistro.ESPERANDO_ENCUESTA_RECOMENDO:
            sesion.datos['nombre_recomendador'] = ''
            await verificar_faltantes_o_confirmar(update, sesion)
        return

    # Confirmación o retoma de foto de expediente
    if data == 'reg:foto_ok':
        await _pedir_encuesta_fuente(update, sesion, prefijo='✅ Fotografía confirmada para el expediente digital.')
        return

    if data == 'reg:foto_retomar':
        await _pedir_foto_candidato(update, sesion, prefijo='📸 Por favor toma o envía nuevamente la fotografía del candidato:')
        return

    # Omitir foto del candidato
    if data == 'reg:omitir_foto':
        sesion.datos['fotografia_base64'] = None
        await _pedir_encuesta_fuente(update, sesion, prefijo='⏩ Fotografía para expediente omitida.')
        return

    # Confirmación paso a paso de datos críticos
    if data == 'reg:conf_paso_ok':
        if sesion.campo_confirmando:
            sesion.campos_confirmados.add(sesion.campo_confirmando)
        await avanzar_confirmacion_critica(update, sesion)
        return

    if data == 'reg:conf_paso_corregir':
        sesion.estado = EstadoRegistro.ESPERANDO_CORRECCION_PASO
        campo = sesion.campo_confirmando
        etiqueta = ETIQUETAS_CAMPOS.get(campo, campo)
        val_act = sesion.datos.get(campo, '')
        texto = (
            f"✏️ **Corregir {etiqueta}**\n\n"
            f"Valor actual: `{val_act}`\n\n"
            f"Escribe por favor el **valor correcto** por texto:"
        )
        teclado = InlineKeyboardMarkup([[boton('❌ Cancelar', 'reg:cancelar')]])
        await responder(update, texto, reply_markup=teclado)
        return

    if data == 'reg:conf_paso_refoto':
        sesion.estado = EstadoRegistro.ESPERANDO_CORRECCION_PASO
        doc_tipo = sesion.ultimo_doc_para_reintentar or 'el documento'
        texto = (
            f"📸 **Tomar foto de nuevo — {doc_tipo}**\n\n"
            f"Por favor toma o sube una foto más clara y enfocada de {doc_tipo} para releer el dato."
        )
        teclado = InlineKeyboardMarkup([
            [boton('✏️ Prefiero escribirlo', 'reg:conf_paso_corregir')],
            [boton('❌ Cancelar', 'reg:cancelar')],
        ])
        await responder(update, texto, reply_markup=teclado)
        return

    if data == 'reg:omitir':
        await procesar_omision(update, context, sesion)
        return

    if data == 'reg:omitir_faltante':
        if sesion.faltantes_pendientes:
            sesion.faltantes_pendientes.pop(0)
        if sesion.faltantes_pendientes:
            siguiente = sesion.faltantes_pendientes[0]
            etiqueta = ETIQUETAS_CAMPOS.get(siguiente, siguiente)
            teclado = InlineKeyboardMarkup([[boton('⏩ Omitir este dato', 'reg:omitir_faltante'), boton('❌ Cancelar', 'reg:cancelar')]])
            await responder(update, f"Ahora escribe el dato para **{etiqueta}**:", reply_markup=teclado)
        else:
            await mostrar_pantalla_confirmacion(update, sesion)
        return

    if data.startswith('reg:par:'):
        parentesco = data.removeprefix('reg:par:')
        sesion.datos['par_eme1'] = parentesco
        sesion.estado = EstadoRegistro.ESPERANDO_EMERGENCIA_TELEFONO
        teclado = InlineKeyboardMarkup([[boton('⏩ Omitir teléfono', 'reg:omitir'), boton('❌ Cancelar', 'reg:cancelar')]])
        await responder(update, f"Parentesco: {parentesco}\n\nEscribe el **teléfono de contacto** de emergencia para {sesion.datos.get('nom_eme1')}:", reply_markup=teclado)
        return

    if data.startswith('reg:edocivil:'):
        edo = data.removeprefix('reg:edocivil:')
        sesion.datos['edo_civil'] = edo
        await _pedir_nacionalidad(update, sesion, prefijo=f'✅ Estado Civil: {edo}')
        return

    if data == 'reg:nac:MEXICANA':
        sesion.datos['nacionalidad'] = 'MEXICANA'
        await _pedir_foto_candidato(update, sesion, prefijo='✅ Nacionalidad: Mexicana 🇲🇽')
        return

    if data == 'reg:nac:otra':
        sesion.estado = EstadoRegistro.ESPERANDO_OTRA_NACIONALIDAD
        texto = "🌎 Escribe la **Nacionalidad** del candidato por texto:"
        teclado = InlineKeyboardMarkup([[boton('🇲🇽 Dejar como Mexicana', 'reg:nac:MEXICANA'), boton('❌ Cancelar', 'reg:cancelar')]])
        await responder(update, texto, reply_markup=teclado)
        return

    if data.startswith('reg:f:'):
        fuente = data.removeprefix('reg:f:')
        sesion.datos['fuente'] = fuente
        # Facebook y Reclu CW piden reclutador; Recomendación pide quién recomendó
        if fuente in ('Facebook', 'Reclu CW'):
            await _pedir_encuesta_reclutador(update, sesion, prefijo=f'✅ Medio: {fuente}')
        elif fuente == 'Recomendación':
            sesion.estado = EstadoRegistro.ESPERANDO_ENCUESTA_RECOMENDO
            sesion.datos['nombre_reclutador'] = ''
            texto = (
                f"✅ Medio: **Recomendación**\n\n"
                f"🤝 Escribe el **Nombre del empleado de la empresa que te recomendó**\n"
                f"*(o pulsa «Omitir» si no lo recuerdas)*:"
            )
            teclado = InlineKeyboardMarkup([[boton('⏩ Omitir recomendador', 'reg:omitir'), boton('❌ Cancelar', 'reg:cancelar')]])
            await responder(update, texto, reply_markup=teclado)
        else:
            sesion.datos['nombre_reclutador'] = ''
            sesion.datos['nombre_recomendador'] = ''
            await verificar_faltantes_o_confirmar(update, sesion)
        return

    if data.startswith('reg:rec:'):
        rec = data.removeprefix('reg:rec:')
        sesion.datos['nombre_reclutador'] = rec
        await verificar_faltantes_o_confirmar(update, sesion)
        return

    # Confirmación final de datos
    if data == 'reg:confirmar_si':
        sesion.estado = EstadoRegistro.ESPERANDO_NUMERO_TEMPORAL
        texto = (
            "🔢 **Número Temporal de Candidato**\n\n"
            "Ingresa el N° de empleado temporal asignado (ejemplo: `TEMP - 001` o `001`):"
        )
        teclado = InlineKeyboardMarkup([[boton('❌ Cancelar', 'reg:cancelar')]])
        await responder(update, texto, reply_markup=teclado)
        return

    if data == 'reg:editar_menu':
        await mostrar_menu_edicion(update, sesion)
        return

    if data == 'reg:volver_confirmar':
        await mostrar_pantalla_confirmacion(update, sesion)
        return

    if data.startswith('reg:edit:'):
        campo = data.removeprefix('reg:edit:')
        sesion.campo_editando = campo
        if campo == 'procedencia':
            sesion.estado = EstadoRegistro.ESPERANDO_PROCEDENCIA
            val_actual = sesion.datos.get('procedencia') or 'No especificado'
            texto = (
                "📍 **Modificar ¿De dónde vienes? (Procedencia)**\n\n"
                f"Valor actual capturado:\n`{val_actual}`\n\n"
                "Por favor escribe el lugar o entidad de procedencia por texto:"
            )
            teclado = InlineKeyboardMarkup([[boton('⬅️ Volver sin cambiar', 'reg:volver_confirmar')]])
            await responder(update, texto, reply_markup=teclado)
            return

        if campo == 'encuesta':
            await _pedir_encuesta_fuente(update, sesion, prefijo='📊 Modificar medio de reclutamiento:')
            return

        sesion.estado = EstadoRegistro.EDITANDO_CAMPO
        etiqueta = ETIQUETAS_CAMPOS.get(campo, campo)
        val_actual = sesion.datos.get(campo) or 'Vacío'
        if campo == 'domicilio':
            val_actual = f"{sesion.datos.get('calle', '')} {sesion.datos.get('num_ext', '')} {sesion.datos.get('colonia', '')}".strip() or 'Vacío'

        texto = (
            f"✏️ **Modificar {etiqueta}**\n\n"
            f"Valor actual capturado:\n`{val_actual}`\n\n"
            f"Por favor escribe el **nuevo valor correcto** por texto:"
        )
        teclado = InlineKeyboardMarkup([[boton('⬅️ Volver sin cambiar', 'reg:volver_confirmar')]])
        await responder(update, texto, reply_markup=teclado)
        return


async def procesar_omision(update: Update, context: ContextTypes.DEFAULT_TYPE, sesion: SesionRegistro) -> None:
    estado = sesion.estado
    if estado == EstadoRegistro.ESPERANDO_ACTA:
        await _pedir_domicilio(update, sesion, prefijo='⏩ Acta de Nacimiento omitida.')
    elif estado in (EstadoRegistro.ESPERANDO_DOMICILIO, EstadoRegistro.ESPERANDO_PARENTESCO_DOMICILIO):
        await _pedir_ine(update, sesion, prefijo='⏩ Comprobante de domicilio omitido.')
    elif estado == EstadoRegistro.ESPERANDO_INE:
        # Si no hay CURP, solicitar Documento CURP; si ya hay, pasar a CSF
        tiene_curp = bool(sesion.datos.get('curp') or sesion.datos.get('acta', {}).get('curp'))
        siguiente = EstadoRegistro.ESPERANDO_CSF if tiene_curp else EstadoRegistro.ESPERANDO_DOC_CURP
        await ir_al_estado(update, sesion, siguiente, prefijo='⏩ Identificación (INE / Licencia) omitida.')
    elif estado == EstadoRegistro.ESPERANDO_DOC_CURP:
        await _pedir_csf(update, sesion, prefijo='⏩ Documento de CURP omitido.')
    elif estado == EstadoRegistro.ESPERANDO_CSF:
        await _pedir_nss(update, sesion, prefijo='⏩ Constancia Fiscal omitida.')
    elif estado == EstadoRegistro.ESPERANDO_NSS:
        await _pedir_emergencia_nombre(update, sesion, prefijo='⏩ NSS omitido.')
    elif estado == EstadoRegistro.ESPERANDO_EMERGENCIA_NOMBRE:
        sesion.datos['nom_eme1'] = ''
        sesion.datos['par_eme1'] = ''
        sesion.datos['tel_eme1'] = ''
        await _pedir_tel_candidato(update, sesion, prefijo='⏩ Contacto de emergencia omitido.')
    elif estado == EstadoRegistro.ESPERANDO_EMERGENCIA_PARENTESCO:
        sesion.datos['par_eme1'] = 'No especificado'
        sesion.estado = EstadoRegistro.ESPERANDO_EMERGENCIA_TELEFONO
        teclado = InlineKeyboardMarkup([[boton('⏩ Omitir teléfono', 'reg:omitir_tel_eme'), boton('❌ Cancelar', 'reg:cancelar')]])
        await responder(update, 'Escribe el teléfono de contacto de emergencia:', reply_markup=teclado)
    elif estado == EstadoRegistro.ESPERANDO_EMERGENCIA_TELEFONO:
        sesion.datos['tel_eme1'] = ''
        await _pedir_tel_candidato(update, sesion, prefijo='⏩ Teléfono de emergencia omitido.')
    elif estado == EstadoRegistro.ESPERANDO_CORREO_CANDIDATO:
        sesion.datos['correo'] = ''
        await _pedir_edo_civil(update, sesion, prefijo='⏩ Correo electrónico omitido.')
    elif estado == EstadoRegistro.ESPERANDO_EDO_CIVIL:
        sesion.datos['edo_civil'] = ''
        await _pedir_nacionalidad(update, sesion, prefijo='⏩ Estado Civil omitido.')
    elif estado == EstadoRegistro.ESPERANDO_ENCUESTA_RECLUTADOR:
        sesion.datos['nombre_reclutador'] = ''
        await verificar_faltantes_o_confirmar(update, sesion)


# --- GUARDADO FINAL ---

async def finalizar_registro(update: Update, context: ContextTypes.DEFAULT_TYPE, sesion: SesionRegistro) -> None:
    user_id = sesion.user_id
    backend: ClienteBackend = context.application.bot_data['backend']
    d = sesion.datos

    nombre = d.get('nombre_completo') or 'Candidato sin nombre'
    curp = d.get('curp') or ''
    rfc = d.get('rfc') or ''
    nss = d.get('nss') or ''
    fecha_nac = d.get('fecha_nac') or ''
    edad = calcular_edad(fecha_nac)
    genero = d.get('genero') or d.get('sexo') or ''
    procedencia = d.get('procedencia') or ''
    nacionalidad = d.get('nacionalidad') or 'MEXICANA'

    calle = d.get('calle') or ''
    num_ext = d.get('num_ext') or ''
    num_int = d.get('num_int') or ''
    colonia = d.get('colonia') or ''
    municipio = d.get('municipio') or ''
    estado_dir = d.get('estado') or ''
    cp = d.get('cp') or ''

    tel_movil = d.get('tel_movil') or ''
    correo = d.get('correo') or ''
    edo_civil = d.get('edo_civil') or ''
    nom_eme = d.get('nom_eme1') or ''
    par_eme = d.get('par_eme1') or ''
    tel_eme = d.get('tel_eme1') or ''
    fotografia_b64 = d.get('fotografia_base64') or None

    no_temp = d.get('numero_temporal') or 'TEMP - 001'

    formulario = {
        'numero_temporal': no_temp,
        'nombre_completo': nombre,
        'curp': curp,
        'rfc': rfc,
        'nss': nss,
        'fecha_nac': fecha_nac,
        'edad': edad,
        'genero': genero,
        'edo_civil': edo_civil,
        'nacionalidad': nacionalidad,
        'procedencia': procedencia,
        'calle': calle,
        'num_ext': num_ext,
        'num_int': num_int,
        'colonia': colonia,
        'municipio': municipio,
        'estado': estado_dir,
        'cp': cp,
        'tel_movil': tel_movil,
        'correo': correo,
        'nom_eme1': nom_eme,
        'par_eme1': par_eme,
        'tel_eme1': tel_eme,
    }
    if fotografia_b64:
        formulario['fotografia_base64'] = fotografia_b64

    await responder(update, '⏳ Guardando expediente completo del candidato en el sistema…')

    try:
        # Guardar en registro
        await backend.peticion(user_id, 'POST', '/api/guardar-registro', data=formulario)

        # Guardar encuesta si se capturó
        fuente = d.get('fuente')
        if fuente:
            sub_fuente = 'CW' if fuente == 'Reclu CW' else ('PURO' if fuente == 'Reclu puro' else '')
            recomendo = d.get('nombre_recomendador') or ''
            encuesta = {
                'curp': curp,
                'fuente': fuente,
                'sub_fuente': sub_fuente,
                'nombre_reclutador': d.get('nombre_reclutador') or '',
                'nombre_empleado': recomendo if 'recomen' in fuente.lower() else nombre,
                'nombre_recomendador': recomendo,
            }
            try:
                await backend.peticion(user_id, 'POST', '/api/guardar-encuesta', data=encuesta)
            except Exception as e_enc:
                logger.warning('No se pudo guardar la encuesta: %s', e_enc)

        # Limpiar la sesión activa
        cancelar_registro(user_id)

        det_encuesta = f"{fuente or 'Sin dato'}"
        if 'recomen' in str(fuente).lower() and d.get('nombre_recomendador'):
            det_encuesta += f" | Recomendado por: {d.get('nombre_recomendador')}"
        elif d.get('nombre_reclutador'):
            det_encuesta += f" | Reclutador: {d.get('nombre_reclutador')}"

        resumen_final = (
            "🎉 **¡Registro de Candidato completado con éxito!**\n\n"
            f"• **N° Temporal:** `{no_temp}`\n"
            f"• **Nombre:** {nombre}\n"
            f"• **CURP:** `{curp or 'Sin dato'}`\n"
            f"• **RFC:** `{rfc or 'Sin dato'}`\n"
            f"• **NSS:** `{nss or 'Sin dato'}`\n"
            f"• **Fecha nac.:** {fecha_nac or 'Sin dato'} ({edad + ' años' if edad else 's/d'})\n"
            f"• **Sexo:** {genero or 'Sin dato'}\n"
            f"• **Nacionalidad:** {nacionalidad}\n"
            f"• **¿De dónde vienes? (Procedencia):** {procedencia or 'Sin dato'}\n"
            f"• **Dirección:** {calle} {num_ext}, Col. {colonia}, CP {cp}, {municipio}, {estado_dir}\n"
            f"• **Teléfono:** {tel_movil or 'Sin dato'}\n"
            f"• **Correo:** {correo or 'Sin dato'}\n"
            f"• **Estado Civil:** {edo_civil or 'Sin dato'}\n"
            f"• **Contacto de emergencia:** {nom_eme or 'Sin dato'} ({par_eme}) - {tel_eme}\n"
            f"• **Fotografía expediente:** {'Adjunta ✅' if fotografia_b64 else 'No capturada'}\n"
            f"• **Encuesta:** {det_encuesta}\n\n"
            "El expediente ha quedado registrado y disponible en el panel administrativo."
        )
        await responder(update, resumen_final)

    except Exception as exc:
        logger.error('Error al guardar registro de candidato: %s', exc)
        await responder(
            update,
            f"⚠️ Ocurrió un error al guardar el registro en el sistema: {exc}\n"
            "Tus datos siguen guardados en memoria. Intenta enviar nuevamente el número temporal o escribe /cancelar para descartar."
        )
