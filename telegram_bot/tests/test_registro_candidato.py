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

    def test_ordenar_nombre_natural(self):
        # Con desglose de nombre y apellidos (como en Acta)
        res = registro_candidato.ordenar_nombre_natural('PÉREZ LÓPEZ CARLOS', 'CARLOS', 'PÉREZ', 'LÓPEZ')
        self.assertEqual(res, 'CARLOS PÉREZ LÓPEZ')

        # Con nombre que empieza por apellidos reconocidos
        res2 = registro_candidato.ordenar_nombre_natural('PÉREZ LÓPEZ CARLOS ALBERTO', apellido_pat='PÉREZ', apellido_mat='LÓPEZ')
        self.assertEqual(res2, 'CARLOS ALBERTO PÉREZ LÓPEZ')

        # Nombre ya en formato natural
        res3 = registro_candidato.ordenar_nombre_natural('CARLOS ALBERTO PÉREZ LÓPEZ')
        self.assertEqual(res3, 'CARLOS ALBERTO PÉREZ LÓPEZ')

    def test_normalizar_sexo_ine(self):
        # En INE: M = Mujer / Femenino, H = Hombre / Masculino
        self.assertEqual(registro_candidato.normalizar_sexo('M', es_ine=True), 'FEMENINO')
        self.assertEqual(registro_candidato.normalizar_sexo('H', es_ine=True), 'MASCULINO')
        self.assertEqual(registro_candidato.normalizar_sexo('Mujer', es_ine=True), 'FEMENINO')
        self.assertEqual(registro_candidato.normalizar_sexo('Hombre', es_ine=True), 'MASCULINO')

    def test_resolver_por_mayoria(self):
        # Voto por mayoría: 2 contra 1
        valores = ['JUAN PEREZ LOPEZ', 'Juan Pérez López', 'JUAN PEREZ L']
        ganador, hay_disc, vars_ = registro_candidato.resolver_por_mayoria(valores)
        self.assertEqual(registro_candidato.normalizar_texto(ganador), 'juan perez lopez')
        self.assertTrue(hay_disc)
        self.assertIn('JUAN PEREZ L', vars_)

        # Todos iguales
        curps = ['PELJ000101HDFRRN01', 'PELJ000101HDFRRN01']
        ganador_curp, hay_disc_curp, _ = registro_candidato.resolver_por_mayoria(curps)
        self.assertEqual(ganador_curp, 'PELJ000101HDFRRN01')
        self.assertFalse(hay_disc_curp)

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


if __name__ == '__main__':
    unittest.main()
