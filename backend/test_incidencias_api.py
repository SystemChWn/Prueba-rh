import os
import unittest
from datetime import date, datetime
from unittest.mock import patch

import utily


class FakeCursor:
    def __init__(self):
        self.result = None
        self.rows = []
        self.inserted = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, query, params=None):
        if 'FROM information_schema.columns' in query:
            self.result = ('YES',)
        elif 'SELECT r.nombre_completo, r.tel_movil' in query:
            self.result = ('Ana Colaboradora', '8112345678', 'Contacto', '8111223344', '1001')
        elif 'INSERT INTO incidencias_chatbot' in query:
            self.inserted = params
            self.result = (
                17,
                params[2],
                params[3],
                params[4],
                params[5],
                None,
                'no_aplica',
                None,
                datetime(2026, 10, 27, 10, 0),
            )
        elif 'SELECT id, tipo, motivo' in query:
            self.rows = [
                (
                    17,
                    'vacaciones',
                    'Vacaciones',
                    date(2026, 11, 1),
                    date(2026, 11, 3),
                    None,
                    'no_aplica',
                    None,
                    datetime(2026, 10, 27, 10, 0),
                )
            ]
        elif 'DELETE FROM incidencias_chatbot' in query:
            self.result = (17,) if params == (17, 1) else None
        else:
            self.result = None

    def fetchone(self):
        return self.result

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def cursor(self):
        return self.fake_cursor

    def commit(self):
        pass


class IncidenciasApiTests(unittest.TestCase):
    def setUp(self):
        self.write_lock_patch = patch.dict(os.environ, {'DB_WRITE_LOCK': '0'})
        self.write_lock_patch.start()
        self.cursor = FakeCursor()
        self.connection = FakeConnection(self.cursor)
        self.client = utily.app.test_client()

    def tearDown(self):
        self.write_lock_patch.stop()

    def test_post_persists_non_chatbot_incident_in_database(self):
        with patch.object(utily, 'get_db_connection', return_value=self.connection):
            response = self.client.post(
                '/api/empleados/1/incidencias',
                json={
                    'tipo': 'vacaciones',
                    'motivo': 'Vacaciones',
                    'fecha_inicio': '2026-11-01',
                    'fecha_reingreso': '2026-11-03',
                },
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()['incidencia']['id'], 17)
        self.assertEqual(self.cursor.inserted[0:6], (
            1,
            '1001',
            'vacaciones',
            'Vacaciones',
            date(2026, 11, 1),
            date(2026, 11, 3),
        ))

    def test_get_returns_database_incident_history(self):
        with patch.object(utily, 'get_db_connection', return_value=self.connection):
            response = self.client.get('/api/empleados/1/incidencias')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['incidencias'][0]['id'], 17)
        self.assertEqual(response.get_json()['incidencias'][0]['fecha_inicio'], '2026-11-01')

    def test_delete_uses_database_record_id_and_employee_id(self):
        with patch.object(utily, 'get_db_connection', return_value=self.connection):
            response = self.client.delete('/api/empleados/1/incidencias/17')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['ok'])


if __name__ == '__main__':
    unittest.main()
