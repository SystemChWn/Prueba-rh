import os
import unittest
from datetime import date
from unittest.mock import Mock, patch

import bot


class FakeCursor:
  def __init__(self):
    self.result = None
    self.insert_sql = ""
    self.insert_params = None

  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc_value, traceback):
    return False

  def execute(self, query, params=None):
    if "SELECT r.id, r.nombre_completo" in query:
      self.result = (7, "Ana Colaboradora", "8112345678", "Contacto", "8111223344", "1001")
    elif "SELECT COUNT(*)" in query:
      self.result = (1,)
    elif "INSERT INTO incidencias_chatbot" in query:
      self.insert_sql = query
      self.insert_params = params
      self.result = (42,)
    else:
      self.result = None

  def fetchone(self):
    return self.result


class FakeConnection:
  def __init__(self, cursor):
    self.fake_cursor = cursor

  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc_value, traceback):
    return False

  def cursor(self):
    return self.fake_cursor


class IncidenciaMessageTests(unittest.TestCase):
  def test_mexican_phone_numbers_are_normalized_for_whatsapp(self):
    self.assertEqual(bot.telefono_whatsapp("8112345678"), "528112345678")
    self.assertEqual(bot.telefono_whatsapp("+52 81 1234 5678"), "528112345678")
    self.assertEqual(bot.telefono_whatsapp("+52 1 81 1234 5678"), "528112345678")
    with self.assertRaises(ValueError):
      bot.telefono_whatsapp("123")

  def test_suspension_template_receives_formatted_dates(self):
    template, parametros = bot.construir_mensaje_colaborador(
      "suspension",
      "incumplimiento",
      date(2026, 11, 2),
      date(2026, 11, 5),
      None,
    )
    self.assertEqual(template, "suspension_colaborador")
    self.assertEqual(parametros, ["02/11/2026", "incumplimiento", "05/11/2026"])

  def test_second_indiscipline_uses_second_report_templates(self):
    employee_template, _ = bot.construir_mensaje_colaborador(
      "indisciplina", "defectos", None, None, 2
    )
    emergency_template, _ = bot.construir_mensaje_emergencia(
      "indisciplina", "Nombre", "defectos", None, None, 2
    )
    self.assertEqual(employee_template, "indisciplina_segundo_colaborador")
    self.assertEqual(emergency_template, "indisciplina_segundo_emergencia")

  def test_invalid_incident_data_is_rejected_before_database_access(self):
    with self.assertRaisesRegex(ValueError, "motivo de indisciplina"):
      bot.registrar_incidencia({
        "tipo": "indisciplina",
        "no_empleado": "1001",
        "categoria": "motivo inválido",
        "motivo": "motivo inválido",
      })

    with self.assertRaisesRegex(ValueError, "fecha_inicio"):
      bot.registrar_incidencia({
        "tipo": "suspension",
        "no_empleado": "1001",
        "motivo": "incumplimiento",
        "fecha_reingreso": "2026-11-05",
      })

    with self.assertRaisesRegex(ValueError, "posterior"):
      bot.registrar_incidencia({
        "tipo": "suspension",
        "no_empleado": "1001",
        "motivo": "incumplimiento",
        "fecha_inicio": "2026-11-05",
        "fecha_reingreso": "2026-11-05",
      })

  def test_registration_sends_to_employee_and_schedules_emergency_for_24_hours(self):
    fake_cursor = FakeCursor()
    enviar = Mock()
    with (
      patch.object(bot, "conectar_db", side_effect=lambda: FakeConnection(fake_cursor)),
      patch.object(bot, "enviar_template_whatsapp", enviar),
    ):
      resultado = bot.registrar_incidencia({
        "tipo": "indisciplina",
        "no_empleado": "1001",
        "categoria": "Defectos",
        "motivo": "Defectos",
      })

    enviar.assert_called_once_with(
      "8112345678",
      "indisciplina_segundo_colaborador",
      ["Defectos"],
    )
    self.assertTrue(resultado["contacto_emergencia_programado"])
    self.assertEqual(fake_cursor.insert_params[6], 2)
    self.assertEqual(fake_cursor.insert_params[10], "pendiente")
    self.assertIn("INTERVAL '24 hours'", fake_cursor.insert_sql)


class InternalEndpointTests(unittest.TestCase):
  def setUp(self):
    self.client = bot.app.test_client()

  def test_internal_endpoint_rejects_missing_or_invalid_token(self):
    with patch.dict(os.environ, {"CHATBOT_INTERNAL_TOKEN": "secret"}):
      response = self.client.post(
        "/api/internal/incidencias",
        json={"tipo": "baja_confirmada"},
        headers={"X-Internal-Token": "wrong"},
      )
    self.assertEqual(response.status_code, 401)

  def test_internal_endpoint_forwards_authorized_incidence(self):
    datos = {"tipo": "baja_confirmada", "no_empleado": "1001", "motivo": "fin"}
    resultado = {
      "incidencia_id": 1,
      "nombre_empleado": "Colaborador",
      "consecutivo_indisciplina": None,
      "contacto_emergencia_programado": False,
    }
    with (
      patch.dict(os.environ, {"CHATBOT_INTERNAL_TOKEN": "secret"}),
      patch.object(bot, "registrar_incidencia", return_value=resultado) as registrar,
    ):
      response = self.client.post(
        "/api/internal/incidencias",
        json=datos,
        headers={"X-Internal-Token": "secret"},
      )

    self.assertEqual(response.status_code, 201)
    self.assertEqual(response.get_json()["incidencia_id"], 1)
    registrar.assert_called_once_with(datos)


if __name__ == "__main__":
  unittest.main()
