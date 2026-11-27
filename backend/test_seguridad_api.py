import io
import unittest

import zxingcpp
from PIL import Image

import seguridad_api
import utily


class SeguridadApiTests(unittest.TestCase):
    def test_normalizar_permisos_acepta_seguridad(self):
        self.assertEqual(utily.normalizar_permisos(['SEGURIDAD']), ['seguridad'])

    def test_extraer_iniciales(self):
        self.assertEqual(seguridad_api.extraer_iniciales('Juan Pérez López'), 'JPL')
        self.assertEqual(seguridad_api.extraer_iniciales('Ana'), 'A')

    def test_generar_imagen_qr_permiso_es_legible_por_zxing(self):
        folio = 'JPL-20261127-1045'
        png = seguridad_api.generar_imagen_qr_permiso(
            nombre_completo='Juan Pérez López',
            no_empleado='1045',
            tipo_permiso='Salida',
            fecha_texto='27 de noviembre de 2026',
            folio=folio,
        )
        img = Image.open(io.BytesIO(png))
        codigos = zxingcpp.read_barcodes(img)
        self.assertTrue(codigos, 'El QR generado debe ser legible por zxingcpp')
        self.assertEqual(codigos[0].text, folio)

    def test_generar_imagen_tarjeta_chica(self):
        png = seguridad_api.generar_imagen_tarjeta_chica('Juan Pérez López', '1045')
        img = Image.open(io.BytesIO(png))
        self.assertEqual(img.size, (680, 310))

    def test_formatos_whatsapp_contienen_campos_clave(self):
        msg_ind = seguridad_api.formatear_mensaje_whatsapp('indisciplina', 'Juan', {'motivo': 'Llegada tarde'})
        self.assertIn('acumulación de 2 reportes', msg_ind)
        self.assertIn('Llegada tarde', msg_ind)

        msg_carta = seguridad_api.formatear_mensaje_whatsapp('carta-suspension', 'Ana', {'motivo': 'Falta'})
        self.assertIn('firmó la carta compromiso', msg_carta)

        msg_vac = seguridad_api.formatear_mensaje_whatsapp(
            'vacaciones', 'Luis', {'dias': 3, 'fecha_inicio': '2026-12-01', 'fecha_reingreso': '2026-12-03'}
        )
        self.assertIn('Días solicitados: 3', msg_vac)

        msg_inc = seguridad_api.formatear_mensaje_whatsapp(
            'incapacidad', 'Sofía', {'tipo_incapacidad': 'Riesgo de Trabajo', 'dias': 5, 'fecha_inicio': '2026-12-01', 'fecha_reingreso': '2026-12-05'}
        )
        self.assertIn('Tipo: Riesgo de Trabajo', msg_inc)
        self.assertIn('IMSS', msg_inc)


if __name__ == '__main__':
    unittest.main()
