"""Flujo de Seguridad en Telegram: listas de permisos/bajas y escáner de QR."""

import io
import logging
from typing import Any

from PIL import Image, ImageOps
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.backend import ClienteBackend, ErrorBackend
from bot.interfaz import responder, texto_error

logger = logging.getLogger(__name__)

# Estado temporal por telegram_id para el módulo de seguridad
_ESPERANDO_QR: set[int] = set()
_LISTAS_CACHE: dict[int, dict[str, Any]] = {}


def tiene_escaner_activo(telegram_id: int) -> bool:
    return telegram_id in _ESPERANDO_QR


def cancelar_seguridad(telegram_id: int) -> bool:
    estaba = telegram_id in _ESPERANDO_QR
    _ESPERANDO_QR.discard(telegram_id)
    _LISTAS_CACHE.pop(telegram_id, None)
    return estaba


def _teclado_volver() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🏠 Menú principal', callback_data='m:raiz')],
    ])


def _formato_validacion(item: dict[str, Any], fecha_texto: str) -> str:
    tipo_permiso = item.get('tipo_permiso') or item.get('tipo') or ''
    nombre = item.get('nombre_completo') or ''
    no_emp = item.get('no_empleado') or ''
    fecha = fecha_texto or item.get('fecha') or ''
    folio = item.get('folio') or ''
    lineas = [
        'Recuerde ver su gafete físico para validar sus datos.',
        f'Permiso de {tipo_permiso}, válido únicamente el {fecha}, autorizado por Recursos Humanos de Cheong Woon.',
        '',
        f'👤 Empleado: {nombre}',
        f'🪪 N°: {no_emp}',
    ]
    if folio:
        lineas.append(f'🔖 Folio: `{folio}`')
    if item.get('hora'):
        lineas.append(f'🕒 Hora: {item["hora"]}')
    if item.get('motivo'):
        lineas.append(f'📝 Motivo: {item["motivo"]}')
    return '\n'.join(lineas)


async def mostrar_lista(update: Update, backend: ClienteBackend, categoria: str) -> None:
    telegram_id = update.effective_user.id
    _ESPERANDO_QR.discard(telegram_id)
    titulos = {
        'salidas': '🚪 Lista de Salidas (Hoy)',
        'entradas': '🚪 Lista de Entradas (Hoy)',
        'suspendidos': '⛔ Lista de Suspendidos',
        'bajas': '❌ Lista de Bajas Confirmadas',
    }
    try:
        resp = await backend.peticion(telegram_id, 'GET', f'/api/seguridad/listas/{categoria}')
    except ErrorBackend as error:
        await update.callback_query.edit_message_text(texto_error(error), reply_markup=_teclado_volver())
        return
    datos = resp.datos if isinstance(resp.datos, dict) else {}
    items = datos.get('items') or []
    fecha_texto = datos.get('fecha_texto') or ''
    _LISTAS_CACHE[telegram_id] = {'categoria': categoria, 'items': items, 'fecha_texto': fecha_texto}

    if not items:
        await update.callback_query.edit_message_text(
            f"{titulos.get(categoria, categoria)}\n\nNo hay registros activos por el momento.",
            reply_markup=_teclado_volver(),
        )
        return

    filas = []
    for idx, item in enumerate(items[:35]):
        estado_icon = '✅ ' if item.get('confirmado') else ''
        etiqueta = f"{estado_icon}{item.get('nombre_completo')} ({item.get('no_empleado')})"
        filas.append([InlineKeyboardButton(etiqueta[:60], callback_data=f'seg:sel:{categoria}:{idx}')])
    filas.append([InlineKeyboardButton('🏠 Menú principal', callback_data='m:raiz')])

    await update.callback_query.edit_message_text(
        f"{titulos.get(categoria, categoria)}\nSelecciona un empleado para ver su formato:",
        reply_markup=InlineKeyboardMarkup(filas),
    )


async def iniciar_escaner_qr(update: Update) -> None:
    telegram_id = update.effective_user.id
    _ESPERANDO_QR.add(telegram_id)
    await update.callback_query.edit_message_text(
        "📷 *Escanear QR*\n\n"
        "Envía la **foto del código QR** del empleado (o escribe el **folio** directamente):",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('❌ Cancelar', callback_data='seg:cancel')]]),
    )


async def _enviar_ficha_empleado(
    chat_message,
    backend: ClienteBackend,
    telegram_id: int,
    item: dict[str, Any],
    fecha_texto: str,
    mostrar_confirmar: bool,
) -> None:
    texto = _formato_validacion(item, fecha_texto)
    botones = []
    inc_id = item.get('incidencia_id')
    if mostrar_confirmar and inc_id and not item.get('confirmado'):
        botones.append([InlineKeyboardButton('✅ Confirmar salida / permiso', callback_data=f'seg:conf:{inc_id}')])
    elif item.get('confirmado'):
        texto += '\n\n✅ *Estado:* Confirmado'
    botones.append([InlineKeyboardButton('🏠 Menú principal', callback_data='m:raiz')])
    teclado = InlineKeyboardMarkup(botones)

    if item.get('registro_id'):
        try:
            tarjeta_resp = await backend.peticion(telegram_id, 'GET', f"/api/seguridad/tarjeta/{item['registro_id']}")
            if tarjeta_resp.contenido:
                await chat_message.reply_photo(
                    photo=io.BytesIO(tarjeta_resp.contenido),
                    caption=texto[:1024],
                    parse_mode='Markdown',
                    reply_markup=teclado,
                )
                return
        except Exception as exc:
            logger.debug('No se pudo cargar la tarjeta del empleado %s: %s', item.get('registro_id'), exc)

    await chat_message.reply_text(texto, parse_mode='Markdown', reply_markup=teclado)


