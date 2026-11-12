"""Los formularios del bot mandan lo mismo que el panel.

Se leen los nombres de campo de frontend/Ingreso.html y frontend/empleado.html:
si el panel cambia un campo y el bot no, estas pruebas fallan.
"""

import json
import re
from datetime import date
from pathlib import Path

try:
    import pytest
except ImportError:
    class _PytestMock:
        @staticmethod
        def raises(exc):
            class _Raises:
                def __enter__(self):
                    return self
                def __exit__(self, exc_type, exc_val, exc_tb):
                    return isinstance(exc_val, exc)
            return _Raises()
        class mark:
            @staticmethod
            def parametrize(*args, **kwargs):
                return lambda fn: fn
    pytest = _PytestMock()

from bot.menu import formularios

RAIZ = Path(__file__).resolve().parents[2]
INGRESO_HTML = (RAIZ / 'frontend' / 'Ingreso.html').read_text(encoding='utf-8')
EMPLEADO_HTML = (RAIZ / 'frontend' / 'empleado.html').read_text(encoding='utf-8')
UTILY = (RAIZ / 'backend' / 'utily.py').read_text(encoding='utf-8')

CATALOGO = {
    'empresas': ['CHEONG WOON', 'KRONOS'],
    'areas': ['EPS', 'CALIDAD'],
    'puestos_por_area': {'EPS': ['MOLDERO', 'OPERARIO'], 'CALIDAD': ['INSPECTOR DE CALIDAD']},
    'turnos': ['DÍA', 'NOCHE'],
    'pagos': ['SEMANAL', 'QUINCENAL'],
    'personal': 'OPERATIVO',
}
HOY = date(2026, 11, 9)
EMPLEADO = {'registro_id': 101, 'nombre_completo': 'Ana Prueba', 'no_empleado': '5001',
            'empresa': 'CHEONG WOON', 'puesto': 'MOLDERO', 'estatus': 'ACTIVO'}


def claves_objeto_js(html, inicio):
    """Claves de un objeto literal JS que empieza en ``inicio``."""
    cuerpo = html[html.index(inicio):]
    cuerpo = cuerpo[cuerpo.index('{') + 1:]
    nivel, fin = 1, 0
    for fin, caracter in enumerate(cuerpo):
        nivel += {'{': 1, '}': -1}.get(caracter, 0)
        if nivel == 0:
            break
    return set(re.findall(r'^\s*([a-z_]+):', cuerpo[:fin], re.M))


def llenar_ingreso():
    a = formularios.asistente_ingreso({'id': 33, 'nombre_completo': 'Beto Ruiz', 'numero_temporal': '77'}, CATALOGO)
    a.responder('KRONOS')
    a.responder('EPS')
    a.responder('MOLDERO')
    a.responder('NOCHE')
    a.responder('  $2,500  semanal ')
    a.responder('SEMANAL')
    return a.construir()


def test_ingreso_manda_los_campos_del_panel():
    pendiente = llenar_ingreso()
    assert set(pendiente.cuerpo) == claves_objeto_js(INGRESO_HTML, 'function construirFilaPayload(tr)')
    assert (pendiente.metodo, pendiente.ruta) == ('POST', '/api/guardar-ingreso')
    assert pendiente.cuerpo == {
        'nombre_candidato': 'Beto Ruiz', 'empresa': 'KRONOS', 'no_empleado': 'TEMP - 77',
        'personal': 'OPERATIVO', 'puesto': 'MOLDERO', 'area': 'EPS', 'turno': 'NOCHE',
        'salario': '$2,500 semanal', 'pago': 'SEMANAL', 'registro_id': '33',
    }
    assert 'Puesto: MOLDERO' in pendiente.resumen()


def test_puestos_dependen_del_area():
    a = formularios.asistente_ingreso({'id': 1, 'nombre_completo': 'X', 'numero_temporal': '1'}, CATALOGO)
    a.responder('KRONOS')
    a.responder('CALIDAD')
    assert a.opciones() == ['INSPECTOR DE CALIDAD']
    with pytest.raises(ValueError):
        a.responder('MOLDERO')


