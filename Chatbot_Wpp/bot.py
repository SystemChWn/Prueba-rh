import os
import re
import hmac
import threading
import time
from logging import getLogger
from datetime import date

import psycopg2
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request

app = Flask(__name__)
logger = getLogger(__name__)
SESSIONS = {}
WHATSAPP_API_TIMEOUT = 15
INCIDENCIAS_INDISCIPLINA = {
  "salidas sin permiso del area",
  "llegadas tarde al area del trabajo",
  "defectos",
  "no uso de equipo de seguridad",
  "desobedecer ordenes",
  "jugar en linea",
  "falta injustificada",
  "fumar sin permiso o en area no permitida",
  "uso del celular dentro del area",
  "tardar al regresar del comedor o del baño",
  "falta de respeto o acoso a compañero",
  "salir con comida a lineas",
  "otro motivo",
}
TIPOS_INCIDENCIA = {
  "suspension",
  "indisciplina",
  "carta_compromiso",
  "baja_confirmada",
}
CONFIRMACIONES_INCIDENCIA = {
  "confirmo",
  "confirmo de mensaje recibido",
  "confirmo recibido",
}

load_dotenv(override=True);


def conectar_db():
  host = os.getenv("POSTGRES_HOST", "postgres")
  if host == "postgres" and not os.path.exists("/.dockerenv"):
    host = "localhost"
  return psycopg2.connect(
    host=host,
    port=os.getenv("POSTGRES_PORT", "5432"),
    database=os.getenv("POSTGRES_DB", "rh_system"),
    user=os.getenv("POSTGRES_USER", "rh_app"),
    password=os.getenv("POSTGRES_PASSWORD", "S1s73m4s!"),
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


def asegurar_tabla_credenciales(cur):
  cur.execute(
    """
    CREATE TABLE IF NOT EXISTS chatbot_credenciales (
      registro_id INTEGER PRIMARY KEY REFERENCES registro(id) ON DELETE CASCADE,
      contrasena TEXT NOT NULL,
      updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """
  )


def buscar_colaborador(numero_empleado):
  with conectar_db() as conn:
    with conn.cursor() as cur:
      asegurar_tabla_credenciales(cur)
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
        SELECT r.id, r.nombre_completo, i.no_empleado, i.puesto, i.area, i.turno,
               c.contrasena
        FROM registro AS r
        JOIN ingresos_puesto AS i ON i.registro_id = r.id
        LEFT JOIN chatbot_credenciales AS c ON c.registro_id = r.id
        WHERE i.no_empleado::text = %s
          {filtro_estatus}
        LIMIT 1
        """,
        (numero_empleado,),
      )
      return cur.fetchone()


def guardar_contrasena_colaborador(registro_id, contrasena):
  with conectar_db() as conn:
    with conn.cursor() as cur:
      asegurar_tabla_credenciales(cur)
      cur.execute(
        """
        INSERT INTO chatbot_credenciales (registro_id, contrasena)
        VALUES (%s, %s)
        ON CONFLICT (registro_id) DO UPDATE
        SET contrasena = EXCLUDED.contrasena,
            updated_at = CURRENT_TIMESTAMP
        """,
        (registro_id, contrasena),
      )


def obtener_contactos_reclutamiento():
  with conectar_db() as conn:
    with conn.cursor() as cur:
      cur.execute(
        """
        SELECT TRIM(telefono)
        FROM personal_reclutamiento
        WHERE telefono IS NOT NULL
          AND NULLIF(TRIM(telefono), '') IS NOT NULL
        ORDER BY id ASC
        """
      )
      return [row[0] for row in cur.fetchall() if row[0]]


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
    if "response" in locals():
      print(f"RESPUESTA META: {response.text}", flush=True)
    logger.exception("No se pudo enviar el mensaje por WhatsApp Cloud API")
    return jsonify({"status": "send_error"}), 502

  return jsonify({"status": "sent"}), 200


class ErrorEnvioWhatsApp(Exception):
  def __init__(self, mensaje, incidencia_id=None):
    super().__init__(mensaje)
    self.incidencia_id = incidencia_id


def telefono_whatsapp(numero):
  digitos = re.sub(r"\D", "", str(numero or ""))
  if len(digitos) == 10:
    return f"52{digitos}"
  if len(digitos) == 12 and digitos.startswith("52"):
    return digitos
  if len(digitos) == 13 and digitos.startswith("521"):
    return f"52{digitos[-10:]}"
  raise ValueError("El número telefónico debe tener 10 dígitos o incluir el código de México.")


def numero_local_whatsapp(numero):
  digitos = re.sub(r"\D", "", str(numero or ""))
  if len(digitos) == 13 and digitos.startswith("521"):
    return digitos[-10:]
  if len(digitos) == 12 and digitos.startswith("52"):
    return digitos[-10:]
  return digitos


def enviar_template_whatsapp(destinatario, nombre_template, parametros):
  phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
  access_token = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
  if not phone_number_id or not access_token:
    raise ErrorEnvioWhatsApp(
      "Configura WHATSAPP_PHONE_NUMBER_ID y WHATSAPP_ACCESS_TOKEN para enviar avisos."
    )

  api_version = os.getenv("WHATSAPP_API_VERSION", "v23.0")
  template_prefix = os.getenv("WHATSAPP_NOTIFICATION_TEMPLATE_PREFIX", "rh_aviso").strip()
  language = os.getenv("WHATSAPP_TEMPLATE_LANGUAGE", "es_MX").strip()
  template_name = f"{template_prefix}_{nombre_template}"
  url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
  body_parameters = [
    {"type": "text", "text": str(parametro)}
    for parametro in parametros
  ]
  payload = {
    "messaging_product": "whatsapp",
    "to": telefono_whatsapp(destinatario),
    "type": "template",
    "template": {
      "name": template_name,
      "language": {"code": language},
    },
  }
  if body_parameters:
    payload["template"]["components"] = [
      {"type": "body", "parameters": body_parameters},
    ]

  try:
    response = requests.post(
      url,
      headers={"Authorization": f"Bearer {access_token}"},
      json=payload,
      timeout=WHATSAPP_API_TIMEOUT,
    )
    response.raise_for_status()
  except requests.RequestException as error:
    logger.exception("No se pudo enviar el template %s por WhatsApp", template_name)
    raise ErrorEnvioWhatsApp(
      f"No se pudo enviar el template de WhatsApp {template_name}."
    ) from error


def asegurar_tabla_incidencias(cur):
  cur.execute(
    """
    CREATE TABLE IF NOT EXISTS incidencias_chatbot (
      id BIGSERIAL PRIMARY KEY,
      registro_id INTEGER NOT NULL REFERENCES registro(id) ON DELETE CASCADE,
      no_empleado VARCHAR(50) NOT NULL,
      tipo VARCHAR(40) NOT NULL,
      motivo TEXT NOT NULL,
      fecha_inicio DATE,
      fecha_reingreso DATE,
      consecutivo_indisciplina INTEGER,
      telefono_colaborador VARCHAR(30) NOT NULL,
      nombre_contacto_emergencia VARCHAR(255),
      telefono_contacto_emergencia VARCHAR(30),
      estado_colaborador VARCHAR(20) NOT NULL DEFAULT 'pendiente',
      respuesta_colaborador_at TIMESTAMP,
      estado_contacto_emergencia VARCHAR(20) NOT NULL DEFAULT 'pendiente',
      contacto_programado_at TIMESTAMP,
      contacto_enviado_at TIMESTAMP,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """
  )


def fecha_incidente(valor, campo, requerida=False):
  if not valor:
    if requerida:
      raise ValueError(f"El campo {campo} es obligatorio.")
    return None
  try:
    return date.fromisoformat(str(valor))
  except ValueError as error:
    raise ValueError(f"El campo {campo} debe tener formato AAAA-MM-DD.") from error


def construir_mensaje_colaborador(tipo, motivo, fecha_inicio, fecha_reingreso, consecutivo):
  if tipo == "suspension":
    return "suspension_colaborador", [
      fecha_inicio.strftime("%d/%m/%Y"),
      motivo,
      fecha_reingreso.strftime("%d/%m/%Y"),
    ]
  if tipo == "indisciplina":
    sufijo = "indisciplina_segundo_colaborador" if consecutivo >= 2 else "indisciplina_colaborador"
    return sufijo, [motivo]
  if tipo == "carta_compromiso":
    return "carta_compromiso_colaborador", []
  return "baja_confirmada_colaborador", [motivo]


def construir_mensaje_emergencia(tipo, nombre, motivo, fecha_inicio, fecha_reingreso, consecutivo):
  if tipo == "suspension":
    return "suspension_emergencia", [
      nombre,
      fecha_inicio.strftime("%d/%m/%Y"),
      motivo,
      fecha_reingreso.strftime("%d/%m/%Y"),
    ]
  if tipo == "indisciplina":
    sufijo = "indisciplina_segundo_emergencia" if consecutivo >= 2 else "indisciplina_emergencia"
    return sufijo, [nombre, motivo]
  return "carta_compromiso_emergencia", [nombre]


def registrar_incidencia(datos):
  tipo = str(datos.get("tipo", "")).strip()
  no_empleado = str(datos.get("no_empleado", "")).strip()
  motivo = str(datos.get("motivo", "")).strip()

  if tipo not in TIPOS_INCIDENCIA:
    raise ValueError("El tipo de incidencia no es válido.")
  if not no_empleado:
    raise ValueError("El número de empleado es obligatorio.")
  if len(no_empleado) > 50:
    raise ValueError("El número de empleado no es válido.")
  if tipo != "carta_compromiso" and not motivo:
    raise ValueError("El motivo de la incidencia es obligatorio.")
  if len(motivo) > 1000:
    raise ValueError("El motivo no puede superar 1000 caracteres.")

  if tipo == "indisciplina":
    categoria = str(datos.get("categoria", "")).strip()
    if categoria.casefold() not in INCIDENCIAS_INDISCIPLINA:
      raise ValueError("Selecciona un motivo de indisciplina válido.")
    if categoria.casefold() == "otro motivo" and not motivo:
      raise ValueError("Especifica el motivo de la incidencia.")
    if categoria.casefold() != "otro motivo":
      motivo = categoria

  fecha_inicio = fecha_incidente(
    datos.get("fecha_inicio"),
    "fecha_inicio",
    requerida=tipo == "suspension",
  )
  fecha_reingreso = fecha_incidente(
    datos.get("fecha_reingreso"),
    "fecha_reingreso",
    requerida=tipo == "suspension",
  )
  if fecha_inicio and fecha_reingreso and fecha_reingreso <= fecha_inicio:
    raise ValueError("La fecha de reingreso debe ser posterior a la fecha de inicio.")

  with conectar_db() as conn:
    with conn.cursor() as cur:
      asegurar_tabla_incidencias(cur)
      cur.execute(
        """
        SELECT r.id, r.nombre_completo, r.tel_movil, r.nombre_emergencia,
               r.telefono_emergencia, i.no_empleado
        FROM registro AS r
        JOIN ingresos_puesto AS i ON i.registro_id = r.id
        WHERE i.no_empleado::text = %s
        ORDER BY i.id DESC
        LIMIT 1
        FOR UPDATE OF r
        """,
        (no_empleado,),
      )
      colaborador = cur.fetchone()
      if not colaborador:
        raise LookupError("No se encontró un empleado con ese número.")

      registro_id, nombre, telefono, nombre_emergencia, telefono_emergencia, numero = colaborador
      telefono_mostrado = re.sub(r"\D", "", str(telefono or ""))
      telefono_mostrado = f"******{telefono_mostrado[-4:]}" if telefono_mostrado else "sin teléfono"
      print(
        f"DEBUG EMPLEADO: Nombre={nombre}, TelefonoReal={telefono_mostrado}",
        flush=True,
      )
      if not str(telefono or "").strip():
        raise ValueError("El colaborador no tiene un teléfono móvil registrado.")
      try:
        telefono_whatsapp(telefono)
      except ValueError as error:
        raise ValueError("El teléfono móvil del colaborador no es válido.") from error

      consecutivo = None
      if tipo == "indisciplina":
        cur.execute(
          """
          SELECT COUNT(*)
          FROM incidencias_chatbot
          WHERE registro_id = %s AND tipo = 'indisciplina'
          """,
          (registro_id,),
        )
        consecutivo = cur.fetchone()[0] + 1

      if tipo == "suspension":
        mensaje_texto = (
          f"Buen día. Te notificamos que estarás en suspensión a partir del "
          f"{fecha_inicio.strftime('%d/%m/%Y')} por el motivo de: {motivo}, "
          f"y regresarás a tus labores el {fecha_reingreso.strftime('%d/%m/%Y')}.\n"
          "Confirma de mensaje recibido."
        )
      elif tipo == "indisciplina":
        consecuencia = (
          "Has acumulado tu segundo reporte, por lo que procede tu suspensión laboral."
          if consecutivo >= 2
          else "Se le recuerda que la acumulación de 2 reportes será motivo de suspensión."
        )
        mensaje_texto = (
          f"Buen día. Te notificamos que se te ha generado un reporte de indisciplina "
          f"por el motivo de: {motivo}.\n{consecuencia}\n"
          "Confirma de mensaje recibido."
        )
      elif tipo == "carta_compromiso":
        mensaje_texto = (
          "Buen día. Se ha registrado tu carta compromiso en el sistema.\n"
          "Te recordamos que si acumulas una falta más, será motivo de baja directa de la empresa.\n"
          "Confirma de mensaje recibido."
        )
      else:
        mensaje_texto = (
          f"Buen día. Te notificamos que has sido dado de baja de la empresa "
          f"por el motivo de: {motivo}.\n"
          "Favor de presentarte en la oficina de Recursos Humanos para realizar el trámite final.\n"
          "Confirma de mensaje recibido."
        )

      requiere_emergencia = tipo != "baja_confirmada"
      telefono_emergencia_valido = False
      if requiere_emergencia and str(telefono_emergencia or "").strip():
        try:
          telefono_whatsapp(telefono_emergencia)
          telefono_emergencia_valido = True
        except ValueError:
          logger.warning(
            "El contacto de emergencia del empleado %s tiene un teléfono no válido",
            numero,
          )
      estado_emergencia = (
        "pendiente"
        if requiere_emergencia and telefono_emergencia_valido
        else "sin_contacto"
        if requiere_emergencia
        else "no_aplica"
      )
      template_emergencia = parametros_emergencia = None
      if estado_emergencia == "pendiente":
        template_emergencia, parametros_emergencia = construir_mensaje_emergencia(
          tipo,
          nombre,
          motivo,
          fecha_inicio,
          fecha_reingreso,
          consecutivo,
        )

      cur.execute(
        """
        INSERT INTO incidencias_chatbot (
          registro_id, no_empleado, tipo, motivo, fecha_inicio, fecha_reingreso,
          consecutivo_indisciplina, telefono_colaborador, nombre_contacto_emergencia,
          telefono_contacto_emergencia, estado_contacto_emergencia
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
          registro_id,
          numero,
          tipo,
          motivo,
          fecha_inicio,
          fecha_reingreso,
          consecutivo,
          telefono,
          nombre_emergencia,
          telefono_emergencia,
          estado_emergencia,
        ),
      )
      incidencia_id = cur.fetchone()[0]

  try:
    if not os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip() or not os.getenv(
      "WHATSAPP_ACCESS_TOKEN", ""
    ).strip():
      raise ErrorEnvioWhatsApp(
        "Configura WHATSAPP_PHONE_NUMBER_ID y WHATSAPP_ACCESS_TOKEN para enviar avisos."
      )
    _, codigo_envio = enviar_respuesta(telefono_whatsapp(telefono), mensaje_texto)
    if codigo_envio != 200:
      raise ErrorEnvioWhatsApp("La API de WhatsApp no confirmó el envío.")
  except ErrorEnvioWhatsApp as error:
    with conectar_db() as conn:
      with conn.cursor() as cur:
        cur.execute(
          "UPDATE incidencias_chatbot SET estado_colaborador = 'error' WHERE id = %s",
          (incidencia_id,),
        )
    raise ErrorEnvioWhatsApp(
      f"La incidencia se guardó con folio {incidencia_id}, pero no se pudo enviar el aviso.",
      incidencia_id=incidencia_id,
    ) from error

  with conectar_db() as conn:
    with conn.cursor() as cur:
      cur.execute(
        """
        UPDATE incidencias_chatbot
        SET estado_colaborador = 'enviado',
            contacto_programado_at = CASE
              WHEN estado_contacto_emergencia = 'pendiente'
                THEN CURRENT_TIMESTAMP + INTERVAL '24 hours'
              ELSE NULL
            END
        WHERE id = %s
        """,
        (incidencia_id,),
      )

  return {
    "incidencia_id": incidencia_id,
    "nombre_empleado": nombre,
    "consecutivo_indisciplina": consecutivo,
    "contacto_emergencia_programado": estado_emergencia == "pendiente",
    "template_emergencia": template_emergencia,
    "parametros_emergencia": parametros_emergencia,
    "telefono_emergencia": telefono_emergencia,
  }


def confirmar_mensaje_incidencia(remitente):
  telefono_local = numero_local_whatsapp(remitente)
  if len(telefono_local) < 10:
    return False

  with conectar_db() as conn:
    with conn.cursor() as cur:
      asegurar_tabla_incidencias(cur)
      cur.execute(
        """
        SELECT id
        FROM incidencias_chatbot
        WHERE RIGHT(REGEXP_REPLACE(telefono_colaborador, '[^0-9]', '', 'g'), 10) = %s
          AND respuesta_colaborador_at IS NULL
          AND estado_colaborador IN ('pendiente', 'enviado')
        ORDER BY id DESC
        LIMIT 1
        FOR UPDATE
        """,
        (telefono_local[-10:],),
      )
      incidencia = cur.fetchone()
      if not incidencia:
        return False
      cur.execute(
        """
        UPDATE incidencias_chatbot
        SET respuesta_colaborador_at = CURRENT_TIMESTAMP,
            estado_contacto_emergencia = CASE
              WHEN estado_contacto_emergencia = 'pendiente' THEN 'cancelado'
              ELSE estado_contacto_emergencia
            END
        WHERE id = %s
        """,
        (incidencia[0],),
      )
  return True


def enviar_contactos_emergencia_pendientes():
  with conectar_db() as conn:
    with conn.cursor() as cur:
      asegurar_tabla_incidencias(cur)
      cur.execute(
        """
        SELECT i.id, i.telefono_contacto_emergencia, i.tipo, i.nombre_contacto_emergencia,
               i.motivo, i.fecha_inicio, i.fecha_reingreso, i.consecutivo_indisciplina,
               r.nombre_completo
        FROM incidencias_chatbot AS i
        JOIN registro AS r ON r.id = i.registro_id
        WHERE i.estado_contacto_emergencia = 'pendiente'
          AND i.estado_colaborador = 'enviado'
          AND i.respuesta_colaborador_at IS NULL
          AND i.contacto_programado_at <= CURRENT_TIMESTAMP
        ORDER BY i.contacto_programado_at, i.id
        LIMIT 25
        FOR UPDATE OF i SKIP LOCKED
        """
      )
      pendientes = cur.fetchall()
      for incidencia in pendientes:
        (
          incidencia_id,
          telefono_emergencia,
          tipo,
          nombre_emergencia,
          motivo,
          fecha_inicio,
          fecha_reingreso,
          consecutivo,
          nombre_empleado,
        ) = incidencia
        template, parametros = construir_mensaje_emergencia(
          tipo,
          nombre_empleado,
          motivo,
          fecha_inicio,
          fecha_reingreso,
          consecutivo,
        )
        try:
          enviar_template_whatsapp(telefono_emergencia, template, parametros)
        except (ErrorEnvioWhatsApp, ValueError):
          logger.exception(
            "No se pudo notificar al contacto de emergencia de la incidencia %s",
            incidencia_id,
          )
          continue
        cur.execute(
          """
          UPDATE incidencias_chatbot
          SET estado_contacto_emergencia = 'enviado',
              contacto_enviado_at = CURRENT_TIMESTAMP
          WHERE id = %s
          """,
          (incidencia_id,),
        )


def ejecutar_programador_notificaciones():
  intervalo = max(15, int(os.getenv("WHATSAPP_NOTIFICATION_POLL_SECONDS", "60")))
  while True:
    try:
      enviar_contactos_emergencia_pendientes()
    except psycopg2.Error:
      logger.exception("No se pudieron revisar los avisos pendientes de emergencia")
    time.sleep(intervalo)


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
  confirmacion_normalizada = re.sub(r"[.!¡?¿]+$", "", mensaje_normalizado).strip()

  if confirmacion_normalizada in CONFIRMACIONES_INCIDENCIA:
    try:
      if confirmar_mensaje_incidencia(remitente):
        return enviar_respuesta(
          remitente,
          "Gracias. Confirmamos que recibimos tu mensaje.",
        )
    except psycopg2.Error:
      logger.exception("No se pudo registrar la confirmación de la incidencia")
      return jsonify({"status": "confirmation_error"}), 500

  if remitente not in SESSIONS or mensaje_normalizado in {"hola", "menu", "menú", "inicio", "0"}:
    return bienvenida(remitente)

  estado = SESSIONS[remitente]

  if estado["paso"] == "menu_principal":
    if mensaje == "1":
      estado["paso"] = "esperando_numero_empleado"
      return enviar_respuesta(
        remitente,
        "Ingresa tu número de empleado:",
      )
    if mensaje == "2":
      try:
        contactos = obtener_contactos_reclutamiento()
      except psycopg2.Error:
        logger.exception("No se pudieron consultar contactos de reclutamiento")
        contactos = []
      texto_respuesta = "Para recibir orientación, ponte en contacto con nuestro equipo de Reclutamiento."
      if contactos:
        texto_contactos = "\n".join(
          f"Contacto {indice}: {telefono}"
          for indice, telefono in enumerate(contactos, start=1)
        )
        texto_respuesta += f"\n\n{texto_contactos}"
      texto_respuesta += "\n\n¡Gracias por tu interés en formar parte de Cheong Woon México! 😊"
      SESSIONS.pop(remitente, None)
      return enviar_respuesta(remitente, texto_respuesta)
    return enviar_respuesta(remitente, "Opción no válida. Envía 1 o 2.")

  if estado["paso"] == "esperando_numero_empleado":
    numero_empleado = mensaje.strip()
    if not numero_empleado:
      return enviar_respuesta(remitente, "Ingresa un número de empleado válido.")
    try:
      colaborador = buscar_colaborador(numero_empleado)
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
        "No encontramos un empleado activo con ese número.\n\n"
        "Menú principal:\n"
        "1. Personal Activo\n"
        "2. Quiero ser parte del Equipo\n\n"
        "Envía el número de la opción deseada.",
      )

    estado.update({
      "registro_id": colaborador[0],
      "nombre_real": colaborador[1],
      "no_empleado": colaborador[2],
      "puesto": colaborador[3],
      "area": colaborador[4],
      "turno": colaborador[5],
    })
    if not colaborador[6]:
      estado["paso"] = "creando_contrasena"
      return enviar_respuesta(
        remitente,
        f"Acceso confirmado. Bienvenido, {colaborador[1]}.\n\n"
        "Crea una contraseña personal de al menos 8 caracteres:",
      )
    estado["contrasena_esperada"] = colaborador[6]
    estado["paso"] = "esperando_contrasena"
    return enviar_respuesta(remitente, "Ingresa tu contraseña personal para continuar.")

  if estado["paso"] == "creando_contrasena":
    if len(mensaje) < 8:
      return enviar_respuesta(remitente, "La contraseña debe tener al menos 8 caracteres. Intenta crear otra:")
    try:
      guardar_contrasena_colaborador(estado["registro_id"], mensaje)
    except psycopg2.Error:
      logger.exception("No se pudo guardar la contraseña del colaborador")
      return enviar_respuesta(remitente, "No pudimos guardar tu contraseña. Intenta de nuevo más tarde.")
    return enviar_respuesta(
      remitente,
      "Tu contraseña quedó guardada.\n\n" + mensaje_menu_colaborador(estado),
    )

  if estado["paso"] == "esperando_contrasena":
    if mensaje != estado["contrasena_esperada"]:
      return enviar_respuesta(remitente, "La contraseña no coincide. Inténtalo de nuevo o envía 0 para volver al menú principal.")
    return enviar_respuesta(remitente, mensaje_menu_colaborador(estado))

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
    }
    if mensaje in respuestas:
      return enviar_respuesta(remitente, respuestas[mensaje])
    return enviar_respuesta(remitente, "Opción no válida. Envía un número del 1 al 3, o 0.")

  return bienvenida(remitente)


