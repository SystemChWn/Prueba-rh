from bot.menu import pantallas
from bot.menu.navegacion import NODOS, hijos_visibles, padre_de


def test_todo_hijo_existe_y_las_claves_coinciden():
    for clave, nodo in NODOS.items():
        assert nodo.clave == clave
        for hijo in nodo.hijos:
            assert hijo in NODOS, f'{clave} apunta a {hijo}, que no existe'


def test_cada_hoja_tiene_una_accion_implementada():
    hojas = {n.accion for n in NODOS.values() if not n.hijos}
    assert None not in hojas
    for accion in hojas:
        assert accion in pantallas.ACCIONES, f"Acción '{accion}' no está en pantallas.ACCIONES"


def test_callback_data_de_nodos_cabe_en_64_bytes():
    assert all(len(f'm:{clave}'.encode()) <= 64 for clave in NODOS)


def test_cada_nodo_es_alcanzable_desde_la_raiz():
    alcanzables, pendientes = set(), ['raiz']
    while pendientes:
        clave = pendientes.pop()
        alcanzables.add(clave)
        pendientes.extend(NODOS[clave].hijos)
    assert alcanzables == set(NODOS)


def test_reclutamiento_no_ve_lo_de_admin():
    raiz = [n.clave for n in hijos_visibles('raiz', ['reclutamiento'])]
    assert raiz == ['formatos_dia', 'generar_registro']
    assert [n.clave for n in hijos_visibles('formatos_dia', ['reclutamiento'])] == ['dia_evaluacion', 'dia_registro']


def test_admin_ve_todo():
    esperados = [
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
    ]
    assert [n.clave for n in hijos_visibles('raiz', ['admin'])] == esperados


def test_sin_permisos_no_ve_nada():
    assert hijos_visibles('raiz', ['notificaciones']) == []


def test_padre():
    assert padre_de('dl_cw') == 'listado_general'
    assert padre_de('tipo_evento') == 'crear_evento'
    assert padre_de('ev_cumple') == 'tipo_evento'
    assert padre_de('raiz') is None
