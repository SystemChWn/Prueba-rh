"""Máquina de estados estricta para el flujo de «Generar Registro de Candidato».

Aísla el proceso de captura y validación documental por user_id.
Incluye:
- Verificación interactiva inmediata por documento (Nombre, CURP, RFC, NSS).
- Opción de confirmación inmediata, retoma de foto o corrección manual antes de continuar.
- Solicitud de Documento CURP si no se encuentra en documentos anteriores.
- Pregunta directa sobre Nacionalidad (Mexicana / Otra).
- Captura obligatoria de Fotografía del Empleado para su expediente digital antes de la encuesta.
- Condicional estricto para Encuesta: reclutadores solo para Facebook y Reclu CW.
- Mapeo correcto de sexo en INE (M = Mujer/Femenino, H = Hombre/Masculino).
- Normalización de nombres iniciando por el nombre de pila.
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
    CONFIRMANDO_DATO_ACTA = 'confirmando_dato_acta'
    EDITANDO_DATO_ACTA = 'editando_dato_acta'

    ESPERANDO_DOMICILIO = 'esperando_domicilio'
    ESPERANDO_PARENTESCO_DOMICILIO = 'esperando_parentesco_domicilio'

    ESPERANDO_INE = 'esperando_ine'
    CONFIRMANDO_DATO_INE = 'confirmando_dato_ine'
    EDITANDO_DATO_INE = 'editando_dato_ine'

    ESPERANDO_DOC_CURP = 'esperando_doc_curp'
    CONFIRMANDO_DATO_CURP = 'confirmando_dato_curp'
    EDITANDO_DATO_CURP = 'editando_dato_curp'

    ESPERANDO_CSF = 'esperando_csf'
    CONFIRMANDO_DATO_CSF = 'confirmando_dato_csf'
    EDITANDO_DATO_CSF = 'editando_dato_csf'

    ESPERANDO_NSS = 'esperando_nss'
    CONFIRMANDO_DATO_NSS = 'confirmando_dato_nss'
    EDITANDO_DATO_NSS = 'editando_dato_nss'

    ESPERANDO_EMERGENCIA_NOMBRE = 'esperando_emergencia_nombre'
    ESPERANDO_EMERGENCIA_PARENTESCO = 'esperando_emergencia_parentesco'
    ESPERANDO_EMERGENCIA_TELEFONO = 'esperando_emergencia_telefono'
    ESPERANDO_TEL_CANDIDATO = 'esperando_tel_candidato'
    ESPERANDO_CORREO_CANDIDATO = 'esperando_correo_candidato'
    ESPERANDO_EDO_CIVIL = 'esperando_edo_civil'
    ESPERANDO_NACIONALIDAD = 'esperando_nacionalidad'
    ESPERANDO_FOTO_EMPLEADO = 'esperando_foto_empleado'

    ESPERANDO_ENCUESTA_FUENTE = 'esperando_encuesta_fuente'
    ESPERANDO_ENCUESTA_RECLUTADOR = 'esperando_encuesta_reclutador'
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


def _teclado_paso_doc() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [boton('⏩ Omitir documento', 'reg:omitir'), boton('❌ Cancelar', 'reg:cancelar')]
    ])


def _teclado_confirmacion_inmediata(prefijo_cb: str) -> InlineKeyboardMarkup:
    """
    Teclado para confirmar inmediatamente el dato extraído:
    - ✅ Correcto
    - 📸 Enviar foto de nuevo
    - ✏️ Escribir dato
    """
    return InlineKeyboardMarkup([
        [boton('✅ Correcto', f'reg:conf_inm:{prefijo_cb}:si')],
        [boton('📸 Enviar foto de nuevo', f'reg:conf_inm:{prefijo_cb}:retomar'), boton('✏️ Escribir dato', f'reg:conf_inm:{prefijo_cb}:escribir')],
        [boton('❌ Cancelar', 'reg:cancelar')],
    ])


def _corregir_orientacion(contenido: bytes, mime: str) -> bytes:
    if mime not in ('image/jpeg', 'image/png', 'image/webp'):
        return contenido
    try:
        from PIL import Image, ImageOps
        img = Image.open(BytesIO(contenido))
        img = ImageOps.exif_transpose(img)
        buf = BytesIO()
        fmt = 'JPEG' if mime == 'image/jpeg' else ('PNG' if mime == 'image/png' else 'WEBP')
        img.save(buf, format=fmt)
        return buf.getvalue()
    except Exception as exc:
        logger.warning('No se pudo corregir orientación EXIF en bot: %s', exc)
        return contenido


async def _escanear_en_backend(context: ContextTypes.DEFAULT_TYPE, user_id: int, tipo_doc: str, archivo: Archivo) -> dict[str, Any]:
    backend: ClienteBackend = context.application.bot_data['backend']
    contenido_corregido = _corregir_orientacion(archivo.contenido, archivo.mime)
    archivo_envio = (archivo.nombre, contenido_corregido, archivo.mime)

    respuesta = await backend.peticion(
        user_id, 'POST', '/api/escanear-documento', timeout=75,
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

    fuentes_norm = {normalizar_texto(f) for f in FUENTES_REGISTRO}
    reclutadores_limpios = [r for r in reclutadores if normalizar_texto(r) not in fuentes_norm]

    sesion = SesionRegistro(user_id=user_id, estado=EstadoRegistro.ESPERANDO_ACTA, reclutadores=reclutadores_limpios)
    registro_activo[user_id] = sesion

    texto = (
        "✍️ **Generar Registro de Candidato**\n\n"
        "**Paso 1 — Acta de Nacimiento**\n"
        "📸 Por favor envía la foto o PDF del **Acta de Nacimiento** mexicana.\n\n"
        "Extraeremos y verificaremos contigo:\n"
        "• Nombre completo (iniciando por nombre)\n"
        "• CURP\n"
        "• Sexo\n"
        "• Fecha de nacimiento\n"
        "• Lugar de nacimiento\n\n"
        "*(La foto puede estar en cualquier orientación. Usa «Omitir» si no la tienes o «Cancelar» para salir)*"
    )
    await responder(update, texto, reply_markup=_teclado_paso_doc())


async def _pedir_domicilio(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_DOMICILIO
    texto = (
        f"{prefijo}\n\n"
        "**Paso 2 — Comprobante de Domicilio**\n"
        "📸 Envía la foto o PDF del **Comprobante de Domicilio** (CFE, agua, teléfono, etc.).\n\n"
        "Extraeremos el titular del recibo y la dirección completa."
    )
    await responder(update, texto, reply_markup=_teclado_paso_doc())


async def _pedir_ine(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_INE
    texto = (
        f"{prefijo}\n\n"
        "**Paso 3 — Credencial de Elector (INE / IFE)**\n"
        "📸 Envía la foto o PDF de la **INE** del candidato.\n\n"
        "Se capturarán y validarán:\n"
        "• Nombre completo\n"
        "• Sexo (M = Mujer, H = Hombre)\n"
        "• Domicilio oficial\n"
        "• CURP\n"
        "• Fecha de nacimiento"
    )
    await responder(update, texto, reply_markup=_teclado_paso_doc())


async def _pedir_doc_curp(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_DOC_CURP
    texto = (
        f"{prefijo}\n\n"
        "⚠️ **Documento CURP requerido**\n"
        "No logramos identificar la CURP en los documentos anteriores.\n"
        "📸 Por favor envía una foto o PDF de la **Constancia de CURP oficial**:"
    )
    await responder(update, texto, reply_markup=_teclado_paso_doc())


async def _pedir_csf(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_CSF
    texto = (
        f"{prefijo}\n\n"
        "**Paso 4 — Constancia de Situación Fiscal (SAT)**\n"
        "📸 Envía la foto o PDF de la **Constancia Fiscal (CSF)**.\n\n"
        "Se capturarán RFC, CURP y Nombre del contribuyente."
    )
    await responder(update, texto, reply_markup=_teclado_paso_doc())


async def _pedir_nss(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_NSS
    texto = (
        f"{prefijo}\n\n"
        "**Paso 5 — Número de Seguro Social (IMSS)**\n"
        "📸 Envía la foto o PDF del documento con el **NSS**.\n\n"
        "Se capturarán NSS (11 dígitos), Nombre y CURP."
    )
    await responder(update, texto, reply_markup=_teclado_paso_doc())


async def _pedir_emergencia_nombre(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_EMERGENCIA_NOMBRE
    texto = (
        f"{prefijo}\n\n"
        "**Contacto de Emergencia**\n"
        "🚨 Escribe el **Nombre completo** de la persona para contactar en caso de emergencia:"
    )
    teclado = InlineKeyboardMarkup([[boton('⏩ Omitir contacto', 'reg:omitir'), boton('❌ Cancelar', 'reg:cancelar')]])
    await responder(update, texto, reply_markup=teclado)


async def _pedir_tel_candidato(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_TEL_CANDIDATO
    texto = (
        f"{prefijo}\n\n"
        "📱 Escribe el **Teléfono móvil** del candidato (10 dígitos):"
    )
    teclado = InlineKeyboardMarkup([[boton('❌ Cancelar', 'reg:cancelar')]])
    await responder(update, texto, reply_markup=teclado)


async def _pedir_correo_candidato(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_CORREO_CANDIDATO
    texto = (
        f"{prefijo}\n\n"
        "📧 Escribe el **Correo electrónico** del candidato (o pulsa «Omitir»):"
    )
    teclado = InlineKeyboardMarkup([[boton('⏩ Omitir correo', 'reg:omitir'), boton('❌ Cancelar', 'reg:cancelar')]])
    await responder(update, texto, reply_markup=teclado)


async def _pedir_edo_civil(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_EDO_CIVIL
    texto = (
        f"{prefijo}\n\n"
        "💍 Selecciona el **Estado Civil** del candidato:\n"
        "Elige una opción con los botones o escríbelo:"
    )
    filas = [[boton(opc, f'reg:edocivil:{opc}')] for opc in ESTADOS_CIVILES]
    filas.append([boton('⏩ Omitir estado civil', 'reg:omitir'), boton('❌ Cancelar', 'reg:cancelar')])
    await responder(update, texto, reply_markup=InlineKeyboardMarkup(filas))


async def _pedir_nacionalidad(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_NACIONALIDAD
    texto = (
        f"{prefijo}\n\n"
        "🌍 ¿Cuál es la **Nacionalidad** del candidato?\n"
        "Selecciona «Mexicana» o escribe otra nacionalidad:"
    )
    teclado = InlineKeyboardMarkup([
        [boton('🇲🇽 Mexicana', 'reg:nac:Mexicana')],
        [boton('✍️ Otra nacionalidad', 'reg:nac:otra'), boton('❌ Cancelar', 'reg:cancelar')],
    ])
    await responder(update, texto, reply_markup=teclado)


async def _pedir_foto_empleado(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_FOTO_EMPLEADO
    texto = (
        f"{prefijo}\n\n"
        "📸 **Fotografía del Empleado (Expediente Digital)**\n"
        "Por favor toma o envía una **fotografía formal o de frente** del candidato para su credencial y expediente digital:\n"
        "*(Debe ser una foto del rostro del empleado)*"
    )
    teclado = InlineKeyboardMarkup([
        [boton('⏩ Omitir fotografía', 'reg:omitir'), boton('❌ Cancelar', 'reg:cancelar')]
    ])
    await responder(update, texto, reply_markup=teclado)


async def _pedir_encuesta_fuente(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_ENCUESTA_FUENTE
    texto = (
        f"{prefijo}\n\n"
        "📊 **Encuesta de Reclutamiento**\n"
        "¿Por qué medio nos conoció el candidato?\n"
        "Elige una opción con los botones o escribe el medio:"
    )
    filas = [[boton(f, f'reg:f:{f}')] for f in FUENTES_REGISTRO]
    filas.append([boton('❌ Cancelar', 'reg:cancelar')])
    await responder(update, texto, reply_markup=InlineKeyboardMarkup(filas))


async def _pedir_encuesta_reclutador(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_ENCUESTA_RECLUTADOR
    texto = (
        f"{prefijo}\n\n"
        "👤 ¿Quién es el **Reclutador** asignado?\n"
        "Elige de la lista o escribe el nombre del reclutador:"
    )
    filas = [[boton(r, f'reg:rec:{r[:25]}')] for r in sesion.reclutadores[:8]]
    filas.append([boton('⏩ Sin reclutador / Omitir', 'reg:omitir'), boton('❌ Cancelar', 'reg:cancelar')])
    await responder(update, texto, reply_markup=InlineKeyboardMarkup(filas))


# --- RESOLUCIÓN Y VALIDACIÓN DE DATOS ---

def ejecutar_validacion_consenso(sesion: SesionRegistro) -> tuple[dict[str, str], list[str]]:
    """
    Consolida los mejores datos entre los documentos si no fueron establecidos manualmente.
    """
    d = sesion.datos

    # 1. CURP
    if not d.get('curp'):
        curps = [
            d.get('curp_doc', {}).get('curp'),
            d.get('acta', {}).get('curp'),
            d.get('ine', {}).get('curp'),
            d.get('csf', {}).get('curp'),
            d.get('nss_doc', {}).get('curp'),
        ]
        curp_win, _, _ = resolver_por_mayoria(curps)
        if curp_win:
            d['curp'] = curp_win

    # 2. NOMBRE COMPLETO
    if not d.get('nombre_completo'):
        nom_acta = d.get('acta', {}).get('nombre_completo')
        nom_pila = d.get('acta', {}).get('nombre', '')
        ap_pat = d.get('acta', {}).get('primer_apellido', '')
        ap_mat = d.get('acta', {}).get('segundo_apellido', '')

        nom_ine = ordenar_nombre_natural(d.get('ine', {}).get('nombre_completo', ''), nom_pila, ap_pat, ap_mat)
        nom_csf = ordenar_nombre_natural(d.get('csf', {}).get('nombre_completo', ''), nom_pila, ap_pat, ap_mat)
        nom_nss = ordenar_nombre_natural(d.get('nss_doc', {}).get('nombre_completo', ''), nom_pila, ap_pat, ap_mat)
        nom_curp = ordenar_nombre_natural(d.get('curp_doc', {}).get('nombre_completo', ''), nom_pila, ap_pat, ap_mat)
        nom_acta_nat = ordenar_nombre_natural(nom_acta, nom_pila, ap_pat, ap_mat) if nom_acta else ''

        nombres = [nom_acta_nat, nom_ine, nom_csf, nom_nss, nom_curp]
        nom_win, _, _ = resolver_por_mayoria(nombres)
        if nom_win:
            d['nombre_completo'] = nom_win

    # 3. SEXO
    if not d.get('genero') and not d.get('sexo'):
        sex_acta = d.get('acta', {}).get('sexo')
        sex_ine = d.get('ine', {}).get('sexo')
        sex_curp = d.get('curp_doc', {}).get('genero') or d.get('curp_doc', {}).get('sexo')
        sex_win, _, _ = resolver_por_mayoria([sex_acta, sex_ine, sex_curp])
        if sex_win:
            d['genero'] = sex_win
            d['sexo'] = sex_win

    # 4. FECHA DE NACIMIENTO
    if not d.get('fecha_nac'):
        fn_acta = d.get('acta', {}).get('fecha_nac')
        fn_ine = d.get('ine', {}).get('fecha_nac')
        fn_curp = d.get('curp_doc', {}).get('fecha_nac')
        fn_win, _, _ = resolver_por_mayoria([fn_acta, fn_ine, fn_curp])
        if fn_win:
            d['fecha_nac'] = fn_win

    # 5. RFC
    if not d.get('rfc'):
        rfc_csf = d.get('csf', {}).get('rfc')
        if rfc_csf:
            d['rfc'] = rfc_csf

    # 6. NSS
    if not d.get('nss'):
        nss_doc = d.get('nss_doc', {}).get('nss')
        if nss_doc:
            d['nss'] = nss_doc

    return d, []


def obtener_campos_en_blanco(sesion: SesionRegistro) -> list[str]:
    """Identifica qué datos esenciales siguen vacíos."""
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
    if not d.get('nacionalidad'):
        faltantes.append('nacionalidad')
    if not d.get('calle') and not d.get('colonia') and not d.get('cp'):
        faltantes.append('domicilio')
    return faltantes


ETIQUETAS_CAMPOS = {
    'nombre_completo': 'Nombre completo',
    'curp': 'CURP',
    'rfc': 'RFC',
    'nss': 'NSS',
    'fecha_nac': 'Fecha de nacimiento (AAAA-MM-DD)',
    'domicilio': 'Dirección (Calle, Número, Colonia, CP, Municipio, Estado)',
    'genero': 'Sexo / Género (MASCULINO / FEMENINO)',
    'nacionalidad': 'Nacionalidad',
    'procedencia': 'Lugar de nacimiento',
    'tel_movil': 'Teléfono móvil',
    'correo': 'Correo electrónico',
    'edo_civil': 'Estado civil',
    'emergencia': 'Contacto de emergencia (Nombre, Parentesco, Teléfono)',
    'encuesta': 'Medio de reclutamiento y Reclutador',
}


async def verificar_faltantes_o_confirmar(update: Update, sesion: SesionRegistro) -> None:
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
    d, _ = ejecutar_validacion_consenso(sesion)

    nombre = d.get('nombre_completo') or 'Sin dato'
    curp = d.get('curp') or 'Sin dato'
    rfc = d.get('rfc') or 'Sin dato'
    nss = d.get('nss') or 'Sin dato'
    fecha_nac = d.get('fecha_nac') or 'Sin dato'
    edad = calcular_edad(fecha_nac)
    edad_str = f" ({edad} años)" if edad else ""
    sexo = d.get('genero') or d.get('sexo') or 'Sin dato'
    nacionalidad = d.get('nacionalidad') or 'Mexicana'
    lugar = d.get('procedencia') or 'Sin dato'

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
    tiene_foto = '✅ Capturada' if d.get('fotografia_base64') else '❌ No proporcionada'

    fuente = d.get('fuente') or 'Sin dato'
    rec = d.get('nombre_reclutador') or ('Sin asignar' if fuente in ('Facebook', 'Reclu CW') else 'N/A')

    lineas_resumen = [
        "📋 **Resumen de Registro de Candidato:**",
        "",
        f"• **Nombre:** {nombre}",
        f"• **CURP:** `{curp}`",
        f"• **RFC:** `{rfc}`",
        f"• **NSS:** `{nss}`",
        f"• **Fecha nac.:** {fecha_nac}{edad_str}",
        f"• **Sexo:** {sexo}",
        f"• **Nacionalidad:** {nacionalidad}",
        f"• **Lugar nac.:** {lugar}",
        f"• **Dirección:** {dir_str}",
        f"• **Teléfono candidato:** {tel}",
        f"• **Correo:** {correo}",
        f"• **Estado Civil:** {edo_civil}",
        f"• **Fotografía de perfil:** {tiene_foto}",
        f"• **Contacto de emergencia:** {nom_eme} ({par_eme}) - {tel_eme}",
        f"• **Medio de reclutamiento:** {fuente}",
        f"• **Reclutador asignado:** {rec}",
        "",
        "¿Todos estos datos son correctos?",
    ]

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
        [boton('Nacionalidad', 'reg:edit:nacionalidad'), boton('Lugar de nacimiento', 'reg:edit:procedencia')],
        [boton('Dirección completa', 'reg:edit:domicilio'), boton('Teléfono candidato', 'reg:edit:tel_movil')],
        [boton('Correo electrónico', 'reg:edit:correo'), boton('Estado Civil', 'reg:edit:edo_civil')],
        [boton('Contacto emergencia', 'reg:edit:emergencia'), boton('Encuesta / Reclutador', 'reg:edit:encuesta')],
        [boton('⬅️ Volver a confirmar', 'reg:volver_confirmar')],
    ]
    await responder(update, texto, reply_markup=InlineKeyboardMarkup(filas))


# --- MANEJADOR DE ARCHIVOS (OCR / FOTO) ---

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

    # PASO: FOTOGRAFÍA DEL EMPLEADO (para expediente digital)
    if estado == EstadoRegistro.ESPERANDO_FOTO_EMPLEADO:
        if mime == 'application/pdf':
            await responder(update, '⚠️ Por favor envía una foto en formato imagen (JPG/PNG), no un archivo PDF.')
            return True
        contenido_orientado = _corregir_orientacion(contenido, mime)
        foto_b64 = f"data:{mime};base64,{base64.b64encode(contenido_orientado).decode('ascii')}"
        sesion.datos['fotografia_base64'] = foto_b64
        await _pedir_encuesta_fuente(update, sesion, prefijo='✅ Fotografía del empleado capturada y guardada.')
        return True

    # PASO 1: ACTA DE NACIMIENTO
    elif estado == EstadoRegistro.ESPERANDO_ACTA:
        await responder(update, '🔍 Analizando Acta de Nacimiento con visión artificial…')
        try:
            leidos = await _escanear_en_backend(context, user_id, 'Acta de Nacimiento', archivo)
            if not leidos or not any(leidos.values()):
                raise ValueError('No se pudieron detectar campos legibles en el Acta.')
        except Exception as exc:
            logger.warning('Falla en escaneo de Acta: %s', exc)
            await responder(
                update,
                '⚠️ No pude leer con claridad el Acta de Nacimiento.\n'
                'Por favor envía una foto más clara, derecha y enfocada, o pulsa «Omitir» para continuar.',
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

        sesion.datos['acta'] = {
            'curp': curp,
            'nombre': nom_pila,
            'primer_apellido': ap_pat,
            'segundo_apellido': ap_mat,
            'nombre_completo': nombre_nat,
            'sexo': sexo,
            'fecha_nac': fecha_nac,
            'lugar_nacimiento': lugar_nac,
        }
        sesion.datos['nombre_completo'] = nombre_nat
        sesion.datos['curp'] = curp
        sesion.datos['sexo'] = sexo
        sesion.datos['genero'] = sexo
        sesion.datos['fecha_nac'] = fecha_nac
        sesion.datos['procedencia'] = lugar_nac

        sesion.estado = EstadoRegistro.CONFIRMANDO_DATO_ACTA
        texto = (
            "📄 **Datos extraídos del Acta de Nacimiento:**\n\n"
            f"• **Nombre:** {nombre_nat or 'No detectado'}\n"
            f"• **CURP:** `{curp or 'No detectado'}`\n"
            f"• **Sexo:** {sexo or 'No detectado'}\n"
            f"• **Fecha nac.:** {fecha_nac or 'No detectado'}\n"
            f"• **Lugar nac.:** {lugar_nac or 'No detectado'}\n\n"
            "¿Estos datos son correctos?"
        )
        await responder(update, texto, reply_markup=_teclado_confirmacion_inmediata('acta'))
        return True

    # PASO 2: COMPROBANTE DE DOMICILIO
    elif estado == EstadoRegistro.ESPERANDO_DOMICILIO:
        await responder(update, '🔍 Analizando Comprobante de Domicilio…')
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
                reply_markup=_teclado_paso_doc(),
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
            teclado = InlineKeyboardMarkup([[boton('⏩ Omitir parentesco', 'reg:omitir'), boton('❌ Cancelar', 'reg:cancelar')]])
            await responder(update, resumen, reply_markup=teclado)
        else:
            sesion.datos['parentesco_propietario'] = 'No especificado'
            resumen = (
                "✅ **Comprobante de Domicilio analizado:**\n"
                f"• **Dirección:** {dir_resumen}"
            )
            await _pedir_ine(update, sesion, prefijo=resumen)
        return True

    # PASO 3: INE
    elif estado == EstadoRegistro.ESPERANDO_INE:
        await responder(update, '🔍 Analizando Credencial de Elector (INE)…')
        try:
            leidos = await _escanear_en_backend(context, user_id, 'INE', archivo)
            if not leidos or not any(leidos.values()):
                raise ValueError('No se pudieron detectar campos legibles en la INE.')
        except Exception as exc:
            logger.warning('Falla en escaneo de INE: %s', exc)
            await responder(
                update,
                '⚠️ No pude leer la INE. Envía una foto más clara o pulsa «Omitir».',
                reply_markup=_teclado_paso_doc(),
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

        sesion.datos['ine'] = {'nombre_completo': ine_nom, 'curp': ine_curp, 'sexo': ine_sexo, 'fecha_nac': ine_fn, 'domicilio': ine_dom}
        if ine_nom and not sesion.datos.get('nombre_completo'):
            sesion.datos['nombre_completo'] = ine_nom
        if ine_curp and not sesion.datos.get('curp'):
            sesion.datos['curp'] = ine_curp
        if ine_sexo and not sesion.datos.get('sexo'):
            sesion.datos['sexo'] = ine_sexo
            sesion.datos['genero'] = ine_sexo

        sesion.estado = EstadoRegistro.CONFIRMANDO_DATO_INE
        texto = (
            "🪪 **Datos extraídos de la INE:**\n\n"
            f"• **Nombre:** {ine_nom or 'No detectado'}\n"
            f"• **CURP:** `{ine_curp or 'No detectado'}`\n"
            f"• **Sexo:** {ine_sexo or 'No detectado'}\n"
            f"• **Fecha nac.:** {ine_fn or 'No detectado'}\n"
            f"• **Domicilio INE:** {ine_dom or 'No detectado'}\n\n"
            "¿Estos datos son correctos?"
        )
        await responder(update, texto, reply_markup=_teclado_confirmacion_inmediata('ine'))
        return True

    # PASO FALLBACK: DOCUMENTO CURP
    elif estado == EstadoRegistro.ESPERANDO_DOC_CURP:
        await responder(update, '🔍 Analizando Documento de CURP…')
        try:
            leidos = await _escanear_en_backend(context, user_id, 'CURP', archivo)
            if not leidos or not any(leidos.values()):
                raise ValueError('No se detectaron campos en el Documento de CURP.')
        except Exception as exc:
            logger.warning('Falla en escaneo de CURP: %s', exc)
            await responder(
                update,
                '⚠️ No pude leer el Documento de CURP. Envía una imagen más clara o pulsa «Omitir».',
                reply_markup=_teclado_paso_doc(),
            )
            return True

        curp_val = str(leidos.get('curp') or '').strip().upper()
        nom_curp = ordenar_nombre_natural(str(leidos.get('nombre_completo') or '').strip())
        fn_curp = str(leidos.get('fecha_nac') or '').strip()
        sex_curp = normalizar_sexo(leidos.get('genero') or leidos.get('sexo'))

        sesion.datos['curp_doc'] = {'curp': curp_val, 'nombre_completo': nom_curp, 'fecha_nac': fn_curp, 'genero': sex_curp}
        if curp_val:
            sesion.datos['curp'] = curp_val
        if nom_curp and not sesion.datos.get('nombre_completo'):
            sesion.datos['nombre_completo'] = nom_curp

        sesion.estado = EstadoRegistro.CONFIRMANDO_DATO_CURP
        texto = (
            "📄 **Datos extraídos de la Constancia CURP:**\n\n"
            f"• **CURP:** `{curp_val or 'No detectada'}`\n"
            f"• **Nombre:** {nom_curp or 'No detectado'}\n\n"
            "¿El dato de CURP es correcto?"
        )
        await responder(update, texto, reply_markup=_teclado_confirmacion_inmediata('curp'))
        return True

    # PASO 4: CSF
    elif estado == EstadoRegistro.ESPERANDO_CSF:
        await responder(update, '🔍 Analizando Constancia de Situación Fiscal (SAT)…')
        try:
            leidos = await _escanear_en_backend(context, user_id, 'Constancia de Situación Fiscal', archivo)
            if not leidos or not any(leidos.values()):
                raise ValueError('No se pudieron detectar campos legibles en la CSF.')
        except Exception as exc:
            logger.warning('Falla en escaneo de CSF: %s', exc)
            await responder(
                update,
                '⚠️ No pude leer la Constancia Fiscal. Envía una foto más legible o pulsa «Omitir».',
                reply_markup=_teclado_paso_doc(),
            )
            return True

        rfc = str(leidos.get('rfc') or '').strip().upper()
        csf_curp = str(leidos.get('curp') or '').strip().upper()
        csf_nom = ordenar_nombre_natural(str(leidos.get('nombre_completo') or '').strip())

        sesion.datos['csf'] = {'rfc': rfc, 'curp': csf_curp, 'nombre_completo': csf_nom}
        if rfc:
            sesion.datos['rfc'] = rfc
        if csf_curp and not sesion.datos.get('curp'):
            sesion.datos['curp'] = csf_curp
        if csf_nom and not sesion.datos.get('nombre_completo'):
            sesion.datos['nombre_completo'] = csf_nom

        sesion.estado = EstadoRegistro.CONFIRMANDO_DATO_CSF
        texto = (
            "🧾 **Datos extraídos de la Constancia Fiscal (SAT):**\n\n"
            f"• **RFC:** `{rfc or 'No detectado'}`\n"
            f"• **CURP:** `{csf_curp or 'No detectado'}`\n"
            f"• **Nombre:** {csf_nom or 'No detectado'}\n\n"
            "¿Estos datos son correctos?"
        )
        await responder(update, texto, reply_markup=_teclado_confirmacion_inmediata('csf'))
        return True

    # PASO 5: NSS
    elif estado == EstadoRegistro.ESPERANDO_NSS:
        await responder(update, '🔍 Analizando Número de Seguro Social (IMSS)…')
        try:
            leidos = await _escanear_en_backend(context, user_id, 'Número de Seguro Social', archivo)
            if not leidos or not any(leidos.values()):
                raise ValueError('No se pudieron detectar campos legibles en el documento de NSS.')
        except Exception as exc:
            logger.warning('Falla en escaneo de NSS: %s', exc)
            await responder(
                update,
                '⚠️ No pude leer el NSS. Envía una foto más clara o pulsa «Omitir».',
                reply_markup=_teclado_paso_doc(),
            )
            return True

        nss = str(leidos.get('nss') or '').strip()
        nss_nom = ordenar_nombre_natural(str(leidos.get('nombre_completo') or '').strip())
        nss_curp = str(leidos.get('curp') or '').strip().upper()

        sesion.datos['nss_doc'] = {'nss': nss, 'nombre_completo': nss_nom, 'curp': nss_curp}
        if nss:
            sesion.datos['nss'] = nss
        if nss_nom and not sesion.datos.get('nombre_completo'):
            sesion.datos['nombre_completo'] = nss_nom
        if nss_curp and not sesion.datos.get('curp'):
            sesion.datos['curp'] = nss_curp

        sesion.estado = EstadoRegistro.CONFIRMANDO_DATO_NSS
        texto = (
            "🏥 **Datos extraídos del IMSS:**\n\n"
            f"• **NSS:** `{nss or 'No detectado'}`\n"
            f"• **Nombre:** {nss_nom or 'No detectado'}\n\n"
            "¿El Número de Seguro Social es correcto?"
        )
        await responder(update, texto, reply_markup=_teclado_confirmacion_inmediata('nss'))
        return True

    else:
        await responder(
            update,
            'En este paso se espera una respuesta en texto, no una imagen. '
            'Por favor escribe tu respuesta o presiona «Cancelar» para descartar.'
        )
        return True


# --- MANEJADOR DE TEXTO LIBRE ---

async def atender_texto(update: Update, context: ContextTypes.DEFAULT_TYPE, texto_libre: str | None = None) -> bool:
    user_id = update.effective_user.id
    sesion = registro_activo.get(user_id)
    if not sesion:
        return False

    texto = (texto_libre if texto_libre is not None else update.effective_message.text or '').strip()

    if texto.lower() in ('/cancelar', 'cancelar', 'salir'):
        cancelar_registro(user_id)
        await responder(update, '✖️ Registro de candidato cancelado.')
        return True

    estado = sesion.estado

    # Si está esperando una foto pero manda texto:
    if estado in (EstadoRegistro.ESPERANDO_ACTA, EstadoRegistro.ESPERANDO_DOMICILIO,
                  EstadoRegistro.ESPERANDO_INE, EstadoRegistro.ESPERANDO_DOC_CURP,
                  EstadoRegistro.ESPERANDO_CSF, EstadoRegistro.ESPERANDO_NSS,
                  EstadoRegistro.ESPERANDO_FOTO_EMPLEADO):
        if texto.lower() in ('omitir', '/omitir', 'no tengo'):
            await procesar_omision(update, context, sesion)
            return True
        await responder(
            update,
            '📸 Para este paso envía la foto o imagen correspondiente, '
            'o pulsa «Omitir» si no la tienes.',
            reply_markup=_teclado_paso_doc()
        )
        return True

    # EDICIÓN DIRECTA EN PASOS DE CONFIRMACIÓN INMEDIATA:
    if estado == EstadoRegistro.EDITANDO_DATO_ACTA:
        sesion.datos['nombre_completo'] = ordenar_nombre_natural(texto)
        sesion.estado = EstadoRegistro.CONFIRMANDO_DATO_ACTA
        await responder(
            update,
            f"✅ Nombre actualizado a: **{sesion.datos['nombre_completo']}**.\n¿Deseas continuar?",
            reply_markup=_teclado_confirmacion_inmediata('acta')
        )
        return True

    elif estado == EstadoRegistro.EDITANDO_DATO_INE:
        sesion.datos['curp'] = texto.strip().upper()
        sesion.estado = EstadoRegistro.CONFIRMANDO_DATO_INE
        await responder(
            update,
            f"✅ CURP actualizada a: `{sesion.datos['curp']}`.\n¿Deseas continuar?",
            reply_markup=_teclado_confirmacion_inmediata('ine')
        )
        return True

    elif estado == EstadoRegistro.EDITANDO_DATO_CURP:
        sesion.datos['curp'] = texto.strip().upper()
        sesion.estado = EstadoRegistro.CONFIRMANDO_DATO_CURP
        await responder(
            update,
            f"✅ CURP actualizada a: `{sesion.datos['curp']}`.\n¿Deseas continuar?",
            reply_markup=_teclado_confirmacion_inmediata('curp')
        )
        return True

    elif estado == EstadoRegistro.EDITANDO_DATO_CSF:
        sesion.datos['rfc'] = texto.strip().upper()
        sesion.estado = EstadoRegistro.CONFIRMANDO_DATO_CSF
        await responder(
            update,
            f"✅ RFC actualizado a: `{sesion.datos['rfc']}`.\n¿Deseas continuar?",
            reply_markup=_teclado_confirmacion_inmediata('csf')
        )
        return True

    elif estado == EstadoRegistro.EDITANDO_DATO_NSS:
        sesion.datos['nss'] = texto.strip()
        sesion.estado = EstadoRegistro.CONFIRMANDO_DATO_NSS
        await responder(
            update,
            f"✅ NSS actualizado a: `{sesion.datos['nss']}`.\n¿Deseas continuar?",
            reply_markup=_teclado_confirmacion_inmediata('nss')
        )
        return True

    # PASO 2.1: PARENTESCO DE DOMICILIO
    elif estado == EstadoRegistro.ESPERANDO_PARENTESCO_DOMICILIO:
        sesion.datos['parentesco_propietario'] = texto
        await _pedir_ine(update, sesion, prefijo=f'✅ Parentesco guardado: {texto}')
        return True

    # CONTACTO EMERGENCIA - NOMBRE
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
        ] + [[boton('⏩ Omitir parentesco', 'reg:omitir'), boton('❌ Cancelar', 'reg:cancelar')]])
        await responder(
            update,
            f"¿Qué **parentesco** tienes con {texto}?\nElige una opción con los botones o escríbelo:",
            reply_markup=teclado
        )
        return True

    # CONTACTO EMERGENCIA - PARENTESCO
    elif estado == EstadoRegistro.ESPERANDO_EMERGENCIA_PARENTESCO:
        sesion.datos['par_eme1'] = texto
        sesion.estado = EstadoRegistro.ESPERANDO_EMERGENCIA_TELEFONO
        teclado = InlineKeyboardMarkup([[boton('⏩ Omitir teléfono', 'reg:omitir'), boton('❌ Cancelar', 'reg:cancelar')]])
        await responder(update, f"Escribe el **teléfono de contacto** de emergencia para {sesion.datos.get('nom_eme1')}:", reply_markup=teclado)
        return True

    # CONTACTO EMERGENCIA - TELÉFONO
    elif estado == EstadoRegistro.ESPERANDO_EMERGENCIA_TELEFONO:
        sesion.datos['tel_eme1'] = texto
        await _pedir_tel_candidato(update, sesion, prefijo='✅ Contacto de emergencia registrado.')
        return True

    # TELÉFONO MÓVIL DEL CANDIDATO
    elif estado == EstadoRegistro.ESPERANDO_TEL_CANDIDATO:
        sesion.datos['tel_movil'] = texto
        await _pedir_correo_candidato(update, sesion, prefijo='✅ Teléfono móvil registrado.')
        return True

    # CORREO ELECTRÓNICO DEL CANDIDATO
    elif estado == EstadoRegistro.ESPERANDO_CORREO_CANDIDATO:
        if texto.lower() not in ('omitir', 'no', 'no tengo'):
            sesion.datos['correo'] = texto
            pref = f'✅ Correo guardado: {texto}'
        else:
            sesion.datos['correo'] = ''
            pref = '⏩ Correo electrónico omitido.'
        await _pedir_edo_civil(update, sesion, prefijo=pref)
        return True

    # ESTADO CIVIL DEL CANDIDATO
    elif estado == EstadoRegistro.ESPERANDO_EDO_CIVIL:
        if texto.lower() not in ('omitir', 'no'):
            sesion.datos['edo_civil'] = texto.strip().upper()
            pref = f'✅ Estado Civil guardado: {texto}'
        else:
            sesion.datos['edo_civil'] = ''
            pref = '⏩ Estado Civil omitido.'
        await _pedir_nacionalidad(update, sesion, prefijo=pref)
        return True

    # NACIONALIDAD
    elif estado == EstadoRegistro.ESPERANDO_NACIONALIDAD:
        sesion.datos['nacionalidad'] = texto.strip()
        await _pedir_foto_empleado(update, sesion, prefijo=f'✅ Nacionalidad: {sesion.datos["nacionalidad"]}')
        return True

    # ENCUESTA FUENTE
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
        if texto in ('Facebook', 'Reclu CW'):
            await _pedir_encuesta_reclutador(update, sesion, prefijo=f'✅ Medio: {texto}')
        else:
            sesion.datos['nombre_reclutador'] = ''
            await verificar_faltantes_o_confirmar(update, sesion)
        return True

    # ENCUESTA RECLUTADOR
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

    # EDITANDO UN CAMPO SELECCIONADO POR EL USUARIO
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

    # RESPUESTAS A CONFIRMACIONES INMEDIATAS (acta, ine, curp, csf, nss)
    if data.startswith('reg:conf_inm:'):
        partes = data.split(':')
        doc = partes[2] if len(partes) > 2 else ''
        accion = partes[3] if len(partes) > 3 else ''

        if doc == 'acta':
            if accion == 'si':
                await _pedir_domicilio(update, sesion, prefijo='✅ Datos de Acta confirmados.')
            elif accion == 'retomar':
                sesion.estado = EstadoRegistro.ESPERANDO_ACTA
                await responder(update, '📸 Envía nuevamente la foto del **Acta de Nacimiento**:', reply_markup=_teclado_paso_doc())
            elif accion == 'escribir':
                sesion.estado = EstadoRegistro.EDITANDO_DATO_ACTA
                await responder(update, f"Escribe el **Nombre completo** del candidato (actual: `{sesion.datos.get('nombre_completo')}`):")

        elif doc == 'ine':
            if accion == 'si':
                # Si aún no tenemos CURP tras INE, solicitar Documento CURP
                if not sesion.datos.get('curp'):
                    await _pedir_doc_curp(update, sesion, prefijo='✅ INE confirmada.')
                else:
                    await _pedir_csf(update, sesion, prefijo='✅ INE confirmada.')
            elif accion == 'retomar':
                sesion.estado = EstadoRegistro.ESPERANDO_INE
                await responder(update, '📸 Envía nuevamente la foto de la **INE**:', reply_markup=_teclado_paso_doc())
            elif accion == 'escribir':
                sesion.estado = EstadoRegistro.EDITANDO_DATO_INE
                await responder(update, f"Escribe la **CURP** del candidato (actual: `{sesion.datos.get('curp')}`):")

        elif doc == 'curp':
            if accion == 'si':
                await _pedir_csf(update, sesion, prefijo='✅ CURP confirmada.')
            elif accion == 'retomar':
                sesion.estado = EstadoRegistro.ESPERANDO_DOC_CURP
                await responder(update, '📸 Envía nuevamente la foto del **Documento de CURP**:', reply_markup=_teclado_paso_doc())
            elif accion == 'escribir':
                sesion.estado = EstadoRegistro.EDITANDO_DATO_CURP
                await responder(update, f"Escribe la **CURP** del candidato:")

        elif doc == 'csf':
            if accion == 'si':
                await _pedir_nss(update, sesion, prefijo='✅ Constancia Fiscal confirmada.')
            elif accion == 'retomar':
                sesion.estado = EstadoRegistro.ESPERANDO_CSF
                await responder(update, '📸 Envía nuevamente la foto de la **Constancia Fiscal**:', reply_markup=_teclado_paso_doc())
            elif accion == 'escribir':
                sesion.estado = EstadoRegistro.EDITANDO_DATO_CSF
                await responder(update, f"Escribe el **RFC** del candidato (actual: `{sesion.datos.get('rfc')}`):")

        elif doc == 'nss':
            if accion == 'si':
                await _pedir_emergencia_nombre(update, sesion, prefijo='✅ NSS confirmado.')
            elif accion == 'retomar':
                sesion.estado = EstadoRegistro.ESPERANDO_NSS
                await responder(update, '📸 Envía nuevamente la foto del **NSS**:', reply_markup=_teclado_paso_doc())
            elif accion == 'escribir':
                sesion.estado = EstadoRegistro.EDITANDO_DATO_NSS
                await responder(update, f"Escribe el **NSS** del candidato (11 dígitos, actual: `{sesion.datos.get('nss')}`):")
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

    if data.startswith('reg:nac:'):
        nac = data.removeprefix('reg:nac:')
        if nac == 'otra':
            sesion.estado = EstadoRegistro.ESPERANDO_NACIONALIDAD
            await responder(update, 'Escribe la **nacionalidad** del candidato:')
        else:
            sesion.datos['nacionalidad'] = 'Mexicana'
            await _pedir_foto_empleado(update, sesion, prefijo='✅ Nacionalidad: Mexicana')
        return

    if data.startswith('reg:f:'):
        fuente = data.removeprefix('reg:f:')
        sesion.datos['fuente'] = fuente
        if fuente in ('Facebook', 'Reclu CW'):
            await _pedir_encuesta_reclutador(update, sesion, prefijo=f'✅ Medio: {fuente}')
        else:
            sesion.datos['nombre_reclutador'] = ''
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
            "🔢 **Paso Final — Número Temporal de Candidato**\n\n"
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
    if estado in (EstadoRegistro.ESPERANDO_ACTA, EstadoRegistro.CONFIRMANDO_DATO_ACTA):
        await _pedir_domicilio(update, sesion, prefijo='⏩ Acta de Nacimiento omitida.')
    elif estado in (EstadoRegistro.ESPERANDO_DOMICILIO, EstadoRegistro.ESPERANDO_PARENTESCO_DOMICILIO):
        await _pedir_ine(update, sesion, prefijo='⏩ Comprobante de domicilio omitido.')
    elif estado in (EstadoRegistro.ESPERANDO_INE, EstadoRegistro.CONFIRMANDO_DATO_INE):
        if not sesion.datos.get('curp'):
            await _pedir_doc_curp(update, sesion, prefijo='⏩ INE omitida.')
        else:
            await _pedir_csf(update, sesion, prefijo='⏩ INE omitida.')
    elif estado in (EstadoRegistro.ESPERANDO_DOC_CURP, EstadoRegistro.CONFIRMANDO_DATO_CURP):
        await _pedir_csf(update, sesion, prefijo='⏩ Documento CURP omitido.')
    elif estado in (EstadoRegistro.ESPERANDO_CSF, EstadoRegistro.CONFIRMANDO_DATO_CSF):
        await _pedir_nss(update, sesion, prefijo='⏩ Constancia Fiscal omitida.')
    elif estado in (EstadoRegistro.ESPERANDO_NSS, EstadoRegistro.CONFIRMANDO_DATO_NSS):
        await _pedir_emergencia_nombre(update, sesion, prefijo='⏩ NSS omitido.')
    elif estado == EstadoRegistro.ESPERANDO_EMERGENCIA_NOMBRE:
        sesion.datos['nom_eme1'] = ''
        sesion.datos['par_eme1'] = ''
        sesion.datos['tel_eme1'] = ''
        await _pedir_tel_candidato(update, sesion, prefijo='⏩ Contacto de emergencia omitido.')
    elif estado == EstadoRegistro.ESPERANDO_EMERGENCIA_PARENTESCO:
        sesion.datos['par_eme1'] = 'No especificado'
        sesion.estado = EstadoRegistro.ESPERANDO_EMERGENCIA_TELEFONO
        teclado = InlineKeyboardMarkup([[boton('⏩ Omitir teléfono', 'reg:omitir'), boton('❌ Cancelar', 'reg:cancelar')]])
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
    elif estado == EstadoRegistro.ESPERANDO_NACIONALIDAD:
        sesion.datos['nacionalidad'] = 'Mexicana'
        await _pedir_foto_empleado(update, sesion, prefijo='⏩ Nacionalidad asignada como Mexicana.')
    elif estado == EstadoRegistro.ESPERANDO_FOTO_EMPLEADO:
        sesion.datos['fotografia_base64'] = None
        await _pedir_encuesta_fuente(update, sesion, prefijo='⏩ Fotografía omitida.')
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
    nacionalidad = d.get('nacionalidad') or 'Mexicana'
    procedencia = d.get('procedencia') or ''

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
    foto_b64 = d.get('fotografia_base64')

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
    if foto_b64:
        formulario['fotografia_base64'] = foto_b64

    await responder(update, '⏳ Guardando expediente completo del candidato en el sistema…')

    try:
        # Guardar en registro
        await backend.peticion(user_id, 'POST', '/api/guardar-registro', data=formulario)

        # Guardar encuesta si se capturó
        fuente = d.get('fuente')
        if fuente:
            sub_fuente = 'CW' if fuente == 'Reclu CW' else ('PURO' if fuente == 'Reclu puro' else '')
            encuesta = {
                'curp': curp,
                'fuente': fuente,
                'sub_fuente': sub_fuente,
                'nombre_reclutador': d.get('nombre_reclutador') or '',
                'nombre_empleado': nombre,
            }
            try:
                await backend.peticion(user_id, 'POST', '/api/guardar-encuesta', data=encuesta)
            except Exception as e_enc:
                logger.warning('No se pudo guardar la encuesta: %s', e_enc)

        # Limpiar la sesión activa
        cancelar_registro(user_id)

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
            f"• **Procedencia:** {procedencia or 'Sin dato'}\n"
            f"• **Dirección:** {calle} {num_ext}, Col. {colonia}, CP {cp}, {municipio}, {estado_dir}\n"
            f"• **Teléfono:** {tel_movil or 'Sin dato'}\n"
            f"• **Correo:** {correo or 'Sin dato'}\n"
            f"• **Estado Civil:** {edo_civil or 'Sin dato'}\n"
            f"• **Fotografía:** {'Registrada en expediente' if foto_b64 else 'Sin foto'}\n"
            f"• **Contacto de emergencia:** {nom_eme or 'Sin dato'} ({par_eme}) - {tel_eme}\n"
            f"• **Encuesta:** {fuente or 'Sin dato'} | Reclutador: {d.get('nombre_reclutador') or 'Sin asignar'}\n\n"
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
