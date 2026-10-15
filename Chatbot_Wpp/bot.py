import os
import re
from logging import getLogger

import psycopg2
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)
logger = getLogger(__name__)
SESSIONS = {}
WHATSAPP_API_TIMEOUT = 15


def conectar_db():
  host = os.getenv("POSTGRES_HOST", "postgres")
  if host == "postgres" and not os.path.exists("/.dockerenv"):
    host = "localhost"

  return psycopg2.connect(
    host=host,
    port=os.getenv("POSTGRES_PORT", "5432"),
    database=os.getenv("POSTGRES_DB", "rh_system"),
    user=os.getenv("POSTGRES_USER", "rh_app"),
    password=os.getenv("POSTGRES_PASSWORD", ""),
    connect_timeout=5,
    options="-c lc_messages=C",
  )


def extraer_mensaje(data):
  """Accept the existing normalized payload and Meta Cloud API webhooks."""
  sender = data.get("sender")
  text = data.get("text", "")

  if sender and text:
    return str(sender), str(text).strip()

  for entry in data.get("entry", []):
    for change in entry.get("changes", []):
      value = change.get("value", {})
      for message in value.get("messages", []):
        message_text = message.get("text", {}).get("body")
        if message_text:
          return str(message.get("from", "")), message_text.strip()
  return None, None


def buscar_colaborador(nombre, numero_empleado):
  with conectar_db() as conn:
    with conn.cursor() as cur:
      cur.execute(
        """
        SELECT EXISTS (
          SELECT 1
          FROM information_schema.columns
          WHERE table_schema = 'public'
            AND table_name = 'registro'
            AND column_name = 'estatus'
        )
        """
      )
      tiene_estatus = cur.fetchone()[0]
      filtro_estatus = (
        "AND COALESCE(NULLIF(UPPER(TRIM(r.estatus)), ''), 'ACTIVO') = 'ACTIVO'"
        if tiene_estatus
        else ""
      )
      cur.execute(
        f"""
        SELECT r.nombre_completo, i.no_empleado, i.puesto, i.area, i.turno
        FROM registro AS r
        JOIN ingresos_puesto AS i ON i.registro_id = r.id
        WHERE i.no_empleado::text = %s
          AND LOWER(TRIM(r.nombre_completo)) = LOWER(TRIM(%s))
          {filtro_estatus}
        LIMIT 1
        """,
        (numero_empleado, nombre),
      )
      return cur.fetchone()


def obtener_contactos_reclutamiento():
  with conectar_db() as conn:
    with conn.cursor() as cur:
      cur.execute(
        """
        SELECT nombre_notificacion, telefono_notificacion
        FROM usuarios_acceso
        WHERE telefono_notificacion IS NOT NULL
          AND telefono_notificacion <> ''
        LIMIT 2
        """
      )
      return cur.fetchall()


def enviar_respuesta(destinatario, texto):
  phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
  access_token = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()

  if not phone_number_id or not access_token:
    print(
      f"[WHATSAPP SIMULADO] Destino: {destinatario}\n"
      f"[WHATSAPP SIMULADO] Mensaje:\n{texto}",
      flush=True,
    )
    return jsonify({"status": "simulated", "success": True, "message": texto}), 200

  recipient = re.sub(r"\D", "", destinatario)
  api_version = os.getenv("WHATSAPP_API_VERSION", "v23.0")
  url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
  try:
    response = requests.post(
      url,
      headers={"Authorization": f"Bearer {access_token}"},
      json={
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "text",
        "text": {"preview_url": False, "body": texto},
      },
      timeout=WHATSAPP_API_TIMEOUT,
    )
    response.raise_for_status()
  except requests.RequestException:
    logger.exception("No se pudo enviar el mensaje por WhatsApp Cloud API")
    return jsonify({"status": "send_error"}), 502

  return jsonify({"status": "sent"}), 200


def bienvenida(remitente):
  SESSIONS[remitente] = {"paso": "menu_principal"}
  return enviar_respuesta(
    remitente,
    "¡Hola! Bienvenido al asistente de Cheong Woon Mexico.\n\n"
    "Selecciona una de estas opciones:\n\n"
    "1. Personal Activo\n"
    "2. Quiero ser parte del Equipo\n\n"
    "Envía el número de la opción deseada.",
  )


