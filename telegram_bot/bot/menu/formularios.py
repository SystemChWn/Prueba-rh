"""Formularios del menú. Cada uno termina en un ``CambioPendiente``.

Los nombres de campo son los que manda el panel; ``tests/test_menu_formularios``
los compara con ``frontend/Ingreso.html`` y ``frontend/empleado.html``.
"""

import json
import re
import secrets
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from bot.borrador import Archivo, CambioPendiente
from bot.menu.asistente import Asistente, Paso, Respuestas, formatear_fecha

MAX_SALARIO = 50
MAX_MOTIVO = 1000
MAX_ARCHIVO = 10 * 1024 * 1024
TIPOS_ARCHIVO = {'.pdf': 'application/pdf', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg'}


def _dato(valor: Any, defecto: str = 'Sin dato') -> str:
    return str(valor).strip() if valor not in (None, '') else defecto


# --- Asignar puesto (Ingreso.html) ---------------------------------------------

def normalizar_numero_temporal(valor: Any) -> str:
    """Igual que ``normalizarNumeroTemporal`` de Ingreso.html."""
    texto = str(valor or '').strip()
    if not texto:
        return ''
    if re.match(r'^TEMP\s*-', texto, re.IGNORECASE):
        return re.sub(r'^TEMP\s*-\s*', 'TEMP - ', texto, flags=re.IGNORECASE)
    return f'TEMP - {texto}'


def _validar_salario(valor: Any, _r: Respuestas) -> str:
    texto = ' '.join(str(valor or '').split())
    if not texto:
        raise ValueError('Escribe el salario.')
    if len(texto) > MAX_SALARIO:
        raise ValueError(f'El salario no puede pasar de {MAX_SALARIO} caracteres.')
    return texto


def pendiente_ingreso(candidato: dict[str, Any], catalogo: dict[str, Any], r: Respuestas,
                      origen: str = 'menu') -> CambioPendiente:
    """Igual que ``construirFilaPayload`` de Ingreso.html."""
    nombre = _dato(candidato.get('nombre_completo'), '')
    no_empleado = normalizar_numero_temporal(candidato.get('numero_temporal'))
    if not nombre or not no_empleado:
        raise ValueError('A este candidato le falta el nombre o el número temporal; asígnalo desde el panel.')
    cuerpo = {
        'nombre_candidato': nombre,
        'empresa': r['empresa'],
        'no_empleado': no_empleado,
        'personal': catalogo.get('personal') or 'OPERATIVO',
        'puesto': r['puesto'],
        'area': r['area'],
        'turno': r['turno'],
        'salario': r['salario'],
        'pago': r['pago'],
        'registro_id': str(int(candidato['id'])),
    }
    return CambioPendiente(
        'POST', '/api/guardar-ingreso',
        f'📝 Asignar puesto a {nombre}:',
        detalle=[
            f'Número: {no_empleado}',
            f'Empresa: {cuerpo["empresa"]}',
            f'Área: {cuerpo["area"]}',
            f'Puesto: {cuerpo["puesto"]}',
            f'Turno: {cuerpo["turno"]}',
            f'Salario: {cuerpo["salario"]}',
            f'Pago: {cuerpo["pago"]}',
        ],
        cuerpo=cuerpo,
        origen=origen,
    )


def asistente_ingreso(candidato: dict[str, Any], catalogo: dict[str, Any]) -> Asistente:
    nombre = _dato(candidato.get('nombre_completo'), '')
    if not nombre or not normalizar_numero_temporal(candidato.get('numero_temporal')):
        raise ValueError('A este candidato le falta el nombre o el número temporal; asígnalo desde el panel.')
    puestos = catalogo.get('puestos_por_area') or {}

    def terminar(r: Respuestas) -> CambioPendiente:
        return pendiente_ingreso(candidato, catalogo, r)

    return Asistente(
        titulo=f'Asignar puesto — {nombre}',
        pasos=[
            Paso('empresa', 'Empresa:', opciones=lambda _r: catalogo.get('empresas') or []),
            Paso('area', 'Área:', opciones=lambda _r: catalogo.get('areas') or []),
            Paso('puesto', 'Puesto:', opciones=lambda r: puestos.get(r.get('area'), [])),
            Paso('turno', 'Turno:', opciones=lambda _r: catalogo.get('turnos') or []),
            Paso('salario', 'Escribe el salario (tal como en el panel):', tipo='texto', validar=_validar_salario),
            Paso('pago', 'Esquema de pago:', opciones=lambda _r: catalogo.get('pagos') or []),
        ],
        al_terminar=terminar,
    )


# --- Incidencias (empleado.html) ---------------------------------------------

#: Etiqueta del botón -> (tipo del backend, título del panel)
TIPOS_INCIDENCIA = {
    'Vacaciones': ('vacaciones', 'Vacaciones'),
    'Incapacidad': ('incapacidad', 'Incapacidad'),
    'Suspensión': ('suspension', 'Suspensión'),
    'Carta compromiso': ('carta-suspension', 'Carta Compromiso'),
}
CON_MOTIVO = {'suspension', 'carta-suspension'}


def _tipo(r: Respuestas) -> str:
    return TIPOS_INCIDENCIA[r['tipo']][0]


def _validar_motivo(valor: Any, _r: Respuestas) -> str:
    texto = str(valor or '').strip()
    if not texto:
        raise ValueError('Escribe el motivo.')
    if len(texto) > MAX_MOTIVO:
        raise ValueError(f'El motivo no puede pasar de {MAX_MOTIVO} caracteres.')
    return texto


def _validar_hasta(valor: date, r: Respuestas) -> date:
    desde = r['desde']
    if valor < desde:
        raise ValueError('La fecha final no puede ser anterior a la inicial.')
    if (valor - desde).days + 1 > 999:
        raise ValueError('El periodo no puede pasar de 999 días.')
    return valor


def validar_archivo(nombre: str, contenido: bytes) -> Archivo:
    extension = ('.' + nombre.rsplit('.', 1)[-1].lower()) if '.' in nombre else ''
    if extension not in TIPOS_ARCHIVO:
        raise ValueError('El archivo debe ser PDF, PNG o JPG.')
    if len(contenido) > MAX_ARCHIVO:
        raise ValueError('El archivo no puede pasar de 10 MB.')
    return Archivo('archivo', nombre, contenido, TIPOS_ARCHIVO[extension])


#: Tipo del backend -> título del panel.
TITULOS_INCIDENCIA = {tipo: titulo for tipo, titulo in TIPOS_INCIDENCIA.values()}


def pendiente_incidencia(empleado: dict[str, Any], tipo: str, desde: date, hasta: date, motivo: str | None = None,
                         archivo: Archivo | None = None, origen: str = 'menu') -> CambioPendiente:
    """Igual que el formulario de incidencias de empleado.html (de fecha / a fecha)."""
    if tipo not in TITULOS_INCIDENCIA:
        raise ValueError(f'Tipo de incidencia no válido: {tipo}.')
    titulo = TITULOS_INCIDENCIA[tipo]
    hasta = _validar_hasta(hasta, {'desde': desde})
    if tipo in CON_MOTIVO:
        motivo = _validar_motivo(motivo, {})
    registro_id = int(empleado['registro_id'])
    nombre = _dato(empleado.get('nombre_completo'), 'Sin nombre')
    # El panel manda el día de reingreso (el siguiente al último) en suspensiones.
    reingreso = hasta + timedelta(days=1) if tipo in CON_MOTIVO else hasta
    dias = (hasta - desde).days + 1
    motivo = (motivo or '').strip() or titulo
    detalles = {'dias': dias, 'opcion': '', 'hora': '', 'motivos': [], 'otro_motivo': '', 'categoria': ''}
    formulario = {
        'tipo': tipo,
        'motivo': motivo,
        'fecha_inicio': desde.isoformat(),
        'fecha_reingreso': reingreso.isoformat(),
        'detalles': json.dumps(detalles, ensure_ascii=False),
    }
    detalle = [
        f'Empleado: {nombre} ({_dato(empleado.get("no_empleado"), "sin número")})',
        f'Tipo: {titulo}',
        f'Del {formatear_fecha(desde)} al {formatear_fecha(hasta)} ({dias} día{"s" if dias != 1 else ""})',
    ]
    if tipo in CON_MOTIVO:
        detalle.append(f'Reingresa: {formatear_fecha(reingreso)}')
        detalle.append(f'Motivo: {motivo}')
    return CambioPendiente(
        'POST', f'/api/empleados/{registro_id}/incidencias',
        '🗓 Registrar incidencia:',
        detalle=detalle,
        formulario=formulario,
        archivo=archivo,
        origen=origen,
    )


def asistente_incidencia(empleado: dict[str, Any], hoy: Callable[[], date] = date.today) -> Asistente:
    nombre = _dato(empleado.get('nombre_completo'), 'Sin nombre')

    def terminar(r: Respuestas) -> CambioPendiente:
        return pendiente_incidencia(empleado, TIPOS_INCIDENCIA[r['tipo']][0], r['desde'], r['hasta'],
                                    r.get('motivo'), r.get('archivo'))

    return Asistente(
        titulo=f'Incidencia — {nombre}',
        pasos=[
            Paso('tipo', 'Tipo de incidencia:', opciones=lambda _r: list(TIPOS_INCIDENCIA)),
            Paso('motivo', 'Escribe el motivo:', tipo='texto', validar=_validar_motivo,
                 aplica=lambda r: 'tipo' in r and _tipo(r) in CON_MOTIVO),
            Paso('desde', 'Primer día (DD/MM/AAAA):', tipo='fecha'),
            Paso('hasta', 'Último día (DD/MM/AAAA):', tipo='fecha', validar=_validar_hasta),
            Paso('archivo', 'Si tienes el documento firmado, envíalo como foto o PDF; si no, pulsa Omitir.',
                 tipo='archivo', omitible=True,
                 aplica=lambda r: 'tipo' in r and _tipo(r) in CON_MOTIVO),
        ],
        al_terminar=terminar,
        hoy=hoy,
    )


def asistente_documento(empleado: dict[str, Any], leer: Callable) -> Asistente:
    """Tipo de documento y archivo; ``leer(tipo, archivo)`` (async) arma el cambio con lo leído."""
    from bot import documentos

    nombre = _dato(empleado.get('nombre_completo'), 'Sin nombre')
    nombres = {t.nombre: t for t in documentos.TIPOS.values()}

    def validar(archivo: Archivo, _r: Respuestas) -> Archivo:
        documentos.validar(archivo)
        return archivo

    return Asistente(
        titulo=f'Actualizar con documento — {nombre}',
        pasos=[
            Paso('tipo', '¿Qué documento es?', opciones=lambda _r: list(nombres)),
            Paso('archivo', 'Envía el documento como foto o PDF (completo, con buena luz).', tipo='archivo',
                 validar=validar),
        ],
        # Devuelve una corrutina: la lectura es asíncrona (pantallas la espera).
        al_terminar=lambda r: leer(nombres[r['tipo']], r['archivo']),
    )


# --- Baja / reactivación y eliminación -----------------------------------------------

def pendiente_estatus(empleado: dict[str, Any], origen: str = 'menu') -> CambioPendiente:
    """Igual que el botón de estatus de los listados: solo manda ``estatus``."""
    activo = str(empleado.get('estatus') or 'ACTIVO').upper() == 'ACTIVO'
    nuevo = 'INACTIVO' if activo else 'ACTIVO'
    return CambioPendiente(
        'POST', f'/api/actualizar-empleado/{int(empleado["registro_id"])}',
        '⏸ Dar de baja (estatus INACTIVO):' if activo else '▶️ Reactivar (estatus ACTIVO):',
        detalle=[
            f'Nombre: {_dato(empleado.get("nombre_completo"), "Sin nombre")}',
            f'Número: {_dato(empleado.get("no_empleado"), "Sin asignar")}',
            f'Empresa: {_dato(empleado.get("empresa"))}',
        ],
        cuerpo={'estatus': nuevo},
        origen=origen,
    )


def pendiente_eliminar(empleado: dict[str, Any], origen: str = 'menu') -> CambioPendiente:
    return CambioPendiente(
        'DELETE', f'/api/eliminar-empleado/{int(empleado["registro_id"])}',
        '🗑 Eliminar definitivamente este empleado y todos sus datos:',
        detalle=[
            f'Nombre: {_dato(empleado.get("nombre_completo"), "Sin nombre")}',
            f'Número: {_dato(empleado.get("no_empleado"), "Sin asignar")}',
            f'Empresa: {_dato(empleado.get("empresa"))}',
            f'Puesto: {_dato(empleado.get("puesto"))}',
        ],
        origen=origen,
    )


# --- Eventos compartidos (Crear Nuevo Evento) ----------------------------------------

MESES_NOMBRES = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']


def asistente_cumpleanos(eventos_actuales: list[dict], hoy: Callable[[], date] = date.today) -> Asistente:
    def validar_dia(valor: Any, _r: Respuestas) -> str:
        try:
            d = int(str(valor).strip())
            if not 1 <= d <= 31:
                raise ValueError
            return f'{d:02d}'
        except Exception:
            raise ValueError('El día debe ser un número entre 1 y 31.')

    def terminar(r: Respuestas) -> CambioPendiente:
        mes_idx = MESES_NOMBRES.index(r['mes']) + 1
        month_day = f"{r['dia']}-{mes_idx:02d}"
        nombre = str(r['nombre']).strip()
        nuevo_evento = {
            'id': f"evt_{secrets.token_hex(4)}",
            'title': nombre,
            'monthDay': month_day,
            'startTime': '',
            'endTime': '',
            'asistentes': '',
            'tipo': 'cumpleanos',
            'descripcion': '',
        }
        eventos = [*eventos_actuales, nuevo_evento]
        return CambioPendiente(
            'POST', '/api/shared-events',
            f'🎂 Registrar Cumpleaños de {nombre}:',
            detalle=[
                f'Cumpleañero: {nombre}',
                f'Fecha: {r["dia"]} de {r["mes"]} ({month_day})',
                'Tipo: Cumpleaños',
            ],
            cuerpo={'eventos': eventos},
        )

    return Asistente(
        titulo='🎂 Crear Cumpleaños',
        pasos=[
            Paso('nombre', 'Nombre del cumpleañero:', tipo='texto',
                 validar=lambda v, _r: str(v).strip() if str(v).strip() else (_ for _ in ()).throw(ValueError('Escribe el nombre.'))),
            Paso('dia', 'Día del mes (1 a 31):', tipo='texto', validar=validar_dia),
            Paso('mes', 'Mes:', opciones=lambda _r: list(MESES_NOMBRES)),
        ],
        al_terminar=terminar,
        hoy=hoy,
    )


def asistente_evento(eventos_actuales: list[dict], hoy: Callable[[], date] = date.today) -> Asistente:
    def validar_dia(valor: Any, _r: Respuestas) -> str:
        try:
            d = int(str(valor).strip())
            if not 1 <= d <= 31:
                raise ValueError
            return f'{d:02d}'
        except Exception:
            raise ValueError('El día debe ser un número entre 1 y 31.')

    def validar_hora(valor: Any, _r: Respuestas) -> str:
        h = str(valor or '').strip()
        if not re.match(r'^\d{2}:\d{2}$', h):
            raise ValueError('La hora debe tener formato HH:MM (ej. 09:00 o 14:30).')
        return h

    def terminar(r: Respuestas) -> CambioPendiente:
        mes_idx = MESES_NOMBRES.index(r['mes']) + 1
        month_day = f"{r['dia']}-{mes_idx:02d}"
        titulo = str(r['titulo']).strip()
        nuevo_evento = {
            'id': f"evt_{secrets.token_hex(4)}",
            'title': titulo,
            'monthDay': month_day,
            'startTime': r['hora_inicio'],
            'endTime': r['hora_fin'],
            'asistentes': '',
            'tipo': 'evento',
            'descripcion': '',
        }
        eventos = [*eventos_actuales, nuevo_evento]
        return CambioPendiente(
            'POST', '/api/shared-events',
            f'📌 Registrar Evento: {titulo}',
            detalle=[
                f'Título: {titulo}',
                f'Fecha: {r["dia"]} de {r["mes"]} ({month_day})',
                f'Horario: {r["hora_inicio"]} a {r["hora_fin"]}',
                'Tipo: Evento',
            ],
            cuerpo={'eventos': eventos},
        )

    return Asistente(
        titulo='📌 Crear Nuevo Evento',
        pasos=[
            Paso('titulo', 'Título del evento:', tipo='texto',
                 validar=lambda v, _r: str(v).strip() if str(v).strip() else (_ for _ in ()).throw(ValueError('Escribe el título.'))),
            Paso('dia', 'Día del mes (1 a 31):', tipo='texto', validar=validar_dia),
            Paso('mes', 'Mes:', opciones=lambda _r: list(MESES_NOMBRES)),
            Paso('hora_inicio', 'Hora inicial (formato HH:MM, ej. 09:00):', tipo='texto', validar=validar_hora),
            Paso('hora_fin', 'Hora final (formato HH:MM, ej. 11:00):', tipo='texto', validar=validar_hora),
        ],
        al_terminar=terminar,
        hoy=hoy,
    )


# --- Asignar Acceso (Usuarios) ------------------------------------------------------

ROLES_ACCESO = {
    'Admin (Recursos Humanos)': ['admin'],
    'Reclutamiento': ['reclutamiento'],
    'Notificaciones': ['notificaciones'],
}


def asistente_nuevo_usuario() -> Asistente:
    def validar_pass(valor: Any, _r: Respuestas) -> str:
        p = str(valor or '').strip()
        if len(p) < 6:
            raise ValueError('La contraseña debe tener al menos 6 caracteres.')
        return p

    def terminar(r: Respuestas) -> CambioPendiente:
        permisos = ROLES_ACCESO[r['rol']]
        usuario = str(r['usuario']).strip()
        cuerpo: dict[str, Any] = {
            'usuario': usuario,
            'password': r['password'],
            'permisos': permisos,
        }
        if 'notificaciones' in permisos:
            cuerpo['nombre_notificacion'] = str(r.get('tel_notif') and usuario or usuario).strip()
            cuerpo['telefono_notificacion'] = str(r.get('tel_notif') or '').strip()
        return CambioPendiente(
            'POST', '/api/access-users',
            f'🔐 Crear Usuario de Acceso — {usuario}:',
            detalle=[
                f'Usuario: {usuario}',
                f'Asignación / Rol: {r["rol"]}',
            ],
            cuerpo=cuerpo,
        )

    return Asistente(
        titulo='🔐 Nuevo Usuario de Acceso',
        pasos=[
            Paso('usuario', 'Nombre de usuario:', tipo='texto',
                 validar=lambda v, _r: str(v).strip() if str(v).strip() else (_ for _ in ()).throw(ValueError('Escribe el usuario.'))),
            Paso('password', 'Contraseña (mínimo 6 caracteres):', tipo='texto', validar=validar_pass),
            Paso('rol', 'Asignación / Rol:', opciones=lambda _r: list(ROLES_ACCESO)),
            Paso('tel_notif', 'Teléfono para notificaciones:', tipo='texto',
                 aplica=lambda r: r.get('rol') == 'Notificaciones',
                 validar=lambda v, _r: str(v).strip() if str(v).strip() else (_ for _ in ()).throw(ValueError('Escribe el teléfono.'))),
        ],
        al_terminar=terminar,
    )


def asistente_editar_usuario(usuarios: list[dict]) -> Asistente:
    mapa = {u['usuario']: u for u in usuarios}

    def validar_pass(valor: Any, _r: Respuestas) -> str:
        p = str(valor or '').strip()
        if len(p) < 6:
            raise ValueError('La contraseña debe tener al menos 6 caracteres.')
        return p

    def terminar(r: Respuestas) -> CambioPendiente:
        u = mapa[r['usuario']]
        return CambioPendiente(
            'PUT', f"/api/access-users/{u['id']}",
            f'✏️ Modificar contraseña de {u["usuario"]}:',
            detalle=[
                f'Usuario: {u["usuario"]}',
                'Acción: Actualizar contraseña',
            ],
            cuerpo={'password': r['password']},
        )

    return Asistente(
        titulo='✏️ Editar Usuario de Acceso',
        pasos=[
            Paso('usuario', 'Elige el usuario:', opciones=lambda _r: list(mapa)),
            Paso('password', 'Nueva contraseña (mínimo 6 caracteres):', tipo='texto', validar=validar_pass),
        ],
        al_terminar=terminar,
    )


def asistente_eliminar_usuario(usuarios: list[dict]) -> Asistente:
    mapa = {u['usuario']: u for u in usuarios}

    def terminar(r: Respuestas) -> CambioPendiente:
        u = mapa[r['usuario']]
        return CambioPendiente(
            'DELETE', f"/api/access-users/{u['id']}",
            f'🗑 Eliminar usuario {u["usuario"]}:',
            detalle=[
                f'Usuario: {u["usuario"]}',
                f'Permisos: {", ".join(u.get("permisos") or [])}',
            ],
        )

    return Asistente(
        titulo='🗑 Eliminar Usuario de Acceso',
        pasos=[
            Paso('usuario', 'Elige el usuario que deseas eliminar:', opciones=lambda _r: list(mapa)),
        ],
        al_terminar=terminar,
    )


# --- Personal Activo por Team -------------------------------------------------------

def asistente_team_agregar() -> Asistente:
    def terminar(r: Respuestas) -> CambioPendiente:
        team_num = int(r['team'].replace('Team ', '').strip())
        nombre = str(r['nombre']).strip()
        telefono = str(r['telefono']).strip()
        cuerpo = {'nombre': nombre, 'team': team_num, 'telefono': telefono}
        return CambioPendiente(
            'POST', '/api/reclutadores-nombres',
            f'👥 Agregar Reclutador — {nombre}:',
            detalle=[
                f'Nombre: {nombre}',
                f'Team: {team_num}',
                f'Teléfono: {telefono}',
            ],
            cuerpo=cuerpo,
        )

    return Asistente(
        titulo='👥 Agregar Reclutador',
        pasos=[
            Paso('nombre', 'Nombre del reclutador:', tipo='texto',
                 validar=lambda v, _r: str(v).strip() if str(v).strip() else (_ for _ in ()).throw(ValueError('Escribe el nombre.'))),
            Paso('team', 'Team / Equipo:', opciones=lambda _r: ['Team 1', 'Team 2', 'Team 3', 'Team 4']),
            Paso('telefono', 'Teléfono de contacto:', tipo='texto',
                 validar=lambda v, _r: str(v).strip() if str(v).strip() else (_ for _ in ()).throw(ValueError('Escribe el teléfono.'))),
        ],
        al_terminar=terminar,
    )


def asistente_team_modificar(reclutadores: list[dict]) -> Asistente:
    etiquetas = [f"{r['nombre']} (Team {r.get('team', 1)})" for r in reclutadores]
    mapa = {f"{r['nombre']} (Team {r.get('team', 1)})": r for r in reclutadores}

    def terminar(r: Respuestas) -> CambioPendiente:
        rec = mapa[r['reclutador']]
        nuevo_nombre = str(r['nombre']).strip() if r.get('nombre') else rec['nombre']
        nuevo_tel = str(r['telefono']).strip() if r.get('telefono') else rec.get('telefono', '')
        cuerpo = {
            'nombre': nuevo_nombre,
            'team': rec.get('team', 1),
            'telefono': nuevo_tel,
        }
        return CambioPendiente(
            'POST', f"/api/reclutadores-nombres/{rec['id']}",
            f'✏️ Modificar Reclutador — {rec["nombre"]}:',
            detalle=[
                f'Nombre: {nuevo_nombre}',
                f'Team: {rec.get("team", 1)}',
                f'Teléfono: {nuevo_tel}',
            ],
            cuerpo=cuerpo,
        )

    return Asistente(
        titulo='✏️ Modificar Reclutador',
        pasos=[
            Paso('reclutador', 'Elige al reclutador:', opciones=lambda _r: etiquetas),
            Paso('nombre', 'Nombre del reclutador (o pulsa Omitir para mantener actual):', tipo='texto', omitible=True),
            Paso('telefono', 'Teléfono del reclutador:', tipo='texto',
                 validar=lambda v, _r: str(v).strip() if str(v).strip() else (_ for _ in ()).throw(ValueError('Escribe el teléfono.'))),
        ],
        al_terminar=terminar,
    )


def asistente_team_eliminar(reclutadores: list[dict]) -> Asistente:
    etiquetas = [f"{r['nombre']} (Team {r.get('team', 1)})" for r in reclutadores]
    mapa = {f"{r['nombre']} (Team {r.get('team', 1)})": r for r in reclutadores}

    def terminar(r: Respuestas) -> CambioPendiente:
        rec = mapa[r['reclutador']]
        return CambioPendiente(
            'DELETE', f"/api/reclutadores-nombres/{rec['id']}",
            f'🗑 Eliminar Reclutador — {rec["nombre"]}:',
            detalle=[
                f'Nombre: {rec["nombre"]}',
                f'Team: {rec.get("team", 1)}',
                f'Teléfono: {rec.get("telefono") or "Sin teléfono"}',
            ],
        )

    return Asistente(
        titulo='🗑 Eliminar Reclutador',
        pasos=[
            Paso('reclutador', 'Elige al reclutador a eliminar:', opciones=lambda _r: etiquetas),
        ],
        al_terminar=terminar,
    )


# --- Generar Registro ---------------------------------------------------------------

FUENTES_REGISTRO = ['Facebook', 'Posteo', 'Volante', 'Recomendación', 'Reclu CW', 'Reclu puro']


def _comparar_cadenas(a: Any, b: Any) -> bool:
    if not a or not b:
        return True
    sa = normalizar(a).replace(' ', '')
    sb = normalizar(b).replace(' ', '')
    return sa == sb or sa in sb or sb in sa


def _obtener_datos_doc(r: Respuestas, clave: str) -> dict[str, str]:
    val = r.get(clave)
    if isinstance(val, dict):
        return val.get('datos') or {}
    return {}


def _obtener_nombre_candidato(r: Respuestas) -> str:
    for doc in ('doc_acta', 'doc_ine', 'doc_csf', 'doc_nss'):
        nombre = _obtener_datos_doc(r, doc).get('nombre_completo')
        if nombre:
            return nombre
    return str(r.get('nombre_manual') or '').strip()


def _obtener_curp(r: Respuestas) -> str:
    for doc in ('doc_acta', 'doc_ine', 'doc_csf', 'doc_nss'):
        curp = _obtener_datos_doc(r, doc).get('curp')
        if curp:
            return curp
    return str(r.get('curp_manual') or '').strip().upper()


def _obtener_propietario(r: Respuestas) -> str:
    return _obtener_datos_doc(r, 'doc_domicilio').get('nombre_propietario') or 'el titular del comprobante'


def _necesita_parentesco(r: Respuestas) -> bool:
    prop = _obtener_datos_doc(r, 'doc_domicilio').get('nombre_propietario')
    if not prop:
        return False
    cand = _obtener_nombre_candidato(r)
    if cand and _comparar_cadenas(prop, cand):
        r['parentesco_propietario'] = 'Propio'
        return False
    return True


def _tiene_nombre(r: Respuestas) -> bool:
    return bool(_obtener_nombre_candidato(r))


def _tiene_curp(r: Respuestas) -> bool:
    return bool(_obtener_curp(r))


def _tiene_domicilio(r: Respuestas) -> bool:
    for doc in ('doc_domicilio', 'doc_ine', 'doc_csf'):
        datos = _obtener_datos_doc(r, doc)
        if datos.get('calle') or datos.get('colonia') or datos.get('cp'):
            return True
    return bool(r.get('domicilio_manual'))


def formatear_resultado_escaneo(tipo: str, leidos: dict[str, str], r: Respuestas) -> str:
    lineas = [f'✅ **{tipo} analizada con éxito:**']
    if tipo == 'Acta de Nacimiento':
        if leidos.get('nombre_completo'):
            lineas.append(f'• **Nombre:** {leidos["nombre_completo"]}')
        if leidos.get('curp'):
            lineas.append(f'• **CURP:** {leidos["curp"]}')
        if leidos.get('genero'):
            lineas.append(f'• **Sexo:** {leidos["genero"]}')
        if leidos.get('fecha_nac'):
            lineas.append(f'• **Fecha nac.:** {leidos["fecha_nac"]}')
        if leidos.get('lugar_nacimiento'):
            lineas.append(f'• **Lugar nac.:** {leidos["lugar_nacimiento"]}')

    elif tipo == 'Comprobante de Domicilio':
        prop = leidos.get('nombre_propietario')
        cand = _obtener_nombre_candidato(r)
        if prop:
            coincide = _comparar_cadenas(prop, cand) if cand else False
            tag = ' (Coincide con candidato ✅)' if coincide else ' (Se solicitará parentesco ℹ️)'
            lineas.append(f'• **Titular:** {prop}{tag}')
        partes_dir = filter(None, [
            leidos.get('calle'),
            f"Ext. {leidos['num_ext']}" if leidos.get('num_ext') else None,
            f"Int. {leidos['num_int']}" if leidos.get('num_int') else None,
            f"Col. {leidos['colonia']}" if leidos.get('colonia') else None,
            f"C.P. {leidos['cp']}" if leidos.get('cp') else None,
            leidos.get('municipio'),
            leidos.get('estado'),
        ])
        dir_txt = ', '.join(partes_dir)
        if dir_txt:
            lineas.append(f'• **Dirección:** {dir_txt}')

    elif tipo == 'INE':
        acta_datos = _obtener_datos_doc(r, 'doc_acta')
        dom_datos = _obtener_datos_doc(r, 'doc_domicilio')

        if leidos.get('nombre_completo'):
            acta_nom = acta_datos.get('nombre_completo')
            comp = ' (Coincide con Acta ✅)' if acta_nom and _comparar_cadenas(acta_nom, leidos['nombre_completo']) else (f' (⚠️ Difiere de Acta: {acta_nom})' if acta_nom else '')
            lineas.append(f'• **Nombre:** {leidos["nombre_completo"]}{comp}')

        if leidos.get('curp'):
            acta_c = acta_datos.get('curp')
            comp = ' (Coincide con Acta ✅)' if acta_c and acta_c.strip().upper() == leidos['curp'].strip().upper() else (f' (⚠️ Difiere de Acta: {acta_c})' if acta_c else '')
            lineas.append(f'• **CURP:** {leidos["curp"]}{comp}')

        if leidos.get('genero'):
            acta_g = acta_datos.get('genero')
            comp = ' (Coincide con Acta ✅)' if acta_g and _comparar_cadenas(acta_g, leidos['genero']) else ''
            lineas.append(f'• **Sexo:** {leidos["genero"]}{comp}')

        if leidos.get('fecha_nac'):
            acta_f = acta_datos.get('fecha_nac')
            comp = ' (Coincide con Acta ✅)' if acta_f and acta_f == leidos['fecha_nac'] else (f' (⚠️ Difiere de Acta: {acta_f})' if acta_f else '')
            lineas.append(f'• **Fecha nac.:** {leidos["fecha_nac"]}{comp}')

        if leidos.get('calle') or leidos.get('cp'):
            comp_dom = ' (Coincide con Comprobante ✅)' if dom_datos.get('calle') and _comparar_cadenas(dom_datos.get('calle'), leidos.get('calle')) else ''
            lineas.append(f'• **Domicilio INE:** {leidos.get("calle", "")} CP {leidos.get("cp", "")}{comp_dom}')

    elif tipo == 'Constancia de Situación Fiscal':
        prev_c = _obtener_curp(r)
        prev_n = _obtener_nombre_candidato(r)
        if leidos.get('rfc'):
            lineas.append(f'• **RFC:** {leidos["rfc"]}')
        if leidos.get('curp'):
            comp = ' (Coincide con Acta/INE ✅)' if prev_c and prev_c.strip().upper() == leidos['curp'].strip().upper() else (f' (⚠️ Difiere: {prev_c})' if prev_c else '')
            lineas.append(f'• **CURP:** {leidos["curp"]}{comp}')
        if leidos.get('nombre_completo'):
            comp = ' (Coincide con Acta/INE ✅)' if prev_n and _comparar_cadenas(prev_n, leidos['nombre_completo']) else (f' (⚠️ Difiere: {prev_n})' if prev_n else '')
            lineas.append(f'• **Nombre:** {leidos["nombre_completo"]}{comp}')

    elif tipo == 'Número de Seguro Social':
        prev_c = _obtener_curp(r)
        prev_n = _obtener_nombre_candidato(r)
        if leidos.get('nss'):
            lineas.append(f'• **NSS:** {leidos["nss"]}')
        if leidos.get('nombre_completo'):
            comp = ' (Coincide con registros ✅)' if prev_n and _comparar_cadenas(prev_n, leidos['nombre_completo']) else (f' (⚠️ Difiere: {prev_n})' if prev_n else '')
            lineas.append(f'• **Nombre:** {leidos["nombre_completo"]}{comp}')
        if leidos.get('curp'):
            comp = ' (Coincide con registros ✅)' if prev_c and prev_c.strip().upper() == leidos['curp'].strip().upper() else (f' (⚠️ Difiere: {prev_c})' if prev_c else '')
            lineas.append(f'• **CURP:** {leidos["curp"]}{comp}')

    return '\n'.join(lineas)


def asistente_generar_registro(reclutadores: list[str] | None = None) -> Asistente:
    def terminar(r: Respuestas) -> CambioPendiente:
        no_temp = normalizar_numero_temporal(r['numero_temporal'])
        nombre = _obtener_nombre_candidato(r)
        curp = _obtener_curp(r)

        # Consolidar datos de los documentos
        acta = _obtener_datos_doc(r, 'doc_acta')
        dom = _obtener_datos_doc(r, 'doc_domicilio')
        ine = _obtener_datos_doc(r, 'doc_ine')
        csf = _obtener_datos_doc(r, 'doc_csf')
        nss_doc = _obtener_datos_doc(r, 'doc_nss')

        rfc = str(csf.get('rfc') or r.get('rfc_manual') or '').strip().upper()
        nss = str(nss_doc.get('nss') or r.get('nss_manual') or '').strip()
        genero = str(acta.get('genero') or ine.get('genero') or '').strip().upper()
        fecha_nac = str(acta.get('fecha_nac') or ine.get('fecha_nac') or '').strip()
        lugar_nac = str(acta.get('lugar_nacimiento') or '').strip()

        # Domicilio
        calle = str(dom.get('calle') or ine.get('calle') or csf.get('calle') or r.get('domicilio_manual') or '').strip()
        num_ext = str(dom.get('num_ext') or ine.get('num_ext') or csf.get('num_ext') or '').strip()
        num_int = str(dom.get('num_int') or ine.get('num_int') or csf.get('num_int') or '').strip()
        colonia = str(dom.get('colonia') or ine.get('colonia') or csf.get('colonia') or '').strip()
        municipio = str(dom.get('municipio') or ine.get('municipio') or csf.get('municipio') or '').strip()
        estado_dir = str(dom.get('estado') or ine.get('estado') or csf.get('estado') or '').strip()
        cp = str(dom.get('cp') or ine.get('cp') or csf.get('cp') or '').strip()

        # Propietario comprobante
        propietario = dom.get('nombre_propietario') or ''
        parentesco_prop = str(r.get('parentesco_propietario') or ('Propio' if propietario and _comparar_cadenas(propietario, nombre) else 'No especificado')).strip()

        # Contacto emergencia
        nom_eme = str(r.get('nom_eme') or '').strip()
        par_eme = str(r.get('par_eme') or '').strip()
        tel_eme = str(r.get('tel_eme') or '').strip()
        tel_movil = str(r.get('tel_movil') or '').strip()

        # Edad
        edad_str = ''
        if fecha_nac:
            try:
                fn_dt = datetime.strptime(fecha_nac, '%Y-%m-%d').date()
                hoy_d = date.today()
                edad = hoy_d.year - fn_dt.year - ((hoy_d.month, hoy_d.day) < (fn_dt.month, fn_dt.day))
                if 14 <= edad <= 100:
                    edad_str = str(edad)
            except Exception:
                edad_str = ''

        formulario: dict[str, str] = {
            'numero_temporal': no_temp,
            'nombre_completo': nombre,
            'curp': curp,
            'rfc': rfc,
            'nss': nss,
            'fecha_nac': fecha_nac,
            'edad': edad_str,
            'genero': genero,
            'procedencia': lugar_nac,
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

        siguiente = None
        fuente = r.get('fuente')
        reclutador = str(r.get('reclutador') or '').strip()
        if fuente:
            sub_fuente = 'CW' if fuente == 'Reclu CW' else ('PURO' if fuente == 'Reclu puro' else '')
            encuesta_form = {
                'curp': curp,
                'fuente': fuente,
                'sub_fuente': sub_fuente,
                'nombre_reclutador': reclutador,
                'nombre_empleado': nombre,
            }
            siguiente = CambioPendiente(
                'POST', '/api/guardar-encuesta',
                f'📊 Guardar Encuesta de Reclutamiento — {nombre}:',
                detalle=[f'Fuente: {fuente}', f'Reclutador: {reclutador or "Sin asignar"}'],
                formulario=encuesta_form,
            )

        dir_resumen = ', '.join(filter(None, [calle, f"Ext. {num_ext}" if num_ext else None, f"Col. {colonia}" if colonia else None, f"CP {cp}" if cp else None, municipio, estado_dir])) or 'Sin dato'
        detalle = [
            f'Número Temporal: {no_temp}',
            f'Nombre: {nombre}',
            f'CURP: {curp}',
            f'RFC: {rfc or "Sin dato"}',
            f'NSS: {nss or "Sin dato"}',
            f'Sexo: {genero or "Sin dato"}',
            f'Fecha Nacimiento: {fecha_nac or "Sin dato"}' + (f' ({edad_str} años)' if edad_str else ''),
            f'Lugar Nacimiento: {lugar_nac or "Sin dato"}',
            f'Dirección: {dir_resumen}',
            f'Titular Domicilio: {propietario or "Sin dato"} (Parentesco: {parentesco_prop})',
            f'Teléfono Candidato: {tel_movil}',
            f'Contacto Emergencia: {nom_eme or "Sin dato"} ({par_eme or "Sin parentesco"}) - {tel_eme or "Sin tel"}',
            f'Encuesta: {fuente or "Sin encuesta"} | Reclutador: {reclutador or "Sin asignar"}',
        ]

        return CambioPendiente(
            'POST', '/api/guardar-registro',
            f'✍️ Registrar Candidato — {nombre}:',
            detalle=detalle,
            formulario=formulario,
            siguiente=siguiente,
        )

    reclutadores_lista = list(reclutadores) if reclutadores else []

    pasos = [
        Paso('doc_acta',
             '📸 Envía la foto o PDF del **Acta de Nacimiento**:\n\n'
             'Se capturarán automáticamente:\n'
             '• Clave Única de Registro de Población (CURP)\n'
             '• Nombre completo (nombre, primer y segundo apellido)\n'
             '• Sexo\n'
             '• Fecha de nacimiento\n'
             '• Lugar de nacimiento (Entidad y municipio/localidad)\n\n'
             '*(O pulsa «Omitir» si no la tienes)*',
             tipo='archivo', omitible=True, escanear_como='Acta de Nacimiento'),

        Paso('doc_domicilio',
             '📸 Envía la foto o PDF del **Comprobante de Domicilio**:\n\n'
             'Se capturarán automáticamente:\n'
             '• Nombre del propietario/titular\n'
             '• Dirección completa (Calle, Número, Colonia, Municipio, Estado, C.P.)\n\n'
             '*(O pulsa «Omitir» si no lo tienes)*',
             tipo='archivo', omitible=True, escanear_como='Comprobante de Domicilio'),

        Paso('parentesco_propietario',
             lambda r: f"El comprobante de domicilio está a nombre de: «{_obtener_propietario(r)}».\n"
                       f"¿Qué parentesco tiene con el candidato? (o escribe «Propio» si es del candidato):",
             tipo='texto', omitible=True, aplica=lambda r: _necesita_parentesco(r)),

        Paso('doc_ine',
             '📸 Envía la foto o PDF de la **Credencial de Elector (INE / IFE)**:\n\n'
             'Se capturarán y comprobarán:\n'
             '• Nombre completo (comprobación con Acta)\n'
             '• Sexo (comprobación con Acta)\n'
             '• Domicilio (comprobación con Comprobante)\n'
             '• CURP (comprobación con Acta)\n'
             '• Fecha de Nacimiento (comprobación con Acta)\n\n'
             '*(O pulsa «Omitir» si no la tienes)*',
             tipo='archivo', omitible=True, escanear_como='INE'),

        Paso('doc_csf',
             '📸 Envía la foto o PDF de la **Constancia de Situación Fiscal (SAT)**:\n\n'
             'Se capturarán y comprobarán:\n'
             '• RFC\n'
             '• CURP (comprobación con Acta e INE)\n'
             '• Nombre Completo (comprobación con Acta e INE)\n\n'
             '*(O pulsa «Omitir» si no la tienes)*',
             tipo='archivo', omitible=True, escanear_como='Constancia de Situación Fiscal'),

        Paso('doc_nss',
             '📸 Envía la foto o PDF del **Número de Seguro Social (IMSS)**:\n\n'
             'Se capturarán y comprobarán:\n'
             '• Número de Seguro Social (NSS)\n'
             '• Nombre completo (comprobación con registros)\n'
             '• CURP (comprobación con registros)\n\n'
             '*(O pulsa «Omitir» si no lo tienes)*',
             tipo='archivo', omitible=True, escanear_como='Número de Seguro Social'),

        Paso('nombre_manual', 'Escribe el nombre completo del candidato:', tipo='texto',
             aplica=lambda r: not _tiene_nombre(r),
             validar=lambda v, _r: str(v).strip() if str(v).strip() else (_ for _ in ()).throw(ValueError('Escribe el nombre completo.'))),

        Paso('curp_manual', 'CURP (18 caracteres):', tipo='texto',
             aplica=lambda r: not _tiene_curp(r),
             validar=lambda v, _r: str(v).strip().upper() if len(str(v).strip()) >= 10 else (_ for _ in ()).throw(ValueError('Escribe una CURP válida.'))),

        Paso('domicilio_manual', 'Dirección completa (Calle, Número, Colonia, C.P., Municipio, Estado):', tipo='texto',
             omitible=True, aplica=lambda r: not _tiene_domicilio(r)),

        Paso('tel_movil', '📱 Teléfono móvil del candidato:', tipo='texto',
             validar=lambda v, _r: str(v).strip() if str(v).strip() else (_ for _ in ()).throw(ValueError('Escribe el teléfono.'))),

        Paso('nom_eme', '🚨 Contacto de Emergencia — Nombre completo (o pulsa Omitir):', tipo='texto', omitible=True),

        Paso('par_eme', '🚨 Contacto de Emergencia — Parentesco:',
             opciones=lambda _r: ['Padre', 'Madre', 'Cónyuge / Pareja', 'Hijo / Hija', 'Hermano / Hermana', 'Familiar', 'Amigo / Conocido'],
             tipo='opciones', omitible=True, aplica=lambda r: bool(r.get('nom_eme'))),

        Paso('tel_eme', '🚨 Contacto de Emergencia — Teléfono (o pulsa Omitir):', tipo='texto',
             omitible=True, aplica=lambda r: bool(r.get('nom_eme'))),

        Paso('fuente', '📊 Encuesta de Reclutamiento — ¿Por qué medio nos conoció?:', opciones=lambda _r: list(FUENTES_REGISTRO)),

        Paso('reclutador', '👤 Nombre del reclutador (o pulsa Omitir):',
             opciones=(lambda _r: reclutadores_lista) if reclutadores_lista else None,
             tipo='opciones' if reclutadores_lista else 'texto',
             omitible=True),

        Paso('numero_temporal', '🔢 Ingresa el N° de Empleado Temporal (ej. TEMP - 001):', tipo='texto',
             validar=lambda v, _r: str(v).strip() if str(v).strip() else (_ for _ in ()).throw(ValueError('Escribe el número temporal.'))),
    ]

    return Asistente(
        titulo='✍️ Generar Registro de Candidato',
        pasos=pasos,
        al_terminar=terminar,
    )


# --- Subir Papelería -----------------------------------------------------------------

DOCS_PAPELERIA = {
    'INE': 'ine',
    'Comprobante de Domicilio': 'comprobante_domicilio',
    'Acta de Nacimiento': 'acta_nacimiento',
    'CURP': 'curp_doc',
    'RFC': 'rfc_doc',
    'Numero de Seguro Social (NSS)': 'nss_doc',
    'Comprobante de Estudios': 'comprobante_estudios',
    'Infonavit': 'infonavit_doc',
    'Carta de Autorizacion': 'carta_autorizacion',
    'Contrato Firmado': 'contrato_firmado',
}


def asistente_subir_papeleria(empleado: dict[str, Any]) -> Asistente:
    nombre = empleado.get('nombre_completo') or 'Sin nombre'
    no_emp = empleado.get('no_empleado') or empleado.get('numero_temporal') or str(empleado.get('id') or '')
    registro_id = str(empleado.get('registro_id') or empleado.get('id') or '')

    def terminar(r: Respuestas) -> CambioPendiente:
        tipo_nombre = r['tipo_doc']
        doc_key = DOCS_PAPELERIA[tipo_nombre]
        archivo = r['archivo']
        archivo_envio = Archivo('file', archivo.nombre, archivo.contenido, archivo.mime)
        formulario = {
            'registro_id': registro_id,
            'doc_key': doc_key,
            'empresa': empleado.get('empresa') or 'CHEONG_WOON',
            'curp': empleado.get('curp') or '',
        }
        return CambioPendiente(
            'POST', '/api/employee-files',
            f'📎 Subir Papelería — {nombre}:',
            detalle=[
                f'Empleado: {nombre} ({no_emp})',
                f'Documento: {tipo_nombre}',
                f'Archivo: {archivo.nombre}',
            ],
            formulario=formulario,
            archivo=archivo_envio,
        )

    return Asistente(
        titulo=f'📎 Subir Papelería — {nombre}',
        pasos=[
            Paso('tipo_doc', '¿Qué documento deseas subir?', opciones=lambda _r: list(DOCS_PAPELERIA)),
            Paso('archivo', 'Envía el documento como foto o PDF (máx. 10 MB):', tipo='archivo',
                 validar=lambda a, _r: validar_archivo(a.nombre, a.contenido)),
        ],
        al_terminar=terminar,
    )
