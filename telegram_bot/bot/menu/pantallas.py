"""Pantallas del menú /sistema.

Prefijos de ``callback_data`` (todos ≤ 64 bytes):
  m:<nodo>      navegar o ejecutar un nodo de ``navegacion.NODOS``
  e:<i>         abrir el empleado i de la última lista
  ea:<acción>   acción sobre el empleado abierto (inc, reg, est, del)
  c:<i>         elegir el candidato i de los ingresos pendientes
  cp:<página>   página de la lista de candidatos
  a:<i|b|s|x>   formulario: opción i, atrás, omitir, cancelar

Los botones llevan índices, nunca ids ni rutas. Ningún camino de aquí escribe:
los formularios terminan en un ``CambioPendiente`` que confirma ``al_boton``.
"""

import inspect
import logging
from datetime import date, datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.backend import ClienteBackend, ErrorBackend
from bot.borrador import Archivo, CambioPendiente
from bot.config import Settings
from bot.estado import EstadoUsuario, Estados
from bot.interfaz import (
    boton,
    formatear_empleado,
    mostrar,
    parametros_busqueda,
    responder,
    teclado_confirmar,
    texto_error,
)
from bot import documentos, menu_seguridad, registro_candidato
from bot.menu import formularios
from bot.menu.asistente import Asistente
from bot.menu.navegacion import NODOS, hijos_visibles, padre_de, puede

logger = logging.getLogger(__name__)

POR_PAGINA = 8
#: Orden de las barras que devuelve /api/datos-grafica?periodo=mes (InicioReclu.html).
FUENTES = ['Facebook', 'Posteo', 'Volante', 'Recomendación', 'Reclu CW', 'Reclu puro']
MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto',
         'septiembre', 'octubre', 'noviembre', 'diciembre']
CADUCADO = 'Este menú ya caducó. Ábrelo de nuevo con /sistema.'
EXPORTAR_EMPRESA = {'dl_cw': 'CHEONG_WOON', 'dl_kronos': 'KRONOS'}


def _backend(context) -> ClienteBackend:
    return context.application.bot_data['backend']


def _estados(context) -> Estados:
    return context.application.bot_data['estados']


def _settings(context) -> Settings:
    return context.application.bot_data['settings']


def hoy(context) -> date:
    return datetime.now(ZoneInfo(_settings(context).ZONA_HORARIA)).date()


def _menu_inicio() -> list:
    return [boton('🏠 Menú', 'm:raiz')]


async def _permisos(context, telegram_id: int) -> list[str]:
    return (await _backend(context).sesion(telegram_id)).permisos


# --- Navegación ---------------------------------------------------------------------

def teclado_nodo(clave: str, permisos: list[str]) -> InlineKeyboardMarkup:
    filas = [[boton(hijo.titulo, f'm:{hijo.clave}')] for hijo in hijos_visibles(clave, permisos)]
    padre = padre_de(clave)
    if padre:
        filas.append([boton('⬅️ Atrás', f'm:{padre}')])
    return InlineKeyboardMarkup(filas)


