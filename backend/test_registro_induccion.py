import io
import unittest
from unittest.mock import MagicMock, patch

import utily


class RegistroInduccionTests(unittest.TestCase):
    def setUp(self):
        self.client = utily.app.test_client()

    def test_estado_hoy_fecha_invalida(self):
        res = self.client.get('/api/registro-induccion/estado-hoy?fecha=2026-99-99')
        self.assertEqual(res.status_code, 400)

    def test_historial_mes_invalido(self):
        res = self.client.get('/api/registro-induccion/historial-mes?mes=13&anio=2026')
        self.assertEqual(res.status_code, 400)

    def test_subir_sin_archivo(self):
        res = self.client.post('/api/registro-induccion/subir', data={})
        self.assertEqual(res.status_code, 400)
        self.assertIn('El archivo es obligatorio', res.get_json().get('error', ''))

    @patch('utily.get_db_connection')
    def test_estado_hoy_existente(self, mock_conn):
        cur = MagicMock()
        cur.fetchone.return_value = (1, 'checklist_2026_11_13.pdf', '2026-11-13 10:00:00')
        mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cur

        res = self.client.get('/api/registro-induccion/estado-hoy?fecha=2026-11-13')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data['subido'])
        self.assertEqual(data['id'], 1)
        self.assertEqual(data['nombre_archivo'], 'checklist_2026_11_13.pdf')

    @patch('utily.get_db_connection')
    def test_subir_archivo_exitoso(self, mock_conn):
        cur = MagicMock()
        cur.fetchone.return_value = (5,)
        mock_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cur

        data = {
            'file': (io.BytesIO(b'%PDF-1.4 test content'), 'induccion.pdf'),
            'fecha': '2026-11-13'
        }
        res = self.client.post('/api/registro-induccion/subir', data=data, content_type='multipart/form-data')
        self.assertEqual(res.status_code, 200)
        json_data = res.get_json()
        self.assertTrue(json_data['success'])
        self.assertEqual(json_data['id'], 5)


if __name__ == '__main__':
    unittest.main()

