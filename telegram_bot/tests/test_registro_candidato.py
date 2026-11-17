import unittest
from bot import registro_candidato
from bot.registro_candidato import EstadoRegistro, SesionRegistro


class RegistroCandidatoTests(unittest.TestCase):
    def test_normalizacion_y_comparacion_cadenas(self):
        self.assertTrue(registro_candidato.cadenas_coinciden('Juan Pérez López', 'JUAN PEREZ LOPEZ'))
        self.assertTrue(registro_candidato.cadenas_coinciden('María de los Ángeles', 'MARIA DE LOS ANGELES'))
        self.assertTrue(registro_candidato.cadenas_coinciden('Ana Sofía', 'Ana Sofia Ruiz'))
        self.assertFalse(registro_candidato.cadenas_coinciden('Pedro Gómez', 'Carlos Ruiz'))
        self.assertFalse(registro_candidato.cadenas_coinciden('', 'Alguien'))

    def test_calculo_edad(self):
        self.assertNotEqual(registro_candidato.calcular_edad('2000-01-01'), '')
        self.assertGreaterEqual(int(registro_candidato.calcular_edad('2000-01-01')), 20)
        self.assertEqual(registro_candidato.calcular_edad('invalido'), '')
        self.assertEqual(registro_candidato.calcular_edad(None), '')

    def test_normalizar_numero_temporal(self):
        self.assertEqual(registro_candidato.normalizar_numero_temporal('001'), 'TEMP - 001')
        self.assertEqual(registro_candidato.normalizar_numero_temporal('TEMP - 005'), 'TEMP - 005')
        self.assertEqual(registro_candidato.normalizar_numero_temporal('temp-12'), 'TEMP - 12')
        self.assertEqual(registro_candidato.normalizar_numero_temporal(''), 'TEMP - 001')

    def test_gestion_sesion(self):
        user_id = 999999
        registro_candidato.cancelar_registro(user_id)
        self.assertFalse(registro_candidato.tiene_registro_activo(user_id))

        sesion = SesionRegistro(user_id=user_id, reclutadores=['Ana', 'Beto'])
        registro_candidato.registro_activo[user_id] = sesion
        self.assertTrue(registro_candidato.tiene_registro_activo(user_id))
        self.assertIs(registro_candidato.obtener_sesion(user_id), sesion)

        self.assertTrue(registro_candidato.cancelar_registro(user_id))
        self.assertFalse(registro_candidato.tiene_registro_activo(user_id))

    def test_flujo_estados_sesion(self):
        sesion = SesionRegistro(user_id=123)
        self.assertEqual(sesion.estado, EstadoRegistro.ESPERANDO_ACTA)

        # Simular datos extraídos de Acta
        sesion.datos['acta'] = {
            'nombre_completo': 'Carlos López Ruiz',
            'curp': 'LORC950505HDFRRN09',
            'sexo': 'MASCULINO',
            'fecha_nac': '1995-05-05',
            'lugar_nacimiento': 'Toluca, Edo Mex',
        }
        sesion.datos['nombre_completo'] = 'Carlos López Ruiz'

        # Domicilio propio
        es_titular = registro_candidato.cadenas_coinciden('Carlos Lopez Ruiz', sesion.datos['nombre_completo'])
        self.assertTrue(es_titular)

        # Domicilio de tercero
        es_titular_tercero = registro_candidato.cadenas_coinciden('Rosa Gómez Martínez', sesion.datos['nombre_completo'])
        self.assertFalse(es_titular_tercero)


if __name__ == '__main__':
    unittest.main()