@pytest.mark.parametrize('entrada,esperado', [
    ('77', 'TEMP - 77'), ('TEMP-77', 'TEMP - 77'), ('temp  -  9', 'TEMP - 9'), ('', ''), (None, ''),
])
def test_numero_temporal_como_el_panel(entrada, esperado):
    assert formularios.normalizar_numero_temporal(entrada) == esperado


def test_sin_numero_temporal_no_se_asigna_por_bot():
    with pytest.raises(ValueError):
        formularios.asistente_ingreso({'id': 1, 'nombre_completo': 'X', 'numero_temporal': ''}, CATALOGO)


def test_incidencia_manda_los_campos_del_panel():
    a = formularios.asistente_incidencia(EMPLEADO, hoy=lambda: HOY)
    a.responder('Suspensión')
    a.responder('Faltó tres días sin aviso')
    a.responder('10/11/2026')
    a.responder('12/11/2026')
    a.responder(formularios.validar_archivo('acta.pdf', b'%PDF'))
    p = a.construir()

    campos_panel = set(re.findall(r"datosIncidencia\.set\('([a-z_]+)'", EMPLEADO_HTML))
    assert set(p.formulario) | {'archivo'} == campos_panel
    assert set(json.loads(p.formulario['detalles'])) == claves_objeto_js(EMPLEADO_HTML, 'const detalles = {')
    assert p.ruta == '/api/empleados/101/incidencias'
    # Igual que el panel: a la última fecha de una suspensión se le suma un día.
    assert (p.formulario['fecha_inicio'], p.formulario['fecha_reingreso']) == ('2026-11-10', '2026-11-13')
    assert p.formulario['motivo'] == 'Faltó tres días sin aviso'
    assert p.argumentos_peticion()['files'] == [('archivo', ('acta.pdf', b'%PDF', 'application/pdf'))]


def test_vacaciones_sin_motivo_ni_archivo():
    a = formularios.asistente_incidencia(EMPLEADO, hoy=lambda: HOY)
    a.responder('Vacaciones')
    assert a.paso.campo == 'desde'
    a.responder('hoy')
    a.responder('13/11/2026')
    p = a.construir()
    assert p.formulario['fecha_reingreso'] == '2026-11-13'
    assert p.formulario['motivo'] == 'Vacaciones'
    assert json.loads(p.formulario['detalles'])['dias'] == 5
    assert 'files' not in p.argumentos_peticion()


def test_tipos_de_incidencia_los_acepta_el_backend():
    permitidos = set(re.search(r"if tipo not in (\{[^}]+\})", UTILY).group(1).strip('{}').replace("'", '').replace(' ', '').split(','))
    assert {tipo for tipo, _ in formularios.TIPOS_INCIDENCIA.values()} <= permitidos


def test_fecha_final_antes_de_la_inicial():
    a = formularios.asistente_incidencia(EMPLEADO, hoy=lambda: HOY)
    a.responder('Incapacidad')
    a.responder('12/11/2026')
    with pytest.raises(ValueError):
        a.responder('10/11/2026')


@pytest.mark.parametrize('nombre,contenido', [('virus.exe', b'x'), ('foto.gif', b'x'), ('a.pdf', b'x' * (10 * 1024 * 1024 + 1))])
def test_archivo_invalido(nombre, contenido):
    with pytest.raises(ValueError):
        formularios.validar_archivo(nombre, contenido)


def test_baja_solo_manda_estatus():
    p = formularios.pendiente_estatus(EMPLEADO)
    assert (p.metodo, p.ruta, p.cuerpo) == ('POST', '/api/actualizar-empleado/101', {'estatus': 'INACTIVO'})
    assert formularios.pendiente_estatus({**EMPLEADO, 'estatus': 'INACTIVO'}).cuerpo == {'estatus': 'ACTIVO'}