@app.route("/webhook-whatsapp", methods=["GET", "POST"])
def webhook_whatsapp():
  if request.method == "GET":
    verify_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "").strip()
    if not verify_token or request.args.get("hub.verify_token") != verify_token:
      return "Verificación no válida", 403
    return request.args.get("hub.challenge", ""), 200

  data = request.get_json(silent=True) or {}
  remitente, mensaje = extraer_mensaje(data)
  if not remitente or mensaje is None:
    return jsonify({"status": "ignored"}), 200

  mensaje = mensaje.strip()
  mensaje_normalizado = mensaje.casefold()
  if remitente not in SESSIONS or mensaje_normalizado in {"hola", "menu", "menú", "inicio", "0"}:
    return bienvenida(remitente)

  estado = SESSIONS[remitente]

  if estado["paso"] == "menu_principal":
    if mensaje == "1":
      estado["paso"] = "esperando_nombre"
      return enviar_respuesta(
        remitente,
        "PERSONAL ACTIVO\n\nPor favor, escribe tu nombre completo:",
      )
    if mensaje == "2":
      try:
        contactos = obtener_contactos_reclutamiento()
      except psycopg2.Error:
        logger.exception("No se pudieron consultar contactos de reclutamiento")
        contactos = []
      texto_contactos = "".join(f"• {nombre}: {telefono}\n" for nombre, telefono in contactos)
      if not texto_contactos:
        texto_contactos = "• Departamento de Reclutamiento Cheong Woon\n"
      SESSIONS.pop(remitente, None)
      return enviar_respuesta(
        remitente,
        "Puedes comunicarte directamente con Reclutamiento:\n\n"
        f"{texto_contactos}\nEscribe HOLA para volver al inicio.",
      )
    return enviar_respuesta(remitente, "Opción no válida. Envía 1 o 2.")

  if estado["paso"] == "esperando_nombre":
    if len(mensaje) < 3:
      return enviar_respuesta(remitente, "Escribe tu nombre completo para continuar.")
    estado["nombre_capturado"] = mensaje
    estado["paso"] = "esperando_numero_empleado"
    return enviar_respuesta(
      remitente,
      f"Gracias, {mensaje}.\n\nAhora ingresa tu número de empleado:",
    )

  if estado["paso"] == "esperando_numero_empleado":
    numero_empleado = mensaje.strip()
    try:
      colaborador = buscar_colaborador(estado["nombre_capturado"], numero_empleado)
    except psycopg2.Error:
      logger.exception("Error al validar los datos del colaborador")
      return enviar_respuesta(
        remitente,
        "No pudimos validar tus datos en este momento. Intenta de nuevo más tarde.",
      )

    if not colaborador:
      SESSIONS[remitente] = {"paso": "menu_principal"}
      return enviar_respuesta(
        remitente,
        "No encontramos una coincidencia activa con esos datos.\n\n"
        "Menú principal:\n"
        "1. Personal Activo\n"
        "2. Quiero ser parte del Equipo\n\n"
        "Envía el número de la opción deseada.",
      )

    estado.update(
      {
        "paso": "menu_colaborador",
        "nombre_real": colaborador[0],
        "no_empleado": colaborador[1],
        "puesto": colaborador[2],
        "area": colaborador[3],
        "turno": colaborador[4],
      }
    )
    return enviar_respuesta(
      remitente,
      f"Acceso confirmado. Bienvenido, {colaborador[0]}.\n\n"
      "Selecciona una opción:\n"
      "1. Ruta asignada\n"
      "2. Vacaciones\n"
      "3. Aviso de privacidad\n"
      "4. Solicitar entrada o salida\n\n"
      "Envía 0 para volver al menú principal.",
    )

  if estado["paso"] == "menu_colaborador":
    respuestas = {
      "1": (
        f"Información de asignación\n\nEmpleado: {estado['nombre_real']}\n"
        f"Área: {estado['area'] or 'No registrada'}\n"
        f"Turno: {estado['turno'] or 'No registrado'}\n\n"
        "Para consultar la ruta exacta, acude con tu supervisor de turno.\n\n"
        "Envía 0 para volver."
      ),
      "2": (
        "Vacaciones\nPara consultar tus días disponibles, acude a la"
        " ventanilla de Recursos Humanos.\n\nEnvía 0 para volver."
      ),
      "3": (
        "Aviso de privacidad\nTus datos personales se manejan de acuerdo"
        " con las políticas internas de Cheong Woon Mexico.\n\n"
        "Envía 0 para volver."
      ),
      "4": (
        "Para solicitar entrada o salida, dirígete con tu supervisor de"
        " área.\n\nEnvía 0 para volver."
      ),
    }
    if mensaje in respuestas:
      return enviar_respuesta(remitente, respuestas[mensaje])
    return enviar_respuesta(remitente, "Opción no válida. Envía un número del 1 al 4, o 0.")

  return bienvenida(remitente)


if __name__ == "__main__":
  app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))