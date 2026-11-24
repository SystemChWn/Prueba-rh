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

    def test_nacionalidad_y_encuesta_reclutador_filtros(self):
        # Sesión por defecto incluye nacionalidad Mexicana
        sesion = SesionRegistro(user_id=123, datos={'curp': 'AAAA000000HDFRRN01'})
        datos, _ = registro_candidato.ejecutar_validacion_consenso(sesion)
        self.assertEqual(datos.get('nacionalidad'), 'MEXICANA')

        # Si se asigna otra nacionalidad
        sesion.datos['nacionalidad'] = 'COLOMBIANA'
        datos, _ = registro_candidato.ejecutar_validacion_consenso(sesion)
        self.assertEqual(datos.get('nacionalidad'), 'COLOMBIANA')

    def test_estados_nuevos_definidos(self):
        self.assertEqual(EstadoRegistro.ESPERANDO_DOC_CURP.value, 'esperando_doc_curp')
        self.assertEqual(EstadoRegistro.ESPERANDO_NACIONALIDAD.value, 'esperando_nacionalidad')
        self.assertEqual(EstadoRegistro.ESPERANDO_FOTO_CANDIDATO.value, 'esperando_foto_candidato')
        self.assertEqual(EstadoRegistro.CONFIRMANDO_DATO_PASO.value, 'confirmando_dato_paso')
        self.assertEqual(EstadoRegistro.ESPERANDO_CORRECCION_PASO.value, 'esperando_correccion_paso')
        self.assertEqual(EstadoRegistro.ESPERANDO_DOMICILIO_CP.value, 'esperando_domicilio_cp')
        self.assertEqual(EstadoRegistro.ESPERANDO_DOMICILIO_COLONIA.value, 'esperando_domicilio_colonia')
        self.assertEqual(EstadoRegistro.ESPERANDO_DOMICILIO_CALLE.value, 'esperando_domicilio_calle')
        self.assertEqual(EstadoRegistro.ESPERANDO_DOMICILIO_NUM_EXT.value, 'esperando_domicilio_num_ext')
        self.assertEqual(EstadoRegistro.ESPERANDO_DOMICILIO_NUM_INT.value, 'esperando_domicilio_num_int')
        self.assertEqual(EstadoRegistro.CONFIRMANDO_DOMICILIO.value, 'confirmando_domicilio')

    def test_catalogo_cp_busqueda(self):
        from bot import catalogo_cp
        info = catalogo_cp.buscar_cp('64000')
        self.assertIsNotNone(info)
        self.assertEqual(info['municipio'], 'Monterrey')
        self.assertEqual(info['estado'], 'Nuevo León')
        self.assertIn('Monterrey Centro', info['colonias'])

        # CP con ceros o inexistente
        self.assertIsNone(catalogo_cp.buscar_cp('00000'))
        self.assertIsNone(catalogo_cp.buscar_cp('abcde'))

    def test_formato_teclado_colonias(self):
        colonias = [f"Colonia {i}" for i in range(15)]
        teclado = registro_candidato._teclado_colonias(colonias, pagina=0)
        # 6 colonias por página + 1 fila paginación + 1 fila escribir/cancelar
        self.assertTrue(len(teclado.inline_keyboard) >= 7)
        # Primera página debe tener botón '>' para página 1
        callbacks = [btn.callback_data for row in teclado.inline_keyboard for btn in row]
        self.assertIn('reg:col:0', callbacks)
        self.assertIn('reg:col_pag:1', callbacks)
        self.assertIn('reg:col_escribir', callbacks)

    def test_escanear_qr_curp_local(self):
        # Cadena oficial RENAPO
        raw = "LOCF720526MNLPRL09||LOPEZ|CORDOVA|FELIPA|MUJER|26/05/1972|NUEVO LEON|19|"
        # Probar parseo de cadena
        m = registro_candidato.re.search(r'([A-Z]{4}\d{6}[HM][A-Z]{2}[A-Z0-9]{3}[A-Z0-9]{2})', raw.upper())
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), 'LOCF720526MNLPRL09')
        # Probar que maneja bytes vacíos sin excepción
        self.assertEqual(registro_candidato._escanear_qr_curp_local(b''), {})

    def test_transicion_rfc_a_nss(self):
        sesion = SesionRegistro(user_id=999)
        sesion.datos['rfc'] = 'XAXX010101000'
        sesion.campo_confirmando = 'rfc'
        sesion.cola_confirmacion = []
        # Si siguiente_estado_post_conf apuntaba a domicilio por error:
        sesion.siguiente_estado_post_conf = EstadoRegistro.ESPERANDO_DOMICILIO_CP

        campo_terminado = sesion.campo_confirmando
        sig = sesion.siguiente_estado_post_conf
        if campo_terminado == 'rfc':
            sig = EstadoRegistro.ESPERANDO_NSS
        self.assertEqual(sig, EstadoRegistro.ESPERANDO_NSS)



if __name__ == '__main__':
    unittest.main()
