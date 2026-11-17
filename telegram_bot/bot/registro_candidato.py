"""Máquina de estados estricta para el flujo de «Generar Registro de Candidato».

Aísla el proceso de captura y validación documental (7 pasos) por user_id.
Mientras un usuario tenga un registro activo en este módulo, NINGÚN mensaje
ni archivo se delega al chat general.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from io import BytesIO
import logging
import re
from typing import Any
import unicodedata

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.backend import ClienteBackend, ErrorBackend
from bot.borrador import Archivo
from bot.interfaz import boton, responder

logger = logging.getLogger(__name__)

FUENTES_REGISTRO = ['Facebook', 'Posteo', 'Volante', 'Recomendación', 'Reclu CW', 'Reclu puro']
PARENTESCOS_COMUNES = ['Madre', 'Padre', 'Cónyuge / Pareja', 'Hijo / Hija', 'Hermano / Hermana', 'Familiar', 'Amigo / Conocido']


class EstadoRegistro(str, Enum):
    ESPERANDO_ACTA = 'esperando_acta'
    ESPERANDO_DOMICILIO = 'esperando_domicilio'
    ESPERANDO_PARENTESCO_DOMICILIO = 'esperando_parentesco_domicilio'
    ESPERANDO_INE = 'esperando_ine'
    ESPERANDO_CSF = 'esperando_csf'
    ESPERANDO_NSS = 'esperando_nss'
    ESPERANDO_EMERGENCIA_NOMBRE = 'esperando_emergencia_nombre'
    ESPERANDO_EMERGENCIA_PARENTESCO = 'esperando_emergencia_parentesco'
    ESPERANDO_EMERGENCIA_TELEFONO = 'esperando_emergencia_telefono'
    ESPERANDO_TEL_CANDIDATO = 'esperando_tel_candidato'
    ESPERANDO_ENCUESTA_FUENTE = 'esperando_encuesta_fuente'
    ESPERANDO_ENCUESTA_RECLUTADOR = 'esperando_encuesta_reclutador'
    ESPERANDO_NUMERO_TEMPORAL = 'esperando_numero_temporal'


@dataclass
class SesionRegistro:
    user_id: int
    estado: EstadoRegistro = EstadoRegistro.ESPERANDO_ACTA
    datos: dict[str, Any] = field(default_factory=dict)
    reclutadores: list[str] = field(default_factory=list)


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
        [boton('⏩ Omitir', 'reg:omitir'), boton('❌ Cancelar', 'reg:cancelar')]
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
            items = datos.get('reclutadores') or []
            reclutadores = [r['nombre'] for r in items if isinstance(r, dict) and r.get('nombre')]
        except Exception as e:
            logger.warning('No fue posible precargar reclutadores: %s', e)

    sesion = SesionRegistro(user_id=user_id, estado=EstadoRegistro.ESPERANDO_ACTA, reclutadores=reclutadores)
    registro_activo[user_id] = sesion

    texto = (
        "✍️ **Generar Registro de Candidato**\n\n"
        "**Paso 1 de 7 — Acta de Nacimiento**\n"
        "📸 Por favor envía la foto o PDF del **Acta de Nacimiento** mexicana.\n\n"
        "Extraeremos automáticamente:\n"
        "• CURP\n"
        "• Nombre completo (titular)\n"
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
        "**Paso 2 de 7 — Comprobante de Domicilio**\n"
        "📸 Envía la foto o PDF del **Comprobante de Domicilio** (CFE, agua, teléfono, etc.).\n\n"
        "Extraeremos el titular del recibo y la dirección completa."
    )
    await responder(update, texto, reply_markup=_teclado_paso_doc())


async def _pedir_ine(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_INE
    texto = (
        f"{prefijo}\n\n"
        "**Paso 3 de 7 — Credencial de Elector (INE / IFE)**\n"
        "📸 Envía la foto o PDF de la **INE** del candidato.\n\n"
        "Se capturarán y validarán:\n"
        "• Nombre completo\n"
        "• Sexo\n"
        "• Domicilio\n"
        "• CURP\n"
        "• Fecha de nacimiento"
    )
    await responder(update, texto, reply_markup=_teclado_paso_doc())


async def _pedir_csf(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_CSF
    texto = (
        f"{prefijo}\n\n"
        "**Paso 4 de 7 — Constancia de Situación Fiscal (SAT)**\n"
        "📸 Envía la foto o PDF de la **Constancia Fiscal**.\n\n"
        "Se capturarán RFC, CURP y Nombre del contribuyente."
    )
    await responder(update, texto, reply_markup=_teclado_paso_doc())


async def _pedir_nss(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_NSS
    texto = (
        f"{prefijo}\n\n"
        "**Paso 5 de 7 — Número de Seguro Social (IMSS)**\n"
        "📸 Envía la foto o PDF del documento con el **NSS**.\n\n"
        "Se capturarán NSS (11 dígitos), Nombre y CURP."
    )
    await responder(update, texto, reply_markup=_teclado_paso_doc())


async def _pedir_emergencia_nombre(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_EMERGENCIA_NOMBRE
    texto = (
        f"{prefijo}\n\n"
        "**Paso 6 de 7 — Contacto de Emergencia**\n"
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


async def _pedir_encuesta_fuente(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_ENCUESTA_FUENTE
    texto = (
        f"{prefijo}\n\n"
        "**Paso 7 de 7 — Encuesta de Reclutamiento**\n"
        "📊 ¿Por qué medio nos conoció el candidato?\n"
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
        "Elige de la lista o escribe el nombre:"
    )
    filas = [[boton(r, f'reg:rec:{r[:25]}')] for r in sesion.reclutadores[:8]]
    filas.append([boton('⏩ Sin reclutador / Omitir', 'reg:omitir'), boton('❌ Cancelar', 'reg:cancelar')])
    await responder(update, texto, reply_markup=InlineKeyboardMarkup(filas))


async def _pedir_numero_temporal(update: Update, sesion: SesionRegistro, prefijo: str = '') -> None:
    sesion.estado = EstadoRegistro.ESPERANDO_NUMERO_TEMPORAL
    texto = (
        f"{prefijo}\n\n"
        "🔢 **Número Temporal de Candidato**\n"
        "Ingresa el N° de empleado temporal asignado (ejemplo: `TEMP - 001` o `001`):"
    )
    teclado = InlineKeyboardMarkup([[boton('❌ Cancelar', 'reg:cancelar')]])
    await responder(update, texto, reply_markup=teclado)


# --- MANEJADOR DE ARCHIVOS (OCR) ---

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

    # PASO 1: ACTA DE NACIMIENTO
    if estado == EstadoRegistro.ESPERANDO_ACTA:
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

        # Consolidar datos de Acta como base de la verdad
        curp = str(leidos.get('curp') or '').strip().upper()
        nombre = str(leidos.get('nombre_completo') or leidos.get('nombre') or '').strip()
        sexo = str(leidos.get('genero') or leidos.get('sexo') or '').strip().upper()
        fecha_nac = str(leidos.get('fecha_nac') or leidos.get('fecha_nacimiento') or '').strip()
        lugar_nac = str(leidos.get('lugar_nacimiento') or '').strip()

        sesion.datos['acta'] = {
            'curp': curp,
            'nombre_completo': nombre,
            'sexo': sexo,
            'fecha_nac': fecha_nac,
            'lugar_nacimiento': lugar_nac,
        }
        sesion.datos['nombre_completo'] = nombre
        sesion.datos['curp'] = curp
        sesion.datos['sexo'] = sexo
        sesion.datos['fecha_nac'] = fecha_nac
        sesion.datos['procedencia'] = lugar_nac

        resumen = (
            "✅ **Acta de Nacimiento analizada con éxito:**\n"
            f"• **Nombre:** {nombre or 'No legible'}\n"
            f"• **CURP:** {curp or 'No legible'}\n"
            f"• **Sexo:** {sexo or 'No legible'}\n"
            f"• **Fecha nac.:** {fecha_nac or 'No legible'}\n"
            f"• **Lugar nac.:** {lugar_nac or 'No legible'}"
        )
        await _pedir_domicilio(update, sesion, prefijo=resumen)
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

        ine_nom = str(leidos.get('nombre_completo') or '').strip()
        ine_curp = str(leidos.get('curp') or '').strip().upper()
        ine_sexo = str(leidos.get('genero') or leidos.get('sexo') or '').strip().upper()
        ine_fn = str(leidos.get('fecha_nac') or leidos.get('fecha_nacimiento') or '').strip()
        ine_dom = str(leidos.get('domicilio') or '').strip()

        sesion.datos['ine'] = {'nombre_completo': ine_nom, 'curp': ine_curp, 'sexo': ine_sexo, 'fecha_nac': ine_fn, 'domicilio': ine_dom}
        if not sesion.datos.get('nombre_completo') and ine_nom:
            sesion.datos['nombre_completo'] = ine_nom
        if not sesion.datos.get('curp') and ine_curp:
            sesion.datos['curp'] = ine_curp

        # Validación cruzada informativa
        lineas = ['✅ **INE analizada con éxito:**']
        acta_nom = sesion.datos.get('nombre_completo')
        if ine_nom:
            tag = ' (Coincide con Acta ✅)' if acta_nom and cadenas_coinciden(acta_nom, ine_nom) else (f' (⚠️ Difiere de Acta: {acta_nom})' if acta_nom else '')
            lineas.append(f'• **Nombre:** {ine_nom}{tag}')

        acta_c = sesion.datos.get('curp')
        if ine_curp:
            tag = ' (Coincide con Acta ✅)' if acta_c and acta_c == ine_curp else (f' (⚠️ Difiere de Acta: {acta_c})' if acta_c else '')
            lineas.append(f'• **CURP:** {ine_curp}{tag}')

        if ine_sexo:
            lineas.append(f'• **Sexo:** {ine_sexo}')
        if ine_fn:
            lineas.append(f'• **Fecha nac.:** {ine_fn}')
        if ine_dom:
            lineas.append(f'• **Domicilio INE:** {ine_dom}')

        await _pedir_csf(update, sesion, prefijo='\n'.join(lineas))
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
        csf_nom = str(leidos.get('nombre_completo') or '').strip()

        sesion.datos['csf'] = {'rfc': rfc, 'curp': csf_curp, 'nombre_completo': csf_nom}
        sesion.datos['rfc'] = rfc

        lineas = ['✅ **Constancia de Situación Fiscal analizada:**']
        if rfc:
            lineas.append(f'• **RFC:** {rfc}')
        base_curp = sesion.datos.get('curp')
        if csf_curp:
            tag = ' (Coincide con Acta/INE ✅)' if base_curp and base_curp == csf_curp else (f' (⚠️ Difiere: {base_curp})' if base_curp else '')
            lineas.append(f'• **CURP:** {csf_curp}{tag}')
        base_nom = sesion.datos.get('nombre_completo')
        if csf_nom:
            tag = ' (Coincide con registros ✅)' if base_nom and cadenas_coinciden(base_nom, csf_nom) else (f' (⚠️ Difiere: {base_nom})' if base_nom else '')
            lineas.append(f'• **Nombre:** {csf_nom}{tag}')

        await _pedir_nss(update, sesion, prefijo='\n'.join(lineas))
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
        nss_nom = str(leidos.get('nombre_completo') or '').strip()
        nss_curp = str(leidos.get('curp') or '').strip().upper()

        sesion.datos['nss_doc'] = {'nss': nss, 'nombre_completo': nss_nom, 'curp': nss_curp}
        sesion.datos['nss'] = nss

        lineas = ['✅ **Número de Seguro Social analizado:**']
        if nss:
            lineas.append(f'• **NSS:** {nss}')
        base_nom = sesion.datos.get('nombre_completo')
        if nss_nom:
            tag = ' (Coincide con registros ✅)' if base_nom and cadenas_coinciden(base_nom, nss_nom) else ''
            lineas.append(f'• **Nombre:** {nss_nom}{tag}')

        await _pedir_emergencia_nombre(update, sesion, prefijo='\n'.join(lineas))
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
        ] + [[boton('⏩ Omitir parentesco', 'reg:omitir'), boton('❌ Cancelar', 'reg:cancelar')]])
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
        teclado = InlineKeyboardMarkup([[boton('⏩ Omitir teléfono', 'reg:omitir'), boton('❌ Cancelar', 'reg:cancelar')]])
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
        await _pedir_encuesta_fuente(update, sesion, prefijo='✅ Teléfono del candidato registrado.')
        return True

    # PASO 7.1: ENCUESTA FUENTE
    elif estado == EstadoRegistro.ESPERANDO_ENCUESTA_FUENTE:
        sesion.datos['fuente'] = texto
        await _pedir_encuesta_reclutador(update, sesion, prefijo=f'✅ Fuente guardada: {texto}')
        return True

    # PASO 7.2: ENCUESTA RECLUTADOR
    elif estado == EstadoRegistro.ESPERANDO_ENCUESTA_RECLUTADOR:
        sesion.datos['nombre_reclutador'] = texto
        await _pedir_numero_temporal(update, sesion, prefijo=f'✅ Reclutador guardado: {texto}')
        return True

    # PASO 7.3: NÚMERO TEMPORAL Y FINALIZACIÓN
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

    if data.startswith('reg:par:'):
        parentesco = data.removeprefix('reg:par:')
        sesion.datos['par_eme1'] = parentesco
        sesion.estado = EstadoRegistro.ESPERANDO_EMERGENCIA_TELEFONO
        teclado = InlineKeyboardMarkup([[boton('⏩ Omitir teléfono', 'reg:omitir'), boton('❌ Cancelar', 'reg:cancelar')]])
        await responder(update, f"Parentesco: {parentesco}\n\nEscribe el **teléfono de contacto** de emergencia para {sesion.datos.get('nom_eme1')}:", reply_markup=teclado)
        return

    if data.startswith('reg:f:'):
        fuente = data.removeprefix('reg:f:')
        sesion.datos['fuente'] = fuente
        await _pedir_encuesta_reclutador(update, sesion, prefijo=f'✅ Medio: {fuente}')
        return

    if data.startswith('reg:rec:'):
        rec = data.removeprefix('reg:rec:')
        sesion.datos['nombre_reclutador'] = rec
        await _pedir_numero_temporal(update, sesion, prefijo=f'✅ Reclutador: {rec}')
        return


async def procesar_omision(update: Update, context: ContextTypes.DEFAULT_TYPE, sesion: SesionRegistro) -> None:
    estado = sesion.estado
    if estado == EstadoRegistro.ESPERANDO_ACTA:
        await _pedir_domicilio(update, sesion, prefijo='⏩ Acta de Nacimiento omitida.')
    elif estado in (EstadoRegistro.ESPERANDO_DOMICILIO, EstadoRegistro.ESPERANDO_PARENTESCO_DOMICILIO):
        await _pedir_ine(update, sesion, prefijo='⏩ Comprobante de domicilio omitido.')
    elif estado == EstadoRegistro.ESPERANDO_INE:
        await _pedir_csf(update, sesion, prefijo='⏩ INE omitida.')
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
        teclado = InlineKeyboardMarkup([[boton('⏩ Omitir teléfono', 'reg:omitir'), boton('❌ Cancelar', 'reg:cancelar')]])
        await responder(update, 'Escribe el teléfono de contacto de emergencia:', reply_markup=teclado)
    elif estado == EstadoRegistro.ESPERANDO_EMERGENCIA_TELEFONO:
        sesion.datos['tel_eme1'] = ''
        await _pedir_tel_candidato(update, sesion, prefijo='⏩ Teléfono de emergencia omitido.')
    elif estado == EstadoRegistro.ESPERANDO_ENCUESTA_RECLUTADOR:
        sesion.datos['nombre_reclutador'] = ''
        await _pedir_numero_temporal(update, sesion, prefijo='⏩ Reclutador omitido.')


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
    genero = d.get('sexo') or ''
    procedencia = d.get('procedencia') or ''

    calle = d.get('calle') or ''
    num_ext = d.get('num_ext') or ''
    num_int = d.get('num_int') or ''
    colonia = d.get('colonia') or ''
    municipio = d.get('municipio') or ''
    estado_dir = d.get('estado') or ''
    cp = d.get('cp') or ''

    tel_movil = d.get('tel_movil') or ''
    nom_eme = d.get('nom_eme1') or ''
    par_eme = d.get('par_eme1') or ''
    tel_eme = d.get('tel_eme1') or ''

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
        'procedencia': procedencia,
        'calle': calle,
        'num_ext': num_ext,
        'num_int': num_int,
        'colonia': colonia,
        'municipio': municipio,
        'estado': estado_dir,
        'cp': cp,
        'tel_movil': tel_movil,
        'nom_eme1': nom_eme,
        'par_eme1': par_eme,
        'tel_eme1': tel_eme,
    }

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
            f"• **CURP:** {curp or 'Sin dato'}\n"
            f"• **RFC:** {rfc or 'Sin dato'}\n"
            f"• **NSS:** {nss or 'Sin dato'}\n"
            f"• **Fecha nac.:** {fecha_nac or 'Sin dato'} ({edad + ' años' if edad else 's/d'})\n"
            f"• **Sexo:** {genero or 'Sin dato'}\n"
            f"• **Procedencia:** {procedencia or 'Sin dato'}\n"
            f"• **Dirección:** {calle} {num_ext}, Col. {colonia}, CP {cp}, {municipio}, {estado_dir}\n"
            f"• **Teléfono candidato:** {tel_movil or 'Sin dato'}\n"
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

