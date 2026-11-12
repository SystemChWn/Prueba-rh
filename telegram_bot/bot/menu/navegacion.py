"""Árbol del menú /sistema.

Las claves de los nodos viajan en ``callback_data`` de mensajes ya enviados:
renombrar cambia el título, nunca la clave. Los permisos solo deciden qué
botones se muestran; quien decide de verdad es el backend.
"""

from dataclasses import dataclass

TODOS = frozenset({'admin', 'reclutamiento'})
ADMIN = frozenset({'admin'})


@dataclass(frozen=True)
class Nodo:
    clave: str
    titulo: str
    permisos: frozenset[str] = TODOS
    hijos: tuple[str, ...] = ()
    #: Acción de ``pantallas`` que ejecuta este nodo (si no tiene hijos).
    accion: str | None = None


NODOS: dict[str, Nodo] = {n.clave: n for n in (
    Nodo('raiz', 'Menú principal', hijos=(
        'crear_evento',
        'asignar_acceso',
        'ingresos_pendientes',
        'formatos_auto',
        'formatos_print',
        'formatos_dia',
        'listado_general',
        'generar_registro',
        'team_reclu',
        'dl_excel_mes',
        'subir_papeleria',
    )),

    # 1. Crear Nuevo Evento
    Nodo('crear_evento', '📅 Crear Nuevo Evento', ADMIN, hijos=('tipo_evento',)),
    Nodo('tipo_evento', 'Tipo de Evento', ADMIN, hijos=('ev_cumple', 'ev_general')),
    Nodo('ev_cumple', '🎂 Cumpleaños', ADMIN, accion='ev_cumple'),
    Nodo('ev_general', '📌 Evento', ADMIN, accion='ev_general'),

    # 2. Asignar Acceso
    Nodo('asignar_acceso', '🔐 Asignar Acceso', ADMIN, hijos=('acc_nuevo', 'acc_editar', 'acc_eliminar')),
    Nodo('acc_nuevo', '➕ Nuevo Usuario', ADMIN, accion='acc_nuevo'),
    Nodo('acc_editar', '✏️ Editar', ADMIN, accion='acc_editar'),
    Nodo('acc_eliminar', '🗑️ Eliminar', ADMIN, accion='acc_eliminar'),

    # 3. Ingresos Pendientes
    Nodo('ingresos_pendientes', '📝 Ingresos Pendientes', ADMIN, accion='ingresos_pendientes'),

    # 4. Formatos Automatizados (Seleccionado)
    Nodo('formatos_auto', '⚡ Formatos Automatizados', ADMIN, hijos=(
        'fmt_gafete',
        'fmt_cartacom',
        'fmt_cartaimss',
        'fmt_contrato',
    )),
    Nodo('fmt_gafete', '🪪 Gafete', ADMIN, hijos=('gafete_nuevo', 'gafete_existente')),
    Nodo('gafete_nuevo', 'Personal Nuevo Ingreso', ADMIN, accion='gafete_nuevo'),
    Nodo('gafete_existente', 'N° de Empleado o Nombre', ADMIN, accion='gafete_existente'),

    Nodo('fmt_cartacom', '📄 Carta Compromiso', ADMIN, accion='cartacom_buscar'),
    Nodo('fmt_cartaimss', '🏢 Carta Patronal', ADMIN, accion='cartaimss_buscar'),

    Nodo('fmt_contrato', '📑 Contrato Laboral', ADMIN, hijos=('contrato_nuevo', 'contrato_existente')),
    Nodo('contrato_nuevo', 'Personal Nuevo Ingreso', ADMIN, accion='contrato_nuevo'),
    Nodo('contrato_existente', 'N° de Empleado o Nombre', ADMIN, accion='contrato_existente'),

    # 5. Formatos para Imprimir
    Nodo('formatos_print', '🖨️ Formatos para Imprimir', ADMIN, hijos=(
        'print_suspension',
        'print_entradasalida',
        'print_indisciplina',
    )),
    Nodo('print_suspension', 'Suspensión', ADMIN, accion='print_suspension'),
    Nodo('print_entradasalida', 'Entrada/Salida', ADMIN, accion='print_entradasalida'),
    Nodo('print_indisciplina', 'Indisciplina', ADMIN, accion='print_indisciplina'),

    # 6. Formatos del dia (Seleccionado)
    Nodo('formatos_dia', '📋 Formatos del día', TODOS, hijos=('dia_evaluacion', 'dia_registro')),
    Nodo('dia_evaluacion', 'Evaluación de Inducción', TODOS, accion='dia_evaluacion'),
    Nodo('dia_registro', 'Registro de Inducción', TODOS, accion='dia_registro'),

    # 7. Listado General
    Nodo('listado_general', '📑 Listado General', ADMIN, hijos=('dl_cw', 'dl_kronos')),
    Nodo('dl_cw', 'Cheong Woon', ADMIN, accion='dl_cw'),
    Nodo('dl_kronos', 'Kronos', ADMIN, accion='dl_kronos'),

    # 8. Generar Registro
    Nodo('generar_registro', '✍️ Generar Registro', TODOS, accion='generar_registro'),

    # 9. Personal Activo por Team
    Nodo('team_reclu', '👥 Personal Activo por Team', ADMIN, hijos=('team_agregar', 'team_modificar', 'team_eliminar')),
    Nodo('team_agregar', '➕ Agregar', ADMIN, accion='team_agregar'),
    Nodo('team_modificar', '✏️ Modificar', ADMIN, accion='team_modificar'),
    Nodo('team_eliminar', '🗑️ Eliminar', ADMIN, accion='team_eliminar'),

    # 10. Descargar Excel (Ingresos del Mes)
    Nodo('dl_excel_mes', '📥 Descargar Excel (Ingresos del Mes)', ADMIN, accion='dl_excel_mes'),

    # 11. Subir Papelería (Ingresar N°Emp o TEMP-N°)
    Nodo('subir_papeleria', '📎 Subir Papelería', ADMIN, accion='subir_papeleria'),
)}


def puede(nodo: Nodo, permisos: list[str] | set[str]) -> bool:
    return bool(nodo.permisos & set(permisos))


def hijos_visibles(clave: str, permisos: list[str] | set[str]) -> list[Nodo]:
    """Hijos que la persona puede ver; una rama sin hojas visibles se oculta."""
    visibles = []
    for hijo in NODOS[clave].hijos:
        nodo = NODOS[hijo]
        if not puede(nodo, permisos):
            continue
        if nodo.hijos and not hijos_visibles(hijo, permisos):
            continue
        visibles.append(nodo)
    return visibles


def padre_de(clave: str) -> str | None:
    for nodo in NODOS.values():
        if clave in nodo.hijos:
            return nodo.clave
    return None