async def cmd_sistema(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = update.effective_user.id
    estado = _estados(context).de(telegram_id)
    estado.limpiar_menu()
    try:
        permisos = await _permisos(context, telegram_id)
    except ErrorBackend as error:
        await responder(update, texto_error(error))
        return
    await responder(update, '¿Qué quieres hacer?', teclado_nodo('raiz', permisos))


async def al_boton_nodo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    consulta = update.callback_query
    await consulta.answer()
    telegram_id = consulta.from_user.id
    clave = (consulta.data or '').removeprefix('m:')
    nodo = NODOS.get(clave)
    if nodo is None:
        await mostrar(update, CADUCADO, editar=True)
        return
    try:
        permisos = await _permisos(context, telegram_id)
    except ErrorBackend as error:
        await mostrar(update, texto_error(error), editar=True)
        return
    if not puede(nodo, permisos):
        await mostrar(update, '🚫 Tu cuenta no tiene acceso a esta opción.', editar=True)
        return
    estado = _estados(context).de(telegram_id)
    estado.limpiar_menu()
    if nodo.hijos:
        titulo = '¿Qué quieres hacer?' if clave == 'raiz' else nodo.titulo
        await mostrar(update, titulo, teclado_nodo(clave, permisos), editar=True)
        return
    try:
        await ACCIONES[nodo.accion](update, context, estado)
    except ErrorBackend as error:
        await mostrar(update, texto_error(error), InlineKeyboardMarkup([_menu_inicio()]), editar=True)


# --- Empleados --------------------------------------------------------------------------

async def accion_buscar_empleado(update, context, estado: EstadoUsuario) -> None:
    estado.esperando = 'buscar'
    await mostrar(
        update,
        '🔎 Escribe el número de empleado o al menos 3 letras del nombre.',
        InlineKeyboardMarkup([_menu_inicio()]),
        editar=True,
    )


async def buscar_texto(update: Update, context, estado: EstadoUsuario, texto: str) -> None:
    params = parametros_busqueda(texto)
    if params is None:
        await responder(update, 'Escribe un número de empleado o al menos 3 letras del nombre.')
        return
    telegram_id = update.effective_user.id
    respuesta = await _backend(context).peticion(telegram_id, 'GET', '/api/empleados/buscar', params=params)
    empleados = list((respuesta.datos or {}).get('empleados') or [])
    if not empleados:
        await responder(update, 'No encontré empleados con ese dato. Escribe otro número o nombre.',
                        InlineKeyboardMarkup([_menu_inicio()]))
        return
    estado.esperando = None
    estado.lista, estado.tipo_lista = empleados, 'empleados'
    if len(empleados) == 1:
        await mostrar_ficha(update, context, estado, empleados[0])
        return
    filas = [
        [boton(f'{e.get("no_empleado") or "s/n"} · {e.get("nombre_completo") or "Sin nombre"}', f'e:{i}')]
        for i, e in enumerate(empleados)
    ]
    filas.append([boton('🔎 Buscar otro', 'm:empleados'), *_menu_inicio()])
    await responder(update, f'Encontré {len(empleados)} empleados. Elige uno:', InlineKeyboardMarkup(filas))


async def mostrar_ficha(update, context, estado: EstadoUsuario, empleado: dict[str, Any], *, editar=False) -> None:
    estado.seleccionado = empleado
    permisos = await _permisos(context, update.effective_user.id)
    filas = []
    if 'admin' in permisos:
        activo = str(empleado.get('estatus') or 'ACTIVO').upper() == 'ACTIVO'
        filas += [
            [boton('🗂 Historial de incidencias', 'ea:inc'), boton('➕ Registrar incidencia', 'ea:reg')],
            [boton('⏸ Dar de baja' if activo else '▶️ Reactivar', 'ea:est'), boton('🗑 Eliminar', 'ea:del')],
            [boton('📄 Actualizar con documento', 'ea:doc')],
        ]
    filas.append([boton('🔎 Buscar otro', 'm:empleados'), *_menu_inicio()])
    await mostrar(update, formatear_empleado(empleado), InlineKeyboardMarkup(filas), editar=editar)


async def al_boton_empleado(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    consulta = update.callback_query
    await consulta.answer()
    estado = _estados(context).de(consulta.from_user.id)
    try:
        indice = int((consulta.data or '').removeprefix('e:'))
    except ValueError:
        indice = -1
    if estado.tipo_lista != 'empleados' or not 0 <= indice < len(estado.lista):
        await mostrar(update, CADUCADO, editar=True)
        return
    try:
        await mostrar_ficha(update, context, estado, estado.lista[indice], editar=True)
    except ErrorBackend as error:
        await mostrar(update, texto_error(error), editar=True)


def lector(context, telegram_id: int, empleado: dict[str, Any]):
    """La lectura del documento para el formulario: ficha fresca + OCR del backend + diff."""
    async def leer(tipo, archivo) -> CambioPendiente:
        backend = _backend(context)
        registro_id = int(empleado['registro_id'])
        actual = (await backend.peticion(telegram_id, 'GET', f'/api/obtener-empleado/{registro_id}')).datos or {}
        leidos = await documentos.escanear(backend, telegram_id, tipo, archivo)
        return documentos.pendiente_de_documento({**empleado, **actual, 'registro_id': registro_id},
                                                 tipo, leidos, archivo)
    return leer


async def al_boton_accion_empleado(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    consulta = update.callback_query
    await consulta.answer()
    telegram_id = consulta.from_user.id
    estado = _estados(context).de(telegram_id)
    empleado = estado.seleccionado
    accion = (consulta.data or '').removeprefix('ea:')
    if empleado is None:
        await mostrar(update, CADUCADO, editar=True)
        return
    try:
        if accion == 'fic':
            await mostrar_ficha(update, context, estado, empleado, editar=True)
        elif accion == 'inc':
            await mostrar_incidencias(update, context, empleado)
        elif accion == 'reg':
            await iniciar_asistente(update, context, estado, formularios.asistente_incidencia(empleado, lambda: hoy(context)))
        elif accion == 'est':
            await proponer(update, estado, formularios.pendiente_estatus(empleado))
        elif accion == 'del':
            await proponer(update, estado, formularios.pendiente_eliminar(empleado))
        elif accion == 'doc':
            await iniciar_asistente(update, context, estado,
                                    formularios.asistente_documento(empleado, lector(context, telegram_id, empleado)))
        else:
            await mostrar(update, CADUCADO, editar=True)
    except ErrorBackend as error:
        await mostrar(update, texto_error(error), editar=True)


def formatear_incidencia(incidencia: dict[str, Any]) -> str:
    def fecha(valor):
        try:
            return date.fromisoformat(str(valor)[:10]).strftime('%d/%m/%Y')
        except ValueError:
            return '¿?'

    partes = [str(incidencia.get('type') or 'incidencia').replace('-', ' ').capitalize()]
    if incidencia.get('fecha_inicio'):
        rango = fecha(incidencia['fecha_inicio'])
        if incidencia.get('fecha_reingreso') and incidencia['fecha_reingreso'] != incidencia['fecha_inicio']:
            rango += f' → {fecha(incidencia["fecha_reingreso"])}'
        partes.append(rango)
    if incidencia.get('dias'):
        partes.append(f'{incidencia["dias"]} día(s)')
    if incidencia.get('hora'):
        partes.append(f'{incidencia.get("opcion") or ""} {incidencia["hora"]}'.strip())
    motivo = str(incidencia.get('motivo') or '').strip()
    if motivo:
        partes.append(motivo if len(motivo) <= 80 else motivo[:79] + '…')
    texto = '• ' + ' · '.join(partes)
    return texto + (' 📎' if incidencia.get('archivo_nombre') else '')


async def mostrar_incidencias(update, context, empleado: dict[str, Any]) -> None:
    respuesta = await _backend(context).peticion(
        update.effective_user.id, 'GET', f'/api/empleados/{int(empleado["registro_id"])}/incidencias'
    )
    incidencias = list((respuesta.datos or {}).get('incidencias') or [])
    nombre = empleado.get('nombre_completo') or 'Sin nombre'
    if not incidencias:
        texto = f'🗂 {nombre}\n\nNo tiene incidencias registradas.'
    else:
        recientes = incidencias[-10:][::-1]
        texto = f'🗂 {nombre} — últimas {len(recientes)} de {len(incidencias)}:\n\n' + '\n'.join(
            formatear_incidencia(i) for i in recientes
        )
    filas = [[boton('➕ Registrar incidencia', 'ea:reg')], [boton('⬅️ Ficha', 'ea:fic'), *_menu_inicio()]]
    await mostrar(update, texto, InlineKeyboardMarkup(filas), editar=True)


# --- Ingresos pendientes ---------------------------------------------------------------------

async def accion_pendientes(update, context, estado: EstadoUsuario) -> None:
    respuesta = await _backend(context).peticion(update.effective_user.id, 'GET', '/api/obtener-pendientes')
    candidatos = respuesta.datos if isinstance(respuesta.datos, list) else []
    estado.lista, estado.tipo_lista = candidatos, 'candidatos'
    await mostrar_pagina_candidatos(update, estado, 0)


async def mostrar_pagina_candidatos(update, estado: EstadoUsuario, pagina: int) -> None:
    candidatos = estado.lista
    if not candidatos:
        await mostrar(update, '📝 No hay ingresos pendientes.', InlineKeyboardMarkup([_menu_inicio()]), editar=True)
        return
    paginas = (len(candidatos) + POR_PAGINA - 1) // POR_PAGINA
    pagina = max(0, min(pagina, paginas - 1))
    inicio = pagina * POR_PAGINA
    filas = [
        [boton(f'{c.get("nombre_completo") or "Sin nombre"} ({formularios.normalizar_numero_temporal(c.get("numero_temporal")) or "sin número"})', f'c:{i}')]
        for i, c in enumerate(candidatos[inicio:inicio + POR_PAGINA], start=inicio)
    ]
    navegacion = []
    if pagina > 0:
        navegacion.append(boton('◀️', f'cp:{pagina - 1}'))
    if pagina < paginas - 1:
        navegacion.append(boton('▶️', f'cp:{pagina + 1}'))
    if navegacion:
        filas.append(navegacion)
    filas.append(_menu_inicio())
    texto = f'📝 Ingresos pendientes: {len(candidatos)}' + (f' (página {pagina + 1} de {paginas})' if paginas > 1 else '')
    await mostrar(update, texto + '\nElige a quién asignar puesto:', InlineKeyboardMarkup(filas), editar=True)


async def al_boton_candidato(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    consulta = update.callback_query
    await consulta.answer()
    estado = _estados(context).de(consulta.from_user.id)
    datos = consulta.data or ''
    if estado.tipo_lista != 'candidatos':
        await mostrar(update, CADUCADO, editar=True)
        return
    try:
        if datos.startswith('cp:'):
            await mostrar_pagina_candidatos(update, estado, int(datos[3:]))
            return
        indice = int(datos.removeprefix('c:'))
    except ValueError:
        await mostrar(update, CADUCADO, editar=True)
        return
    if not 0 <= indice < len(estado.lista):
        await mostrar(update, CADUCADO, editar=True)
        return
    try:
        catalogo = (await _backend(context).peticion(consulta.from_user.id, 'GET', '/api/catalogos/ingreso')).datos
        asistente = formularios.asistente_ingreso(estado.lista[indice], catalogo or {})
    except ErrorBackend as error:
        await mostrar(update, texto_error(error), editar=True)
        return
    except ValueError as error:
        await mostrar(update, f'⚠️ {error}', InlineKeyboardMarkup([_menu_inicio()]), editar=True)
        return
    await iniciar_asistente(update, context, estado, asistente)


# --- Reclutamiento y descargas ----------------------------------------------------------------

async def accion_ind_mes(update, context, estado) -> None:
    dia = hoy(context)
    backend, telegram_id = _backend(context), update.effective_user.id
    por_fuente = (await backend.peticion(telegram_id, 'GET', '/api/datos-grafica',
                                         params={'periodo': 'mes', 'mes': dia.month})).datos or []
    por_mes = (await backend.peticion(telegram_id, 'GET', '/api/datos-grafica',
                                      params={'periodo': '12meses'})).datos or []
    lineas = [f'📊 Ingresos de {MESES[dia.month - 1]} {dia.year}']
    if isinstance(por_mes, list) and len(por_mes) == 12:
        lineas.append(f'Total del mes: {por_mes[dia.month - 1]} · En el año: {sum(por_mes)}')
    lineas.append('')
    for fuente, total in zip(FUENTES, por_fuente if isinstance(por_fuente, list) else []):
        lineas.append(f'• {fuente}: {total}')
    await mostrar(update, '\n'.join(lineas),
                  InlineKeyboardMarkup([[boton('⬅️ Atrás', 'm:reclutamiento'), *_menu_inicio()]]), editar=True)


async def accion_ind_team(update, context, estado) -> None:
    dia = hoy(context)
    datos = (await _backend(context).peticion(
        update.effective_user.id, 'GET', '/api/ingresos-por-team', params={'mes': dia.month, 'anio': dia.year}
    )).datos or {}
    lineas = [f'👥 Ingresos por equipo — {MESES[dia.month - 1]} {dia.year}', '']
    for equipo in datos.get('equipos') or []:
        lineas.append(f'Team {equipo.get("team")}: {equipo.get("total", 0)}')
        for reclutador in equipo.get('reclutadores') or []:
            lineas.append(f'   · {reclutador.get("nombre")}: {reclutador.get("total", 0)}')
    await mostrar(update, '\n'.join(lineas),
                  InlineKeyboardMarkup([[boton('⬅️ Atrás', 'm:reclutamiento'), *_menu_inicio()]]), editar=True)


async def _descargar(update, context, ruta: str, params: dict | None, nombre_defecto: str, volver_menu: str = 'm:descargas', caption: str | None = None) -> None:
    await mostrar(update, '⏳ Generando el archivo…', editar=True)
    respuesta = await _backend(context).peticion(update.effective_user.id, 'GET', ruta, params=params, timeout=120)
    if not respuesta.es_archivo:
        await mostrar(update, '⚠️ El sistema no devolvió un archivo.', editar=True)
        return
    # El archivo va directo a la persona; nunca pasa por un modelo.
    await update.effective_message.reply_document(
        document=respuesta.contenido, filename=respuesta.nombre_archivo or nombre_defecto, caption=caption
    )
    destino_volver = 'Formatos del día' if volver_menu == 'm:formatos_dia' else 'Descargas'
    await mostrar(update, '📥 Archivo enviado correctamente.', InlineKeyboardMarkup([[boton(f'⬅️ {destino_volver}', volver_menu), *_menu_inicio()]]),
                  editar=True)


async def accion_dl_empresa(update, context, estado) -> None:
    clave = (update.callback_query.data or '').removeprefix('m:')
    empresa = EXPORTAR_EMPRESA[clave]
    await _descargar(update, context, f'/api/exportar-empleados/{quote(empresa)}', {'todos': '1'},
                     f'listado_{empresa.lower()}.xlsx')


async def accion_dl_reclu(update, context, estado) -> None:
    dia = hoy(context)
    await _descargar(update, context, '/api/exportar-reclutamiento', {'mes': dia.month, 'anio': dia.year},
                     f'reclutamiento_{dia.year}_{dia.month:02d}.xlsx')


async def accion_dl_permisos(update, context, _estado) -> None:
    dia = hoy(context)
    await _descargar(update, context, '/api/exportar-permisos', {},
                     f'lista_permisos_{dia.isoformat()}.xlsx', volver_menu='m:raiz')


# --- Nuevas acciones del menú de Recursos Humanos -------------------------------------------

async def accion_ev_cumple(update, context, estado: EstadoUsuario) -> None:
    backend, telegram_id = _backend(context), update.effective_user.id
    resp = await backend.peticion(telegram_id, 'GET', '/api/shared-events')
    eventos = (resp.datos or {}).get('eventos') or []
    await iniciar_asistente(update, context, estado, formularios.asistente_cumpleanos(eventos, lambda: hoy(context)))


async def accion_ev_general(update, context, estado: EstadoUsuario) -> None:
    backend, telegram_id = _backend(context), update.effective_user.id
    resp = await backend.peticion(telegram_id, 'GET', '/api/shared-events')
    eventos = (resp.datos or {}).get('eventos') or []
    await iniciar_asistente(update, context, estado, formularios.asistente_evento(eventos, lambda: hoy(context)))


async def accion_acc_nuevo(update, context, estado: EstadoUsuario) -> None:
    await iniciar_asistente(update, context, estado, formularios.asistente_nuevo_usuario())


async def accion_acc_editar(update, context, estado: EstadoUsuario) -> None:
    backend, telegram_id = _backend(context), update.effective_user.id
    resp = await backend.peticion(telegram_id, 'GET', '/api/access-users')
    usuarios = (resp.datos or {}).get('usuarios') or []
    if not usuarios:
        await mostrar(update, 'No hay usuarios de acceso registrados.', InlineKeyboardMarkup([_menu_inicio()]), editar=True)
        return
    await iniciar_asistente(update, context, estado, formularios.asistente_editar_usuario(usuarios))


async def accion_acc_eliminar(update, context, estado: EstadoUsuario) -> None:
    backend, telegram_id = _backend(context), update.effective_user.id
    resp = await backend.peticion(telegram_id, 'GET', '/api/access-users')
    usuarios = (resp.datos or {}).get('usuarios') or []
    if not usuarios:
        await mostrar(update, 'No hay usuarios de acceso registrados.', InlineKeyboardMarkup([_menu_inicio()]), editar=True)
        return
    await iniciar_asistente(update, context, estado, formularios.asistente_eliminar_usuario(usuarios))


async def accion_ingresos_pendientes(update, context, estado: EstadoUsuario) -> None:
    respuesta = await _backend(context).peticion(update.effective_user.id, 'GET', '/api/obtener-pendientes')
    candidatos = respuesta.datos if isinstance(respuesta.datos, list) else []
    if not candidatos:
        await mostrar(update, '📝 *Ingresos Pendientes*\n\nNo hay ingresos nuevos pendientes en este momento.',
                      InlineKeyboardMarkup([_menu_inicio()]), editar=True)
        return
    lineas = [f'📝 *Ingresos Pendientes ({len(candidatos)})*', '', 'Candidatos en la pestaña de pendientes:']
    for c in candidatos:
        nombre = c.get('nombre_completo') or 'Sin nombre'
        temp = formularios.normalizar_numero_temporal(c.get('numero_temporal')) or 'sin número'
        lineas.append(f'• {nombre} ({temp})')
    await mostrar(update, '\n'.join(lineas), InlineKeyboardMarkup([_menu_inicio()]), editar=True)


LINKS_IMPRIMIR = {
    'print_suspension': ('Suspensión', 'https://drive.google.com/file/d/1IMw93o8y56e423HZoKVJNnE5z5b26e5A/view?usp=sharing'),
    'print_entradasalida': ('Entrada/Salida', 'https://drive.google.com/file/d/1MjIXgFLUJAO25hNfZb0IzTFwIW4YLvU5/view?usp=sharing'),
    'print_indisciplina': ('Indisciplina', 'https://drive.google.com/file/d/1gWuv28J-7WjbfsaOdUMSIb0Mbb6KHRqO/view?usp=sharing'),
}


async def accion_imprimir(update, context, estado: EstadoUsuario) -> None:
    clave = (update.callback_query.data or '').removeprefix('m:')
    titulo, url = LINKS_IMPRIMIR.get(clave, ('Formato', ''))
    filas = [
        [InlineKeyboardButton(f'🖨️ Abrir {titulo} (Google Drive)', url=url)],
        [boton('⬅️ Formatos para Imprimir', 'm:formatos_print'), *_menu_inicio()],
    ]
    texto = (
        f'🖨️ *Formato para Imprimir — {titulo}*\n\n'
        f'Abre e imprime el formato directamente en Google Drive:'
    )
    await mostrar(update, texto, InlineKeyboardMarkup(filas), editar=True)


def formatear_apellidos_nombres(nombre: str) -> str:
    if not nombre:
        return ''
    palabras = nombre.strip().split()
    if len(palabras) <= 2:
        return nombre.upper()
    apellidos = ' '.join(palabras[-2:])
    nombres = ' '.join(palabras[:-2])
    return f"{apellidos} {nombres}".upper()


def generar_html_checklist(candidatos: list[dict], fecha_str: str) -> bytes:
    filas_html = []
    for idx, c in enumerate(candidatos, 1):
        nom = c.get('nombre_completo') or 'Sin nombre'
        area = c.get('area') or c.get('puesto') or 'Producción'
        num = c.get('no_empleado') or c.get('numero_temporal') or ''
        filas_html.append(f"""
        <tr>
            <td style="text-align:center; font-weight:bold;">{idx}</td>
            <td style="font-weight:bold;">{nom} <span style="font-size:9pt; color:#4a5568; font-weight:normal;">({num})</span></td>
            <td style="text-align:center;">{area}</td>
            <td style="height:36px;"></td>
        </tr>
        """)
    filas_str = "\n".join(filas_html)

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Registro de Inducción - {fecha_str}</title>
    <style>
        body {{
            background-color: #e2e8f0;
            margin: 0;
            padding: 20px;
            font-family: Arial, sans-serif;
            display: flex;
            flex-direction: column;
            align-items: center;
        }}
        .controls {{
            margin-bottom: 20px;
            display: flex;
            gap: 15px;
        }}
        .btn {{
            padding: 12px 24px;
            font-size: 11pt;
            cursor: pointer;
            border: none;
            border-radius: 6px;
            font-weight: bold;
            background-color: #3182ce;
            color: white;
            box-shadow: 0 2px 4px rgba(0,0,0,0.2);
            text-decoration: none;
        }}
        .btn:hover {{
            background-color: #2b6cb0;
        }}
        .page {{
            background: white;
            width: 21.6cm;
            min-height: 27.9cm;
            padding: 1.4cm 1.3cm;
            margin: 10mm auto;
            box-sizing: border-box;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
            font-size: 11pt;
            line-height: 1.4;
            color: #1a202c;
        }}
        .register-header {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 12px;
        }}
        .register-logo {{
            width: 108px;
            height: auto;
            margin-top: -0.5cm;
        }}
        .register-title {{
            margin: 0 0 24px;
            font-family: "Times New Roman", Times, serif;
            font-size: 18pt;
            text-align: center;
        }}
        .register-schedule {{
            display: grid;
            grid-template-columns: 1fr 1fr 1fr;
            gap: 18px;
            margin-bottom: 24px;
        }}
        .schedule-field {{
            display: flex;
            align-items: baseline;
            gap: 6px;
            white-space: nowrap;
        }}
        .schedule-line {{
            flex: 1;
            min-width: 24px;
            height: 18px;
            border-bottom: 1px solid #1a202c;
        }}
        .register-table {{
            width: 100%;
            border-collapse: collapse;
            table-layout: fixed;
        }}
        .register-table th,
        .register-table td {{
            border: 1px solid #1a202c;
            padding: 7px 8px;
        }}
        .register-table th:first-child,
        .register-table td:first-child {{
            width: 8%;
            text-align: center;
        }}
        .register-table th:nth-child(2),
        .register-table td:nth-child(2) {{
            width: 50%;
            text-align: left;
        }}
        .register-table th:nth-child(3),
        .register-table td:nth-child(3) {{
            width: 22%;
            text-align: center;
        }}
        .register-table th:last-child,
        .register-table td:last-child {{
            width: 20%;
        }}
        @media print {{
            @page {{
                size: letter;
                margin: 0;
            }}
            html, body {{
                background: none;
                padding: 0;
                margin: 0;
                display: block;
            }}
            .controls {{
                display: none !important;
            }}
            .page {{
                box-shadow: none;
                margin: 0;
                width: 21.59cm;
                min-height: 27.94cm;
                height: auto;
            }}
            .register-table thead {{
                display: table-header-group;
            }}
            .register-table tr {{
                break-inside: avoid;
                page-break-inside: avoid;
            }}
        }}
    </style>
</head>
<body>
    <div class="controls">
        <button class="btn" onclick="window.print()">🖨️ Imprimir / Guardar PDF ({len(candidatos)} asistentes)</button>
    </div>
    <main class="page">
        <div class="register-header">
            <img class="register-logo" src="https://lh3.googleusercontent.com/d/1YuA-V3W27vrLeDszpzRYNJnwMKGvpHpA" alt="Cheong Woon Mexico">
        </div>
        <h1 class="register-title">REGISTRO DE INDUCCIÓN</h1>
        <div class="register-schedule">
            <div class="schedule-field"><strong>Fecha:</strong> <span>{fecha_str}</span></div>
            <div class="schedule-field"><strong>Hora de inicio:</strong><span class="schedule-line"></span></div>
            <div class="schedule-field"><strong>Hora de fin:</strong><span class="schedule-line"></span></div>
        </div>
        <table class="register-table">
            <thead>
                <tr>
                    <th scope="col">No.</th>
                    <th scope="col">Empleados</th>
                    <th scope="col">Área</th>
                    <th scope="col">Firma</th>
                </tr>
            </thead>
            <tbody>
                {filas_str}
            </tbody>
        </table>
    </main>
</body>
</html>"""
    return html.encode('utf-8')


def generar_html_evaluacion(candidatos: list[dict], fecha_str: str) -> bytes:
    paginas = []
    for idx, c in enumerate(candidatos, 1):
        nom_raw = c.get('nombre_completo') or 'Sin nombre'
        nom_formateado = formatear_apellidos_nombres(nom_raw)
        num_emp = c.get('no_empleado') or c.get('numero_temporal') or ''
        depto = c.get('area') or c.get('puesto') or 'Producción'

        paginas.append(f"""
    <div class="page" id="contrato-page-{idx}" style="line-height: 1.2; font-size: 11pt; font-family: Arial, sans-serif;">
        <!-- Logo y Fecha -->
        <div class="logo-date-row">
            <div class="logo-container">
                <img src="https://lh3.googleusercontent.com/d/1YuA-V3W27vrLeDszpzRYNJnwMKGvpHpA" alt="Cheong Woon Mexico">
            </div>
            <div class="document-date">
                Fecha: <span>{fecha_str}</span>
            </div>
        </div>
        <h1 class="document-title">EVALUACIÓN DE INDUCCIÓN Y ADAPTACIÓN LABORAL</h1>
        <div class="employee-row personal-row">
            <div class="employee-field">
                <strong>Nombre:</strong>
                <span class="employee-value">{nom_formateado}</span>
            </div>
            <div class="employee-field">
                <strong>N° Emp:</strong>
                <span class="employee-value">{num_emp}</span>
            </div>
        </div>
        <div class="employee-row">
            <div class="employee-field">
                <strong>Departamento:</strong>
                <span class="employee-value">{depto}</span>
            </div>
            <div class="employee-field">
                <strong>Líder:</strong>
                <span class="employee-value"></span>
            </div>
        </div>
        <div class="evaluation-instruction">Indica con una ✔ la opción deseada.</div>
        <div class="evaluation-question">¿Qué te pareció el trabajo?</div>
        <div class="rating-options">
            <label class="rating-option"><input type="checkbox" name="evaluacion-trabajo-{idx}" value="Excelente"> Excelente</label>
            <label class="rating-option"><input type="checkbox" name="evaluacion-trabajo-{idx}" value="Bueno"> Bueno</label>
            <label class="rating-option"><input type="checkbox" name="evaluacion-trabajo-{idx}" value="Regular"> Regular</label>
            <label class="rating-option"><input type="checkbox" name="evaluacion-trabajo-{idx}" value="Malo"> Malo</label>
        </div>
        <table class="induction-table">
            <thead>
                <tr>
                    <th></th>
                    <th scope="col">Sí</th>
                    <th scope="col">No</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td>¿Recibiste correctamente la inducción de la empresa al momento de entrar?</td>
                    <td><input type="checkbox" name="induccion-empresa-{idx}" value="si"></td>
                    <td><input type="checkbox" name="induccion-empresa-{idx}" value="no"></td>
                </tr>
                <tr>
                    <td>¿Se te presentó a tu jefe inmediato?</td>
                    <td><input type="checkbox" name="presentacion-jefe-{idx}" value="si"></td>
                    <td><input type="checkbox" name="presentacion-jefe-{idx}" value="no"></td>
                </tr>
                <tr>
                    <td>¿El trato del supervisor fue correcto y adecuado?</td>
                    <td><input type="checkbox" name="trato-supervisor-{idx}" value="si"></td>
                    <td><input type="checkbox" name="trato-supervisor-{idx}" value="no"></td>
                </tr>
                <tr>
                    <td>¿El trato del personal de Recursos Humanos fue correcto y adecuado?</td>
                    <td><input type="checkbox" name="trato-recursos-humanos-{idx}" value="si"></td>
                    <td><input type="checkbox" name="trato-recursos-humanos-{idx}" value="no"></td>
                </tr>
                <tr>
                    <td>¿El trato del personal coreano fue correcto y adecuado?</td>
                    <td><input type="checkbox" name="trato-personal-coreano-{idx}" value="si"></td>
                    <td><input type="checkbox" name="trato-personal-coreano-{idx}" value="no"></td>
                </tr>
                <tr>
                    <td>¿El trato de tu jefe inmediato fue correcto y adecuado?</td>
                    <td><input type="checkbox" name="trato-jefe-inmediato-{idx}" value="si"></td>
                    <td><input type="checkbox" name="trato-jefe-inmediato-{idx}" value="no"></td>
                </tr>
                <tr>
                    <td>¿Estás cómodo en tu área de trabajo?</td>
                    <td><input type="checkbox" name="comodidad-area-{idx}" value="si"></td>
                    <td><input type="checkbox" name="comodidad-area-{idx}" value="no"></td>
                </tr>
                <tr>
                    <td>¿Te capacitaron en tu área de trabajo?</td>
                    <td><input type="checkbox" name="capacitacion-area-{idx}" value="si"></td>
                    <td><input type="checkbox" name="capacitacion-area-{idx}" value="no"></td>
                </tr>
                <tr>
                    <td>¿Te dieron indicaciones de las actividades que realizarías?</td>
                    <td><input type="checkbox" name="indicaciones-actividades-{idx}" value="si"></td>
                    <td><input type="checkbox" name="indicaciones-actividades-{idx}" value="no"></td>
                </tr>
                <tr>
                    <td>¿Te informaron sobre los permisos sanitarios?</td>
                    <td><input type="checkbox" name="permisos-sanitarios-{idx}" value="si"></td>
                    <td><input type="checkbox" name="permisos-sanitarios-{idx}" value="no"></td>
                </tr>
                <tr>
                    <td>¿Te informaron sobre los permisos de entrada y salida a destiempo?</td>
                    <td><input type="checkbox" name="permisos-entrada-salida-{idx}" value="si"></td>
                    <td><input type="checkbox" name="permisos-entrada-salida-{idx}" value="no"></td>
                </tr>
            </tbody>
        </table>
        <br>
        <div class="evaluation-question">¿Qué tan clara fue la explicación sobre el servicio de comedor?</div>
        <div class="rating-options">
            <label class="rating-option"><input type="checkbox" name="evaluacion-comedor-{idx}" value="Excelente"> Excelente</label>
            <label class="rating-option"><input type="checkbox" name="evaluacion-comedor-{idx}" value="Bueno"> Bueno</label>
            <label class="rating-option"><input type="checkbox" name="evaluacion-comedor-{idx}" value="Regular"> Regular</label>
            <label class="rating-option"><input type="checkbox" name="evaluacion-comedor-{idx}" value="Malo"> Malo</label>
        </div>
        <br>
        <div class="evaluation-question">Cómo calificas el servicio de transporte proporcionado por la empresa</div>
        <div class="rating-options">
            <label class="rating-option"><input type="checkbox" name="evaluacion-transporte-{idx}" value="Excelente"> Excelente</label>
            <label class="rating-option"><input type="checkbox" name="evaluacion-transporte-{idx}" value="Bueno"> Bueno</label>
            <label class="rating-option"><input type="checkbox" name="evaluacion-transporte-{idx}" value="Regular"> Regular</label>
            <label class="rating-option"><input type="checkbox" name="evaluacion-transporte-{idx}" value="Malo"> Malo</label>
        </div>
        <br>
        <div class="evaluation-question">¿Cómo percibes el ambiente de trabajo en tu área?</div>
        <div class="rating-options">
            <label class="rating-option"><input type="checkbox" name="evaluacion-ambiente-{idx}" value="Excelente"> Excelente</label>
            <label class="rating-option"><input type="checkbox" name="evaluacion-ambiente-{idx}" value="Bueno"> Bueno</label>
            <label class="rating-option"><input type="checkbox" name="evaluacion-ambiente-{idx}" value="Regular"> Regular</label>
            <label class="rating-option"><input type="checkbox" name="evaluacion-ambiente-{idx}" value="Malo"> Malo</label>
        </div>
        <br>
        <div class="evaluation-question">Tienes algún comentario o sugerencia para mejorar la experiencia de bienvenida e integración del nuevo personal?</div>
        <div class="comment-line"></div>
        <div class="comment-line"></div>
    </div>
        """)

    paginas_str = "\n".join(paginas)

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Evaluaciones de Inducción - {fecha_str}</title>
    <style>
        body {{
            background-color: #e2e8f0;
            margin: 0;
            padding: 20px;
            font-family: Arial, sans-serif;
            display: flex;
            flex-direction: column;
            align-items: center;
        }}
        .controls {{
            margin-bottom: 20px;
            display: flex;
            gap: 15px;
        }}
        .btn {{
            padding: 12px 24px;
            font-size: 11pt;
            cursor: pointer;
            border: none;
            border-radius: 6px;
            font-weight: bold;
            background-color: #3182ce;
            color: white;
            box-shadow: 0 2px 4px rgba(0,0,0,0.2);
            text-decoration: none;
        }}
        .btn:hover {{
            background-color: #2b6cb0;
        }}
        .page {{
            background: white;
            width: 21.6cm;
            height: 27.9cm;
            padding: 1.4cm 1.3cm 1.5cm;
            margin: 10mm auto;
            box-sizing: border-box;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
            position: relative;
            font-size: 10pt;
            line-height: 1.5;
            color: #1a202c;
            text-align: justify;
            overflow: visible; 
            display: flex;
            flex-direction: column;
            justify-content: flex-start;
        }}
        .employee-row {{
            display: flex;
            align-items: flex-end;
            gap: 20px;
            margin-bottom: 10px;
            text-align: left;
        }}
        .employee-field {{
            display: flex;
            flex: 1;
            align-items: baseline;
            gap: 5px;
            min-width: 0;
            white-space: nowrap;
        }}
        .personal-row .employee-field:first-child {{
            flex: 1.6;
        }}
        .personal-row .employee-field:last-child {{
            flex: 0.8;
        }}
        .employee-value {{
            flex: 1;
            min-height: 18px;
            border-bottom: 1px solid #1a202c;
            overflow: hidden;
            text-overflow: ellipsis;
            padding-left: 4px;
            font-weight: bold;
        }}
        .evaluation-instruction {{
            margin: 8px 0;
            text-align: left;
        }}
        .evaluation-question {{
            margin: 0 0 10px;
            font-weight: bold;
            text-align: left;
        }}
        .rating-options {{
            display: flex;
            justify-content: center;
            gap: 28px;
            text-align: left;
        }}
        .rating-option {{
            display: inline-flex;
            align-items: center;
            gap: 7px;
            cursor: pointer;
        }}
        .rating-option input {{
            width: 15px;
            height: 15px;
            margin: 0;
            accent-color: #1a202c;
        }}
        .induction-table {{
            width: max-content;
            margin-left: auto;
            margin-right: auto;
            margin-top: 12px;
            border-collapse: collapse;
            table-layout: auto;
            font-size: 11pt;
            text-align: left;
        }}
        .induction-table th,
        .induction-table td {{
            padding: 3px 5px;
        }}
        .induction-table th:first-child,
        .induction-table td:first-child {{
            white-space: nowrap;
        }}
        .induction-table th:nth-child(2),
        .induction-table th:nth-child(3),
        .induction-table td:nth-child(2),
        .induction-table td:nth-child(3) {{
            width: 34px;
            text-align: center;
        }}
        .induction-table input {{
            width: 14px;
            height: 18px;
            margin: 0;
            vertical-align: middle;
            accent-color: #1a202c;
        }}
        .logo-date-row {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 6px;
        }}
        .logo-container {{
            max-width: 270px;
            width: 70%;
            margin-top: -0.65cm;
        }}
        .logo-container img {{
            width: 40%;
            height: auto;
        }}
        .document-date {{
            text-align: right;
            font-weight: 500;
        }}
        .document-title {{
            margin: -8px 0 22px;
            font-family: "Times New Roman", Times, serif;
            font-size: 16pt;
            text-align: center;
        }}
        .comment-line {{
            height: 24px;
            border-bottom: 1px solid #1a202c;
        }}
        @media print {{
            @page {{
                size: letter;
                margin: 0;
            }}
            html, body {{
                background: none;
                padding: 0;
                margin: 0;
                display: block;
            }}
            .controls {{
                display: none !important;
            }}
            .page {{
                box-shadow: none;
                margin: 0;
                width: 21.59cm;
                height: 27.94cm;
                break-inside: avoid;
                page-break-after: always;
                break-after: page;
            }}
            .page + .page {{
                page-break-before: always;
                break-before: page;
            }}
        }}
    </style>
</head>
<body>
    <div class="controls">
        <button class="btn" onclick="window.print()">🖨️ Descargar / Guardar PDF ({len(candidatos)} evaluaciones)</button>
    </div>
    {paginas_str}
</body>
</html>"""
    return html.encode('utf-8')


async def accion_formatos_dia(update, context, estado: EstadoUsuario) -> None:
    clave = (update.callback_query.data or '').removeprefix('m:')
    backend, telegram_id = _backend(context), update.effective_user.id
    fecha_hoy = hoy(context).strftime('%d/%m/%Y')
    fecha_iso = hoy(context).strftime('%Y-%m-%d')

    # Consultar ingresos registrados del día de hoy
    resp = await backend.peticion(telegram_id, 'GET', f'/api/ingresos-hoy?fecha={fecha_iso}')
    candidatos = resp.datos if isinstance(resp.datos, list) else []

    if not candidatos:
        # Fallback a pendientes de registro
        resp_p = await backend.peticion(telegram_id, 'GET', '/api/obtener-pendientes')
        candidatos = resp_p.datos if isinstance(resp_p.datos, list) else []

    nombre_formato = 'Evaluación de Inducción' if clave == 'dia_evaluacion' else 'Registro de Inducción'

    if not candidatos:
        await mostrar(
            update,
            f'📋 *{nombre_formato}*\n\nNo se detectaron ingresos registrados el día de hoy ({fecha_hoy}).',
            InlineKeyboardMarkup([[boton('⬅️ Formatos del día', 'm:formatos_dia'), *_menu_inicio()]]),
            editar=True
        )
        return

    await mostrar(update, f'⏳ Generando {nombre_formato} de hoy ({len(candidatos)} personas)…', editar=True)

    if clave == 'dia_evaluacion':
        contenido_doc = generar_html_evaluacion(candidatos, fecha_hoy)
        nombre_arch = f"evaluacion_induccion_{fecha_iso}.html"
        caption = (
            f"📝 Aquí tienes el documento oficial de **Evaluación de Inducción** ({fecha_hoy}).\n"
            f"Generado automáticamente con hojas individuales para los {len(candidatos)} ingresos del día.\n\n"
            f"💡 *Para guardar en PDF:* Abre el archivo y presiona el botón azul «Descargar / Guardar PDF»."
        )
    else:
        contenido_doc = generar_html_checklist(candidatos, fecha_hoy)
        nombre_arch = f"registro_induccion_{fecha_iso}.html"
        caption = (
            f"📋 Aquí tienes el documento oficial de **Registro de Inducción** ({fecha_hoy}).\n"
            f"Lista de asistencia con los {len(candidatos)} ingresos del día listos para firma.\n\n"
            f"💡 *Para guardar en PDF:* Abre el archivo y presiona el botón azul «Imprimir / Guardar PDF»."
        )

    await update.effective_message.reply_document(
        document=contenido_doc,
        filename=nombre_arch,
        caption=caption
    )

    filas = [[boton('⬅️ Formatos del día', 'm:formatos_dia'), *_menu_inicio()]]
    await mostrar(update, f'✅ **{nombre_formato} generado y enviado correctamente.**', InlineKeyboardMarkup(filas), editar=False)


async def accion_dia_excel(update, context, estado: EstadoUsuario) -> None:
    fecha_iso = hoy(context).strftime('%Y-%m-%d')
    fecha_humana = hoy(context).strftime('%d/%m/%Y')
    await _descargar(
        update,
        context,
        f'/api/exportar-ingresos-dia?fecha={fecha_iso}',
        None,
        f'ingresos_del_dia_{fecha_iso}.xlsx',
        volver_menu='m:formatos_dia',
        caption=f'📊 Aquí tienes el Excel oficial de **Ingresos del Día** ({fecha_humana}) con el formato del personal registrado.'
    )


# --- Subir Reporte de Inducción y Ver Historial --------------------------------------------

async def accion_rep_ind_hoy(update, context, estado: EstadoUsuario) -> None:
    backend, telegram_id = _backend(context), update.effective_user.id
    fecha_iso = hoy(context).strftime('%Y-%m-%d')
    fecha_humana = hoy(context).strftime('%d/%m/%Y')

    resp = await backend.peticion(telegram_id, 'GET', f'/api/registro-induccion/estado-hoy?fecha={fecha_iso}')
    datos = resp.datos or {}
    subido = bool(datos.get('subido'))
    filas = []

    if subido:
        reg_id = datos.get('id')
        nom_arch = datos.get('nombre_archivo') or 'registro_induccion.pdf'
        texto = (
            f"📋 *Reporte de Inducción — Hoy ({fecha_humana})*\n\n"
            f"✅ **Estado:** Documento subido y registrado\n"
            f"📄 **Archivo:** `{nom_arch}`\n\n"
            f"¿Qué acción deseas realizar?"
        )
        filas.append([boton('📥 Descargar documento', f'rep:dl:{reg_id}')])
        filas.append([boton('🗑️ Eliminar definitivamente', f'rep:del:{reg_id}')])
        filas.append([boton('📤 Reemplazar con nuevo archivo', f'rep:subir:{fecha_iso}')])
    else:
        texto = (
            f"📋 *Reporte de Inducción — Hoy ({fecha_humana})*\n\n"
            f"⚠️ **Estado:** Aún no se ha subido el reporte firmado del día de hoy.\n\n"
            f"Presiona «Subir documento» para adjuntar la fotografía o PDF del reporte firmado:"
        )
        filas.append([boton('📤 Subir documento', f'rep:subir:{fecha_iso}')])

    filas.append([boton('⬅️ Atrás', 'm:subir_reporte_induccion'), *_menu_inicio()])
    await mostrar(update, texto, InlineKeyboardMarkup(filas), editar=True)


async def accion_rep_ind_historial(update, context, estado: EstadoUsuario) -> None:
    backend, telegram_id = _backend(context), update.effective_user.id
    dia_actual = hoy(context)
    mes, anio = dia_actual.month, dia_actual.year

    resp = await backend.peticion(telegram_id, 'GET', f'/api/registro-induccion/historial-mes?mes={mes}&anio={anio}')
    datos = resp.datos or {}
    registros = datos.get('registros') or []

    lineas = [
        f"📆 *Historial de Reportes de Inducción — {MESES[mes - 1].capitalize()} {anio}*",
        "",
    ]
    if registros:
        lineas.append(f"Se tienen **{len(registros)}** fecha(s) con reporte registrado en este mes:")
        for r in registros[-8:]:
            lineas.append(f"• `{r['fecha']}`: {r.get('nombre_archivo', 'archivo')}")
    else:
        lineas.append("No hay reportes de inducción registrados en este mes todavía.")

    lineas.append("\nPara consultar el reporte de cualquier día, presiona el botón de abajo:")

    filas = [
        [boton('📅 Indicar Fecha', 'rep:pedir_fecha')],
        [boton('⬅️ Atrás', 'm:subir_reporte_induccion'), *_menu_inicio()]
    ]
    await mostrar(update, "\n".join(lineas), InlineKeyboardMarkup(filas), editar=True)


async def al_boton_reporte(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    consulta = update.callback_query
    await consulta.answer()
    telegram_id = consulta.from_user.id
    backend = _backend(context)
    estado = _estados(context).de(telegram_id)
    datos = consulta.data or ''

    if datos.startswith('rep:dl:'):
        reg_id = int(datos.removeprefix('rep:dl:'))
        await mostrar(update, '⏳ Descargando documento…', editar=True)
        respuesta = await backend.peticion(telegram_id, 'GET', f'/api/registro-induccion/archivo/{reg_id}', params={'descargar': '1'})
        if not respuesta.es_archivo:
            await mostrar(update, '⚠️ No se pudo obtener el archivo del servidor.', InlineKeyboardMarkup([_menu_inicio()]), editar=True)
            return
        await update.effective_message.reply_document(
            document=respuesta.contenido,
            filename=respuesta.nombre_archivo or f'registro_induccion_{reg_id}.pdf'
        )
        await mostrar(update, '📥 **Documento descargado con éxito.**', InlineKeyboardMarkup([[boton('⬅️ Historial', 'm:rep_ind_historial'), *_menu_inicio()]]), editar=True)
        return

    elif datos.startswith('rep:del:'):
        reg_id = int(datos.removeprefix('rep:del:'))
        await mostrar(update, '⏳ Eliminando documento…', editar=True)
        resp = await backend.peticion(telegram_id, 'DELETE', f'/api/registro-induccion/{reg_id}')
        if resp.ok or (200 <= resp.status < 300):
            await mostrar(update, '🗑️ **Reporte de Inducción eliminado definitivamente.**', InlineKeyboardMarkup([[boton('📆 Ver Historial', 'm:rep_ind_historial'), *_menu_inicio()]]), editar=True)
        else:
            await mostrar(update, '⚠️ Error al eliminar el reporte.', InlineKeyboardMarkup([_menu_inicio()]), editar=True)
        return

    elif datos.startswith('rep:subir:'):
        fecha = datos.removeprefix('rep:subir:')
        estado.esperando = f'subir_rep_ind:{fecha}'
        texto = (
            f"📤 *Subir Reporte de Inducción — Fecha: {fecha}*\n\n"
            f"Por favor envía el archivo como foto o documento PDF (completo y con buena luz).\n"
            f"Si deseas cancelar, pulsa el botón de abajo:"
        )
        filas = [[boton('❌ Cancelar', 'm:subir_reporte_induccion'), *_menu_inicio()]]
        await mostrar(update, texto, InlineKeyboardMarkup(filas), editar=True)
        return

    elif datos.startswith('rep:ver:'):
        fecha_iso = datos.removeprefix('rep:ver:')
        resp = await backend.peticion(telegram_id, 'GET', f'/api/registro-induccion/estado-hoy?fecha={fecha_iso}')
        st = resp.datos or {}
        filas = []
        if st.get('subido'):
            reg_id = st.get('id')
            nom = st.get('nombre_archivo', 'archivo')
            texto = (
                f"📋 *Reporte de Inducción — {fecha_iso}*\n\n"
                f"✅ **Estado:** Subido (`{nom}`)\n\n"
                f"Elige una acción:"
            )
            filas.append([boton('📥 Descargar', f'rep:dl:{reg_id}'), boton('🗑️ Eliminar', f'rep:del:{reg_id}')])
            filas.append([boton('📤 Reemplazar archivo', f'rep:subir:{fecha_iso}')])
        else:
            texto = (
                f"📋 *Reporte de Inducción — {fecha_iso}*\n\n"
                f"⚪ **Estado:** No hay reporte registrado para esta fecha.\n\n"
                f"¿Deseas subirlo ahora?"
            )
            filas.append([boton('📤 Subir documento', f'rep:subir:{fecha_iso}')])
        filas.append([boton('⬅️ Historial', 'm:rep_ind_historial'), *_menu_inicio()])
        await mostrar(update, texto, InlineKeyboardMarkup(filas), editar=True)
        return

    elif datos == 'rep:pedir_fecha':
        estado.esperando = 'indicar_fecha_rep_ind'
        texto = (
            f"✏️ *Indicar Fecha del Reporte*\n\n"
            f"Escribe la fecha que deseas consultar en formato `AAAA-MM-DD` o `DD/MM/AAAA` (ejemplo: `2026-11-15` o `15/11/2026`):"
        )
        filas = [[boton('❌ Cancelar', 'm:rep_ind_historial'), *_menu_inicio()]]
        await mostrar(update, texto, InlineKeyboardMarkup(filas), editar=True)
        return


# --- Ingresos del Mes ----------------------------------------------------------------------

async def accion_ingresos_mes(update, context, estado: EstadoUsuario) -> None:
    backend, telegram_id = _backend(context), update.effective_user.id
    dia = hoy(context)
    mes, anio = dia.month, dia.year

    resp = await backend.peticion(telegram_id, 'GET', '/api/avisos', params={'ingresos_mes': '1'})
    datos_avisos = resp.datos or {}
    ing_mes = datos_avisos.get('ingresos_mes') or {}

    total = ing_mes.get('total', 0)
    teams = ing_mes.get('teams') or []

    lineas = [
        f"📊 *Ingresos del Mes — {MESES[mes - 1].capitalize()} {anio}*",
        f"• **Total de personas registradas:** {total}",
        "",
        "*Reclutamiento activo por Team:*",
    ]
    if teams:
        for t in teams:
            lineas.append(f"  · Team {t.get('team')}: {t.get('total', 0)} personas")
    else:
        lineas.append("  · No hay registros asociados a teams aún este mes.")

    lineas.append("\n📥 Puedes descargar el listado completo con todos los empleados y medios:")
    filas = [
        [boton('📥 Descargar Excel Reclutamiento', 'm:dl_reclu')],
        [boton('👥 Reclutamiento Activo', 'm:team_reclu'), *_menu_inicio()]
    ]
    await mostrar(update, "\n".join(lineas), InlineKeyboardMarkup(filas), editar=True)


async def accion_generar_registro(update, context, estado: EstadoUsuario) -> None:
    backend, telegram_id = _backend(context), update.effective_user.id
    reclutadores: list[str] = []
    try:
        resp = await backend.peticion(telegram_id, 'GET', '/api/reclutadores-nombres')
        datos = resp.datos if isinstance(resp.datos, dict) else {}
        items = datos.get('reclutadores') or (resp.datos if isinstance(resp.datos, list) else [])
        reclutadores = [
            str(r.get('nombre') if isinstance(r, dict) else r).strip()
            for r in items if str(r).strip()
        ]
    except Exception:
        reclutadores = []
    estado.limpiar_menu()
    await registro_candidato.iniciar_registro(update, context, reclutadores)


async def accion_team_agregar(update, context, estado: EstadoUsuario) -> None:
    await iniciar_asistente(update, context, estado, formularios.asistente_team_agregar())


async def accion_team_modificar(update, context, estado: EstadoUsuario) -> None:
    backend, telegram_id = _backend(context), update.effective_user.id
    resp = await backend.peticion(telegram_id, 'GET', '/api/reclutadores-nombres')
    reclutadores = resp.datos if isinstance(resp.datos, list) else []
    if not reclutadores:
        await mostrar(update, 'No hay reclutadores registrados en el equipo.', InlineKeyboardMarkup([_menu_inicio()]), editar=True)
        return
    await iniciar_asistente(update, context, estado, formularios.asistente_team_modificar(reclutadores))


async def accion_team_eliminar(update, context, estado: EstadoUsuario) -> None:
    backend, telegram_id = _backend(context), update.effective_user.id
    resp = await backend.peticion(telegram_id, 'GET', '/api/reclutadores-nombres')
    reclutadores = resp.datos if isinstance(resp.datos, list) else []
    if not reclutadores:
        await mostrar(update, 'No hay reclutadores registrados en el equipo.', InlineKeyboardMarkup([_menu_inicio()]), editar=True)
        return
    await iniciar_asistente(update, context, estado, formularios.asistente_team_eliminar(reclutadores))


async def accion_dl_excel_mes(update, context, estado: EstadoUsuario) -> None:
    await _descargar(update, context, '/api/exportar-excel', None, 'ingresos_del_mes.xlsx')


async def accion_subir_papeleria(update, context, estado: EstadoUsuario) -> None:
    estado.esperando = 'subir_papeleria'
    await mostrar(
        update,
        '📎 *Subir Papelería*\n\nEscribe el N° de Empleado o TEMP-N° (o al menos 3 letras del nombre):',
        InlineKeyboardMarkup([_menu_inicio()]),
        editar=True,
    )


async def accion_formato_nuevo(update, context, estado: EstadoUsuario) -> None:
    clave = (update.callback_query.data or '').removeprefix('m:')
    tipo = 'Gafete' if 'gafete' in clave else 'Contrato Laboral'
    backend, telegram_id = _backend(context), update.effective_user.id
    resp = await backend.peticion(telegram_id, 'GET', '/api/obtener-pendientes')
    candidatos = resp.datos if isinstance(resp.datos, list) else []
    if not candidatos:
        await mostrar(update, f'⚡ *{tipo} — Nuevo Ingreso*\n\nNo hay ingresos pendientes.',
                      InlineKeyboardMarkup([_menu_inicio()]), editar=True)
        return
    estado.lista, estado.tipo_lista = candidatos, f'formato_nuevo:{clave}'
    filas = [
        [boton(f"{c.get('nombre_completo') or 'Sin nombre'} ({c.get('numero_temporal') or 'sin número'})", f'c:{i}')]
        for i, c in enumerate(candidatos[:POR_PAGINA])
    ]
    filas.append([boton('⬅️ Formatos', 'm:formatos_auto'), *_menu_inicio()])
    await mostrar(update, f'⚡ Elige el candidato para generar su {tipo}:', InlineKeyboardMarkup(filas), editar=True)


PAGINAS_FORMATOS = {
    'gafete_existente': ('Gafete', 'documentos/maingafette.html'),
    'cartacom_buscar': ('Carta Compromiso', 'documentos/cartacom.html'),
    'cartaimss_buscar': ('Carta Patronal IMSS', 'documentos/cartaimss.html'),
    'contrato_existente': ('Contrato Laboral', 'documentos/contrato.html'),
}


async def accion_buscar_para_formato(update, context, estado: EstadoUsuario) -> None:
    clave = (update.callback_query.data or '').removeprefix('m:')
    titulo, _ = PAGINAS_FORMATOS.get(clave, ('Formato', ''))
    estado.esperando = f'buscar_formato:{clave}'
    await mostrar(
        update,
        f'⚡ *{titulo}*\n\nEscribe el N° de Empleado o al menos 3 letras del nombre:',
        InlineKeyboardMarkup([[boton('⬅️ Formatos', 'm:formatos_auto'), *_menu_inicio()]]),
        editar=True,
    )


async def buscar_para_subir_papeleria(update: Update, context, estado: EstadoUsuario, texto: str) -> None:
    params = parametros_busqueda(texto)
    if params is None:
        await responder(update, 'Escribe un número de empleado o al menos 3 letras del nombre.')
        return
    telegram_id = update.effective_user.id
    respuesta = await _backend(context).peticion(telegram_id, 'GET', '/api/empleados/buscar', params=params)
    empleados = list((respuesta.datos or {}).get('empleados') or [])
    if not empleados:
        resp_pend = await _backend(context).peticion(telegram_id, 'GET', '/api/obtener-pendientes')
        candidatos = resp_pend.datos if isinstance(resp_pend.datos, list) else []
        t_clean = texto.strip().upper()
        matching = [
            c for c in candidatos
            if t_clean in str(c.get('numero_temporal') or '').upper() or t_clean in str(c.get('nombre_completo') or '').upper()
        ]
        if matching:
            empleados = matching
    if not empleados:
        await responder(update, 'No encontré empleados o candidatos con ese dato. Escribe otro número o nombre.',
                        InlineKeyboardMarkup([_menu_inicio()]))
        return
    estado.esperando = None
    if len(empleados) == 1:
        await iniciar_asistente(update, context, estado, formularios.asistente_subir_papeleria(empleados[0]))
        return
    filas = [
        [boton(f'{e.get("no_empleado") or e.get("numero_temporal") or "s/n"} · {e.get("nombre_completo") or "Sin nombre"}', f'e:{i}')]
        for i, e in enumerate(empleados[:8])
    ]
    filas.append([boton('⬅️ Cancelar', 'm:subir_papeleria'), *_menu_inicio()])
    estado.lista, estado.tipo_lista = empleados, 'empleados'
    await responder(update, f'Encontré {len(empleados)} coincidencias. Elige una:', InlineKeyboardMarkup(filas))


async def buscar_para_formato_resultado(update: Update, context, estado: EstadoUsuario, texto: str) -> None:
    clave = str(estado.esperando).removeprefix('buscar_formato:')
    params = parametros_busqueda(texto)
    if params is None:
        await responder(update, 'Escribe un número de empleado o al menos 3 letras del nombre.')
        return
    telegram_id = update.effective_user.id
    respuesta = await _backend(context).peticion(telegram_id, 'GET', '/api/empleados/buscar', params=params)
    empleados = list((respuesta.datos or {}).get('empleados') or [])
    if not empleados:
        await responder(update, 'No encontré empleados con ese dato. Intenta con otro número o nombre.',
                        InlineKeyboardMarkup([_menu_inicio()]))
        return
    estado.esperando = None
    if len(empleados) == 1:
        await mostrar_formato_empleado(update, context, estado, empleados[0], clave)
        return
    filas = [
        [boton(f'{e.get("no_empleado") or "s/n"} · {e.get("nombre_completo") or "Sin nombre"}', f'e:{i}')]
        for i, e in enumerate(empleados[:8])
    ]
    filas.append([boton('⬅️ Formatos', 'm:formatos_auto'), *_menu_inicio()])
    estado.lista, estado.tipo_lista = empleados, 'empleados'
    await responder(update, f'Encontré {len(empleados)} empleados. Elige uno:', InlineKeyboardMarkup(filas))


async def mostrar_formato_empleado(update, context, estado, empleado: dict, clave: str) -> None:
    titulo, pagina = PAGINAS_FORMATOS.get(clave, ('Formato', 'documentos/cartacom.html'))
    rid = str(empleado.get('registro_id') or empleado.get('id') or '')
    nombre = empleado.get('nombre_completo') or 'Sin nombre'
    no_emp = str(empleado.get('no_empleado') or empleado.get('numero_temporal') or 's/n').strip()

    # Validación de Personal Temporal: no pueden generar documentos oficiales hasta número definitivo
    if no_emp.upper().startswith('TEMP') or 'TEMP-' in no_emp.upper() or 'TEMP -' in no_emp.upper():
        texto_error_temp = (
            f'⚠️ *No es posible generar {titulo}*\n\n'
            f'El personal **{nombre}** cuenta con número temporal (`{no_emp}`).\n\n'
            f'Personal que tenga número de empleado Temporal no podrá generar ningún documento, '
            f'hasta que se haya modificado con un número definitivo que no tenga el `TEMP -`.'
        )
        filas = [[boton('⬅️ Formatos', 'm:formatos_auto'), *_menu_inicio()]]
        await responder(update, texto_error_temp, InlineKeyboardMarkup(filas))
        return

    texto = (
        f'⚡ *{titulo} — {nombre}*\n\n'
        f'• Empleado: {nombre} ({no_emp})\n'
        f'• Empresa: {empleado.get("empresa") or "Sin empresa"}\n'
        f'• Puesto: {empleado.get("puesto") or "Sin puesto"}\n'
        f'• Registro ID: `{rid}`\n\n'
        f'Ruta para generar o imprimir en el panel web:\n`{pagina}?registro_id={rid}`'
    )
    filas = [[boton('⬅️ Formatos', 'm:formatos_auto'), *_menu_inicio()]]
    await responder(update, texto, InlineKeyboardMarkup(filas))


ACCIONES = {
    # 1. Eventos
    'ev_cumple': accion_ev_cumple,
    'ev_general': accion_ev_general,

    # 2. Asignar Acceso
    'acc_nuevo': accion_acc_nuevo,
    'acc_editar': accion_acc_editar,
    'acc_eliminar': accion_acc_eliminar,

    # 3. Ingresos Pendientes
    'ingresos_pendientes': accion_ingresos_pendientes,

    # 4. Formatos Automatizados
    'gafete_nuevo': accion_formato_nuevo,
    'gafete_existente': accion_buscar_para_formato,
    'cartacom_buscar': accion_buscar_para_formato,
    'cartaimss_buscar': accion_buscar_para_formato,
    'contrato_nuevo': accion_formato_nuevo,
    'contrato_existente': accion_buscar_para_formato,

    # 5. Formatos para Imprimir
    'print_suspension': accion_imprimir,
    'print_entradasalida': accion_imprimir,
    'print_indisciplina': accion_imprimir,

    # 6. Formatos del día y Reporte de Inducción
    'dia_evaluacion': accion_formatos_dia,
    'dia_registro': accion_formatos_dia,
    'dia_excel': accion_dia_excel,
    'rep_ind_hoy': accion_rep_ind_hoy,
    'rep_ind_historial': accion_rep_ind_historial,

    # 7. Listado General
    'dl_cw': accion_dl_empresa,
    'dl_kronos': accion_dl_empresa,
    'dl_permisos': accion_dl_permisos,

    # 8. Generar Registro
    'generar_registro': accion_generar_registro,

    # 9. Reclutamiento Activo (Personal Activo por Team)
    'team_agregar': accion_team_agregar,
    'team_modificar': accion_team_modificar,
    'team_eliminar': accion_team_eliminar,

    # 10. Descargar Excel
    'dl_excel_mes': accion_dl_excel_mes,

    # 11. Subir Papelería
    'subir_papeleria': accion_subir_papeleria,

    # 12. Seguridad
    'seg_salidas': lambda u, c, _e: menu_seguridad.mostrar_lista(u, _backend(c), 'salidas'),
    'seg_entradas': lambda u, c, _e: menu_seguridad.mostrar_lista(u, _backend(c), 'entradas'),
    'seg_suspendidos': lambda u, c, _e: menu_seguridad.mostrar_lista(u, _backend(c), 'suspendidos'),
    'seg_escanear_qr': lambda u, _c, _e: menu_seguridad.iniciar_escaner_qr(u),

    # Compatibilidad con acciones base
    'buscar_empleado': accion_buscar_empleado,
    'pendientes': accion_pendientes,
    'ind_mes': accion_ind_mes,
    'ind_team': accion_ind_team,
    'dl_reclu': accion_dl_reclu,
}


# --- Formularios ----------------------------------------------------------------------------

def teclado_asistente(asistente: Asistente) -> InlineKeyboardMarkup:
    opciones = asistente.opciones()
    filas, fila = [], []
    for i, opcion in enumerate(opciones):
        fila.append(boton(opcion, f'a:{i}'))
        if len(fila) == 2:
            filas.append(fila)
            fila = []
    if fila:
        filas.append(fila)
    paso = asistente.paso
    if paso is not None and paso.omitible:
        filas.append([boton('Omitir', 'a:s')])
    control = [boton('✖️ Cancelar', 'a:x')]
    if asistente.historial:
        control.insert(0, boton('⬅️ Atrás', 'a:b'))
    filas.append(control)
    return InlineKeyboardMarkup(filas)


def texto_paso(asistente: Asistente, aviso: str | None = None) -> str:
    actual, total = asistente.numero_de_paso()
    paso = asistente.paso
    preg = paso.pregunta if paso else ''
    texto_preg = preg(asistente.respuestas) if callable(preg) else preg
    texto = f'{asistente.titulo}\nPaso {actual} de {total}\n\n{texto_preg}'
    if not aviso:
        return texto
    if aviso.startswith(('⚠️', '✅', 'ℹ️', '📋')):
        return f'{aviso}\n\n{texto}'
    return f'⚠️ {aviso}\n\n{texto}'


async def iniciar_asistente(update, context, estado: EstadoUsuario, asistente: Asistente) -> None:
    estado.asistente = asistente
    estado.esperando = None
    await mostrar(update, texto_paso(asistente), teclado_asistente(asistente), editar=True)


async def proponer(update, estado: EstadoUsuario, pendiente: CambioPendiente, *, editar=True) -> None:
    """Deja el cambio esperando el botón Confirmar (uno por persona)."""
    async with estado.candado:
        estado.pendiente = pendiente
    await mostrar(update, pendiente.resumen(), teclado_confirmar(pendiente), editar=editar)


async def avanzar_asistente(update, estado: EstadoUsuario, *, editar: bool, aviso: str | None = None) -> None:
    asistente = estado.asistente
    if asistente.terminado:
        estado.asistente = None
        try:
            pendiente = asistente.construir()
            if inspect.isawaitable(pendiente):
                await mostrar(update, '🔍 Leyendo el documento…', editar=editar)
                editar = False
                pendiente = await pendiente
        except ValueError as error:
            await mostrar(update, f'⚠️ {error}', InlineKeyboardMarkup([_menu_inicio()]), editar=editar)
            return
        except ErrorBackend as error:
            await mostrar(update, texto_error(error), InlineKeyboardMarkup([_menu_inicio()]), editar=editar)
            return
        await proponer(update, estado, pendiente, editar=editar)
        return
    await mostrar(update, texto_paso(asistente, aviso), teclado_asistente(asistente), editar=editar)


async def al_boton_asistente(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    consulta = update.callback_query
    await consulta.answer()
    estado = _estados(context).de(consulta.from_user.id)
    asistente = estado.asistente
    if asistente is None:
        await mostrar(update, CADUCADO, editar=True)
        return
    valor = (consulta.data or '').removeprefix('a:')
    aviso = None
    try:
        if valor == 'x':
            estado.asistente = None
            await mostrar(update, '✖️ Formulario cancelado.', InlineKeyboardMarkup([_menu_inicio()]), editar=True)
            return
        if valor == 'b':
            asistente.atras()
        elif valor == 's':
            asistente.omitir()
        else:
            asistente.elegir(int(valor))
    except ValueError as error:
        aviso = str(error)
    await avanzar_asistente(update, estado, editar=True, aviso=aviso)


async def texto_asistente(update: Update, estado: EstadoUsuario, texto: str) -> None:
    paso = estado.asistente.paso
    if paso.tipo == 'opciones':
        await responder(update, 'Elige una opción con los botones del mensaje anterior.')
        return
    if paso.tipo == 'archivo':
        await responder(update, 'Envía el documento como foto o PDF, o pulsa «Omitir».')
        return
    aviso = None
    try:
        estado.asistente.responder(texto)
    except ValueError as error:
        aviso = str(error)
    await avanzar_asistente(update, estado, editar=False, aviso=aviso)


async def archivo_asistente(update: Update, context, estado: EstadoUsuario) -> None:
    mensaje = update.effective_message
    if mensaje.photo:
        foto = mensaje.photo[-1]
        nombre, tamano, file_id = 'foto.jpg', foto.file_size or 0, foto.file_id
    else:
        documento = mensaje.document
        nombre, tamano, file_id = documento.file_name or 'documento', documento.file_size or 0, documento.file_id
    aviso = None
    try:
        if tamano > formularios.MAX_ARCHIVO:
            raise ValueError('El archivo no puede pasar de 10 MB.')
        formularios.validar_archivo(nombre, b'')  # tipo antes de descargar
        archivo = await context.bot.get_file(file_id)
        contenido = bytes(await archivo.download_as_bytearray())
        archivo_valido = formularios.validar_archivo(nombre, contenido)

        paso_actual = estado.asistente.paso
        escanear_tipo = getattr(paso_actual, 'escanear_como', None) if paso_actual else None
        if escanear_tipo:
            await responder(update, f'🔍 Analizando imagen ({escanear_tipo})…')
            tipo_doc = documentos.tipo_por_texto(escanear_tipo)
            leidos = await documentos.escanear(_backend(context), update.effective_user.id, tipo_doc, archivo_valido)
            if not leidos:
                raise ValueError(
                    f'No fue posible leer datos de «{escanear_tipo}». '
                    'Verifica que la foto esté completa, con buena luz y nítida, o pulsa «Omitir».'
                )
            estado.asistente.responder({'archivo': archivo_valido, 'datos': leidos})
            aviso = formularios.formatear_resultado_escaneo(escanear_tipo, leidos, estado.asistente.respuestas)
        else:
            estado.asistente.responder(archivo_valido)
    except ValueError as error:
        aviso = f'⚠️ {error}'
    await avanzar_asistente(update, estado, editar=False, aviso=aviso)


# --- Entrada libre (la enruta principal.al_texto / al_archivo) -----------------------------

async def atender_texto(update: Update, context, texto: str) -> bool:
    """True si el menú consumió el texto."""
    estado = _estados(context).de(update.effective_user.id)
    try:
        if estado.asistente is not None:
            await texto_asistente(update, estado, texto)
            return True
        if estado.esperando == 'buscar':
            await buscar_texto(update, context, estado, texto)
            return True
        if estado.esperando == 'subir_papeleria':
            await buscar_para_subir_papeleria(update, context, estado, texto)
            return True
        if estado.esperando and str(estado.esperando).startswith('buscar_formato:'):
            await buscar_para_formato_resultado(update, context, estado, texto)
            return True
        if estado.esperando == 'indicar_fecha_rep_ind':
            # Normalizar fecha
            t = texto.strip()
            fecha_iso = None
            for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%Y/%m/%d'):
                try:
                    fecha_iso = datetime.strptime(t, fmt).strftime('%Y-%m-%d')
                    break
                except ValueError:
                    pass
            if not fecha_iso:
                await responder(update, '⚠️ Formato de fecha no válido. Escribe en formato `AAAA-MM-DD` o `DD/MM/AAAA` (ej. `2026-11-15`):')
                return True
            estado.esperando = None
            backend, telegram_id = _backend(context), update.effective_user.id
            resp = await backend.peticion(telegram_id, 'GET', f'/api/registro-induccion/estado-hoy?fecha={fecha_iso}')
            st = resp.datos or {}
            filas = []
            if st.get('subido'):
                reg_id = st.get('id')
                nom = st.get('nombre_archivo', 'archivo')
                txt = (
                    f"📋 *Reporte de Inducción — {fecha_iso}*\n\n"
                    f"✅ **Estado:** Documento subido y registrado (`{nom}`)\n\n"
                    f"Elige una acción:"
                )
                filas.append([boton('📥 Descargar', f'rep:dl:{reg_id}'), boton('🗑️ Eliminar definitivamente', f'rep:del:{reg_id}')])
                filas.append([boton('📤 Reemplazar archivo', f'rep:subir:{fecha_iso}')])
            else:
                txt = (
                    f"📋 *Reporte de Inducción — {fecha_iso}*\n\n"
                    f"⚪ **Estado:** No se detectó ningún documento para esta fecha.\n\n"
                    f"¿Deseas subirlo ahora?"
                )
                filas.append([boton('📤 Subir documento', f'rep:subir:{fecha_iso}')])
            filas.append([boton('📅 Indicar otra fecha', 'rep:pedir_fecha')])
            filas.append([boton('⬅️ Historial', 'm:rep_ind_historial'), *_menu_inicio()])
            await responder(update, txt, InlineKeyboardMarkup(filas))
            return True
    except ErrorBackend as error:
        await responder(update, texto_error(error))
        return True
    return False


async def atender_archivo(update: Update, context) -> bool:
    estado = _estados(context).de(update.effective_user.id)
    if estado.esperando and str(estado.esperando).startswith('subir_rep_ind:'):
        fecha = str(estado.esperando).removeprefix('subir_rep_ind:')
        mensaje = update.effective_message
        if mensaje.photo:
            foto = mensaje.photo[-1]
            nombre, file_id = f"reporte_induccion_{fecha}.jpg", foto.file_id
            mime = 'image/jpeg'
        elif mensaje.document:
            documento = mensaje.document
            nombre = documento.file_name or f"reporte_induccion_{fecha}.pdf"
            file_id = documento.file_id
            mime = documento.mime_type or ('application/pdf' if nombre.lower().endswith('.pdf') else 'application/octet-stream')
        else:
            await responder(update, '⚠️ Envía una fotografía o un archivo PDF del documento.')
            return True

        await responder(update, f'⏳ Subiendo reporte de inducción ({fecha})…')
        try:
            archivo_desc = await context.bot.get_file(file_id)
            contenido = bytes(await archivo_desc.download_as_bytearray())
            backend, telegram_id = _backend(context), update.effective_user.id
            resp = await backend.peticion(
                telegram_id,
                'POST',
                '/api/registro-induccion/subir',
                data={'fecha': fecha},
                files=[('file', (nombre, contenido, mime))]
            )
            estado.esperando = None
            if resp.ok or (200 <= resp.status < 300):
                await responder(
                    update,
                    f"✅ **Reporte de Inducción guardado exitosamente para la fecha {fecha}.**\n"
                    "En la página web ahora se refleja como subido correctamente.",
                    InlineKeyboardMarkup([[boton('📄 Subir Inducción', 'm:subir_reporte_induccion'), *_menu_inicio()]])
                )
            else:
                await responder(update, f"⚠️ Error al subir el reporte: {resp.datos or 'Sin detalle'}")
        except Exception as exc:
            await responder(update, f"⚠️ Error al procesar archivo: {exc}")
        return True

    if estado.asistente is not None:
        if estado.asistente.paso is not None and estado.asistente.paso.tipo == 'archivo':
            await archivo_asistente(update, context, estado)
        else:
            await responder(update, 'En este paso del formulario se espera texto o una opción, no un archivo. Responde a la pregunta o usa /cancelar.')
        return True
    return False