async def al_boton(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    consulta = update.callback_query
    await consulta.answer()
    data = consulta.data or ''
    telegram_id = consulta.from_user.id
    backend: ClienteBackend = context.application.bot_data['backend']

    if data == 'seg:cancel':
        _ESPERANDO_QR.discard(telegram_id)
        await consulta.edit_message_text('Escaneo cancelado.', reply_markup=_teclado_volver())
        return

    if data.startswith('seg:sel:'):
        partes = data.split(':')
        if len(partes) != 4:
            return
        _, _, categoria, idx_str = partes
        cache = _LISTAS_CACHE.get(telegram_id) or {}
        items = cache.get('items') or []
        try:
            item = items[int(idx_str)]
        except (ValueError, IndexError):
            await consulta.edit_message_text('Lista desactualizada. Vuelve a abrirla.', reply_markup=_teclado_volver())
            return
        await _enviar_ficha_empleado(
            consulta.message,
            backend,
            telegram_id,
            item,
            cache.get('fecha_texto') or item.get('fecha') or '',
            mostrar_confirmar=(categoria != 'bajas'),
        )
        return

    if data.startswith('seg:conf:'):
        inc_id = data.removeprefix('seg:conf:')
        try:
            await backend.peticion(
                telegram_id,
                'POST',
                '/api/seguridad/confirmar',
                json={'incidencia_id': int(inc_id)},
                headers={'X-Origen-Telegram': 'seguridad'},
            )
        except ErrorBackend as error:
            await consulta.message.reply_text(f"⚠️ {texto_error(error)}", reply_markup=_teclado_volver())
            return
        msg_ok = '✅ Salida / permiso confirmado correctamente. Este código QR ya quedó marcado como utilizado.'
        if consulta.message.caption is not None:
            await consulta.edit_message_caption(caption=f"{consulta.message.caption}\n\n{msg_ok}", reply_markup=_teclado_volver())
        else:
            await consulta.edit_message_text(f"{consulta.message.text}\n\n{msg_ok}", reply_markup=_teclado_volver())


def _leer_qr_imagen(contenido: bytes) -> str | None:
    try:
        import zxingcpp
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(contenido))).convert('RGB')
        for intento in (img, img.rotate(90, expand=True), img.rotate(180, expand=True), img.rotate(270, expand=True)):
            codigos = zxingcpp.read_barcodes(intento)
            for c in codigos:
                if c.text and c.text.strip():
                    return c.text.strip()
    except Exception as exc:
        logger.debug('Error leyendo QR: %s', exc)
    return None


async def _validar_folio(update: Update, context: ContextTypes.DEFAULT_TYPE, folio: str) -> None:
    telegram_id = update.effective_user.id
    backend: ClienteBackend = context.application.bot_data['backend']
    try:
        resp = await backend.peticion(
            telegram_id,
            'POST',
            '/api/seguridad/validar-qr',
            json={'folio': folio.strip()},
        )
    except ErrorBackend as error:
        await responder(update, f"❌ {texto_error(error)}", reply_markup=_teclado_volver())
        return
    datos = resp.datos if isinstance(resp.datos, dict) else {}
    if not datos.get('valido'):
        await responder(update, f"❌ {datos.get('error') or 'QR inválido.'}", reply_markup=_teclado_volver())
        return
    _ESPERANDO_QR.discard(telegram_id)
    item = datos.get('item') or {}
    await _enviar_ficha_empleado(
        update.effective_message,
        backend,
        telegram_id,
        item,
        item.get('fecha_texto') or item.get('fecha') or '',
        mostrar_confirmar=True,
    )


async def atender_texto(update: Update, context: ContextTypes.DEFAULT_TYPE, texto: str) -> bool:
    telegram_id = update.effective_user.id
    if telegram_id not in _ESPERANDO_QR:
        return False
    await _validar_folio(update, context, texto)
    return True


async def atender_archivo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    telegram_id = update.effective_user.id
    if telegram_id not in _ESPERANDO_QR:
        return False
    msg = update.effective_message
    if not msg.photo and not msg.document:
        return False
    await responder(update, '🔍 Leyendo código QR...')
    try:
        file_id = msg.photo[-1].file_id if msg.photo else msg.document.file_id
        archivo = await context.bot.get_file(file_id)
        contenido = bytes(await archivo.download_as_bytearray())
    except Exception as exc:
        await responder(update, f'⚠️ No se pudo descargar la imagen: {exc}')
        return True
    folio = _leer_qr_imagen(contenido)
    if not folio:
        await responder(update, '⚠️ No pude leer el código QR. Envía una foto más nítida o escribe el folio manualmente.')
        return True
    await _validar_folio(update, context, folio)
    return True