@app.route("/api/internal/incidencias", methods=["POST"])
def notificar_incidencia():
  token_configurado = os.getenv("CHATBOT_INTERNAL_TOKEN", "").strip()
  token_recibido = request.headers.get("X-Internal-Token", "").strip()
  if not token_configurado or not hmac.compare_digest(token_configurado, token_recibido):
    return jsonify({"error": "No autorizado."}), 401

  datos = request.get_json(silent=True)
  if not isinstance(datos, dict):
    return jsonify({"error": "Se requiere un objeto JSON."}), 400
  try:
    resultado = registrar_incidencia(datos)
  except ValueError as error:
    return jsonify({"error": str(error)}), 400
  except LookupError as error:
    return jsonify({"error": str(error)}), 404
  except ErrorEnvioWhatsApp as error:
    respuesta = {"error": str(error)}
    if error.incidencia_id is not None:
      respuesta["incidencia_id"] = error.incidencia_id
    return jsonify(respuesta), 502
  except psycopg2.Error:
    logger.exception("No se pudo registrar la incidencia")
    return jsonify({"error": "No se pudo guardar la incidencia."}), 500

  return jsonify({
    "status": "sent",
    "incidencia_id": resultado["incidencia_id"],
    "nombre_empleado": resultado["nombre_empleado"],
    "consecutivo_indisciplina": resultado["consecutivo_indisciplina"],
    "contacto_emergencia_programado": resultado["contacto_emergencia_programado"],
  }), 201


def mensaje_menu_colaborador(estado):
  estado["paso"] = "menu_colaborador"
  return (
    f"Acceso confirmado. Bienvenido, {estado['nombre_real']}.\n\n"
    "Selecciona una opción:\n"
    "1. Ruta asignada\n"
    "2. Vacaciones\n"
    "3. Aviso de privacidad\n\n"
    "Envía 0 para volver al menú principal."
  )


if __name__ == "__main__":
  hilo_notificaciones = threading.Thread(
    target=ejecutar_programador_notificaciones,
    name="notificaciones-emergencia",
    daemon=True,
  )
  hilo_notificaciones.start()
  app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))