def test_baja_usa_el_mismo_cuerpo_que_el_listado_del_panel():
    cheong = (RAIZ / 'frontend' / 'Cheong Woon.html').read_text(encoding='utf-8')
    assert re.search(r"actualizar-empleado/\$\{[^}]+\}`, \{\s*estatus: nuevo,\s*\}", cheong)


def test_eliminar_es_delete_con_doble_confirmacion():
    p = formularios.pendiente_eliminar(EMPLEADO)
    assert (p.metodo, p.ruta, p.confirmaciones_faltantes) == ('DELETE', '/api/eliminar-empleado/101', 2)


def test_asistente_cumpleanos():
    a = formularios.asistente_cumpleanos([], hoy=lambda: HOY)
    a.responder('Carlos Mendoza')
    a.responder('15')
    a.responder('Mayo')
    p = a.construir()
    assert (p.metodo, p.ruta) == ('POST', '/api/shared-events')
    assert len(p.cuerpo['eventos']) == 1
    ev = p.cuerpo['eventos'][0]
    assert ev['title'] == 'Carlos Mendoza'
    assert ev['monthDay'] == '15-05'
    assert ev['tipo'] == 'cumpleanos'


def test_asistente_evento():
    a = formularios.asistente_evento([], hoy=lambda: HOY)
    a.responder('Junta Mensual')
    a.responder('20')
    a.responder('Octubre')
    a.responder('10:00')
    a.responder('11:30')
    p = a.construir()
    assert (p.metodo, p.ruta) == ('POST', '/api/shared-events')
    assert len(p.cuerpo['eventos']) == 1
    ev = p.cuerpo['eventos'][0]
    assert ev['title'] == 'Junta Mensual'
    assert ev['monthDay'] == '20-10'
    assert ev['startTime'] == '10:00'
    assert ev['endTime'] == '11:30'
    assert ev['tipo'] == 'evento'


def test_asistente_nuevo_usuario():
    a = formularios.asistente_nuevo_usuario()
    a.responder('lucia.rh')
    a.responder('segura123')
    a.responder('Admin (Recursos Humanos)')
    p = a.construir()
    assert (p.metodo, p.ruta) == ('POST', '/api/access-users')
    assert p.cuerpo == {
        'usuario': 'lucia.rh',
        'password': 'segura123',
        'permisos': ['admin'],
    }


def test_asistente_generar_registro():
    a = formularios.asistente_generar_registro()
    a.responder('TEMP-88')
    a.responder('Pedro Morales')
    a.responder('MOPP900101HDFRRN01')
    a.omitir()  # acta
    a.omitir()  # domicilio
    a.omitir()  # rfc
    a.omitir()  # nss
    a.responder('8112345678')
    a.omitir()  # correo
    a.omitir()  # emergencia
    a.responder('Facebook')
    a.responder('Reclutador 1')
    p = a.construir()
    assert (p.metodo, p.ruta) == ('POST', '/api/guardar-registro')
    assert p.formulario['numero_temporal'] == 'TEMP - 88'
    assert p.formulario['nombre_completo'] == 'Pedro Morales'
    assert p.siguiente is not None
    assert (p.siguiente.metodo, p.siguiente.ruta) == ('POST', '/api/guardar-encuesta')
    assert p.siguiente.formulario['fuente'] == 'Facebook'


def test_asistente_subir_papeleria():
    a = formularios.asistente_subir_papeleria(EMPLEADO)
    a.responder('INE')
    a.responder(formularios.validar_archivo('ine.jpg', b'\xff\xd8\xff\xe0' + b'x' * 100))
    p = a.construir()
    assert (p.metodo, p.ruta) == ('POST', '/api/employee-files')
    assert p.formulario['registro_id'] == '101'
    assert p.formulario['doc_key'] == 'ine'
    assert p.archivo.campo == 'file'
