"""Módulo de Seguridad, códigos QR de permisos, cola de WhatsApp y exportación."""

import base64
import io
import os
import re
import secrets
import textwrap
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
import zxingcpp
from flask import Blueprint, Response, g, jsonify, request, send_file
from PIL import Image, ImageDraw, ImageFont

ZONA_MX = ZoneInfo('America/Mexico_City')


def ahora_mx():
    return datetime.now(ZONA_MX)


def _cargar_fuente(tamano: int):
    rutas = [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        'C:\\Windows\\Fonts\\arial.ttf',
    ]
    for ruta in rutas:
        if os.path.exists(ruta):
            try:
                return ImageFont.truetype(ruta, tamano)
            except OSError:
                pass
    try:
        return ImageFont.load_default(size=tamano)
    except TypeError:
        return ImageFont.load_default()


def asegurar_tabla_whatsapp_cola(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS whatsapp_cola (
            id BIGSERIAL PRIMARY KEY,
            telefono VARCHAR(40) NOT NULL,
            mensaje TEXT NOT NULL,
            imagen_base64 TEXT,
            incidencia_id BIGINT,
            estado VARCHAR(20) NOT NULL DEFAULT 'pendiente',
            intentos SMALLINT NOT NULL DEFAULT 0,
            error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            enviado_at TIMESTAMPTZ
        )
        """
    )
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_whatsapp_cola_estado
        ON whatsapp_cola (estado, id)
        """
    )


def extraer_iniciales(nombre_completo: str) -> str:
    palabras = [p for p in re.split(r'\s+', str(nombre_completo or '').strip()) if p]
    iniciales = ''.join(re.sub(r'[^A-Za-zÁÉÍÓÚÑÜáéíóúñü]', '', p)[:1] for p in palabras)
    limpias = iniciales.upper() or 'EMP'
    return limpias[:6]


def generar_folio(cur, nombre_completo: str, no_empleado: str) -> str:
    """Genera folio único: INICIALES-YYYYMMDD-NO_EMP (con sufijo si ya existe ese día)."""
    iniciales = extraer_iniciales(nombre_completo)
    fecha_hoy = ahora_mx().strftime('%Y%m%d')
    emp_limpio = re.sub(r'[^A-Za-z0-9]', '', str(no_empleado or '0')) or '0'
    base = f"{iniciales}-{fecha_hoy}-{emp_limpio}"
    cur.execute("SELECT 1 FROM incidencias_empleado WHERE folio = %s LIMIT 1", (base,))
    if not cur.fetchone():
        return base
    for _ in range(10):
        sufijo = secrets.token_hex(2).upper()
        candidato = f"{base}-{sufijo}"
        cur.execute("SELECT 1 FROM incidencias_empleado WHERE folio = %s LIMIT 1", (candidato,))
        if not cur.fetchone():
            return candidato
    return f"{base}-{int(datetime.now(timezone.utc).timestamp()) % 10000}"


def _etiqueta_tipo_permiso(tipo: str, opcion: str | None = None) -> str:
    if tipo == 'entrada-salida':
        return 'Entrada' if opcion == 'entrada' else 'Salida'
    etiquetas = {
        'suspension': 'Suspensión',
        'carta-suspension': 'Carta Compromiso',
        'indisciplina': 'Indisciplina',
        'vacaciones': 'Vacaciones',
        'incapacidad': 'Incapacidad',
        'baja': 'Baja Confirmada',
    }
    return etiquetas.get(tipo, tipo)


def _fecha_espanol(fecha_dt=None) -> str:
    dt = fecha_dt or ahora_mx()
    meses = (
        'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
        'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
    )
    return f"{dt.day} de {meses[dt.month - 1]} de {dt.year}"


def _decodificar_foto(foto_base64: str | None):
    if not foto_base64:
        return None
    raw = str(foto_base64).strip()
    if ',' in raw and raw.lower().startswith('data:'):
        raw = raw.split(',', 1)[1]
    try:
        datos = base64.b64decode(raw)
        return Image.open(io.BytesIO(datos)).convert('RGB')
    except Exception:
        return None


def _dibujar_logo_cw(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
    """Dibuja el logo estilizado CW (azul marino + punto rojo) como en la plantilla."""
    azul = '#1E1B84'
    rojo = '#EF4444'
    # Arco C
    draw.arc([x, y + 6, x + 56, y + 62], start=50, end=310, fill=azul, width=10)
    # Punto rojo superior
    draw.ellipse([x + 30, y - 2, x + 52, y + 20], fill=rojo)
    # Trazo W
    draw.line([(x + 48, y + 44), (x + 58, y + 58), (x + 68, y + 46), (x + 78, y + 58), (x + 90, y + 44)],
              fill=azul, width=9, joint='curve')


def _dibujar_icono_inferior(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
    """Icono pequeño azul/rojo de Cheong Woon en la esquina inferior derecha."""
    azul = '#1E1B84'
    rojo = '#EF4444'
    draw.ellipse([x + 6, y, x + 22, y + 16], fill=azul)
    draw.pieslice([x, y + 16, x + 28, y + 38], start=180, end=360, fill=azul)
    draw.ellipse([x + 22, y + 10, x + 34, y + 22], fill=rojo)
    draw.pieslice([x + 18, y + 22, x + 38, y + 38], start=180, end=360, fill=rojo)


def generar_imagen_qr_permiso(
    nombre_completo: str,
    no_empleado: str,
    tipo_permiso: str,
    fecha_texto: str,
    folio: str,
    foto_base64: str | None = None,
) -> bytes:
    """Genera tarjeta grande PNG idéntica al diseño: logo CW, nombre/N°Emp izq, foto der, QR centro y leyenda abajo."""
    ancho, alto = 740, 960
    img = Image.new('RGB', (ancho, alto), '#FFFFFF')
    draw = ImageDraw.Draw(img)

    draw.rectangle([10, 10, ancho - 10, alto - 10], outline='#94A3B8', width=2)
    _dibujar_logo_cw(draw, 44, 34)

    f_datos = _cargar_fuente(23)
    f_nombre = _cargar_fuente(22)
    f_folio = _cargar_fuente(18)
    f_legal = _cargar_fuente(18)

    # Foto arriba a la derecha
    foto_x, foto_y, foto_w, foto_h = ancho - 205, 36, 155, 190
    foto_img = _decodificar_foto(foto_base64)
    if foto_img is not None:
        foto_fit = foto_img.copy()
        foto_fit.thumbnail((foto_w - 4, foto_h - 4))
        px = foto_x + (foto_w - foto_fit.width) // 2
        py = foto_y + (foto_h - foto_fit.height) // 2
        img.paste(foto_fit, (px, py))
    else:
        draw.text((foto_x + foto_w // 2, foto_y + foto_h // 2), 'SIN FOTO', fill='#64748B', font=f_folio, anchor='mm')
    draw.rectangle([foto_x, foto_y, foto_x + foto_w, foto_y + foto_h], outline='#64748B', width=2)

    # Nombre y N° Emp a la izquierda debajo del logo
    nombre_lineas = textwrap.wrap(f"Nombre: {str(nombre_completo or '').strip()}", width=34)[:2]
    y_txt = 128
    for lin in nombre_lineas:
        draw.text((44, y_txt), lin, fill='#1E293B', font=f_nombre, anchor='lm')
        y_txt += 30
    draw.text((44, max(y_txt + 12, 198)), f"N° Emp: {no_empleado}", fill='#1E293B', font=f_datos, anchor='lm')

    # Código QR centrado con borde morado/azul
    bc = zxingcpp.create_barcode(folio, zxingcpp.BarcodeFormat.QRCode)
    qr_arr = zxingcpp.write_barcode_to_image(bc, scale=10, add_quiet_zones=True)
    qr_pil = Image.fromarray(qr_arr).convert('RGB')
    qr_tam = 380
    qr_pil = qr_pil.resize((qr_tam, qr_tam), Image.NEAREST)
    qx = (ancho - qr_tam) // 2
    qy = 258
    img.paste(qr_pil, (qx, qy))
    draw.rectangle([qx - 6, qy - 6, qx + qr_tam + 6, qy + qr_tam + 6], outline='#9333EA', width=3)

    draw.text((ancho // 2, qy + qr_tam + 22), f"Folio: {folio}", fill='#334155', font=f_folio, anchor='mm')

    # Texto legal abajo
    texto_legal = (
        f"Permiso de {tipo_permiso}, válido únicamente el {fecha_texto}, "
        "autorizado por Recursos Humanos de Cheong Woon. "
        "Presente su gafete físico junto con este código QR al guardia en caseta "
        "para validar y registrar su permiso. Este código es intransferible y expira "
        "al usarse o al finalizar el día."
    )
    y_legal = qy + qr_tam + 54
    for lin in textwrap.wrap(texto_legal, width=58):
        draw.text((ancho // 2, y_legal), lin, fill='#0F172A', font=f_legal, anchor='mm')
        y_legal += 24

    _dibujar_icono_inferior(draw, ancho - 62, alto - 58)

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def generar_imagen_tarjeta_chica(
    nombre_completo: str,
    no_empleado: str,
    foto_base64: str | None = None,
) -> bytes:
    """Genera tarjeta apaisada (formato chico sin QR) para mostrar en Telegram a los guardias."""
    ancho, alto = 680, 310
    img = Image.new('RGB', (ancho, alto), '#FFFFFF')
    draw = ImageDraw.Draw(img)

    draw.rectangle([8, 8, ancho - 8, alto - 8], outline='#94A3B8', width=2)
    _dibujar_logo_cw(draw, 36, 30)

    f_nombre = _cargar_fuente(22)
    f_datos = _cargar_fuente(22)
    f_sin = _cargar_fuente(16)

    # Foto derecha
    foto_x, foto_y, foto_w, foto_h = ancho - 205, 32, 150, 185
    foto_img = _decodificar_foto(foto_base64)
    if foto_img is not None:
        foto_fit = foto_img.copy()
        foto_fit.thumbnail((foto_w - 4, foto_h - 4))
        px = foto_x + (foto_w - foto_fit.width) // 2
        py = foto_y + (foto_h - foto_fit.height) // 2
        img.paste(foto_fit, (px, py))
    else:
        draw.text((foto_x + foto_w // 2, foto_y + foto_h // 2), 'SIN FOTO', fill='#64748B', font=f_sin, anchor='mm')
    draw.rectangle([foto_x, foto_y, foto_x + foto_w, foto_y + foto_h], outline='#64748B', width=2)

    # Nombre y N° Emp izquierda abajo
    nombre_lineas = textwrap.wrap(f"Nombre: {str(nombre_completo or '').strip()}", width=32)[:2]
    y_txt = 152
    for lin in nombre_lineas:
        draw.text((36, y_txt), lin, fill='#1E293B', font=f_nombre, anchor='lm')
        y_txt += 30
    draw.text((36, max(y_txt + 16, 226)), f"N° Emp: {no_empleado}", fill='#1E293B', font=f_datos, anchor='lm')

    _dibujar_icono_inferior(draw, ancho - 56, alto - 52)

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def formatear_mensaje_whatsapp(tipo: str, nombre: str, incidencia: dict) -> str:
    """Devuelve el texto exacto solicitado para cada tipo de incidencia."""
    fecha_creacion = _fecha_espanol(ahora_mx())
    motivo = incidencia.get('motivo') or ''
    dias = incidencia.get('dias') or ''
    f_ini = incidencia.get('fecha_inicio') or ''
    f_fin = incidencia.get('fecha_reingreso') or ''

    if tipo == 'indisciplina':
        return (
            "Notificación RH Cheong Woon\n"
            f"Hola, {nombre}. Se le notifica un reporte de indisciplina:\n"
            f"• Incidencia: Indisciplina\n"
            f"• Motivo: {motivo}\n"
            f"• Fecha: {fecha_creacion}\n"
            "⚠️ La acumulación de 2 reportes de indisciplina será motivo de suspensión."
        )
    if tipo == 'carta-suspension':
        return (
            "Notificación RH Cheong Woon\n"
            f"Hola, {nombre}. Se le notifica que firmó la carta compromiso:\n"
            "Incidencia: Carta Compromiso\n"
            f"Motivo: {motivo}\n"
            f"Fecha: {fecha_creacion}\n"
            "⚠️ Recuerde que, conforme a lo firmado, no puede volver a faltar injustificadamente."
        )
    if tipo == 'vacaciones':
        return (
            "Notificación RH Cheong Woon\n"
            f"Hola, {nombre}. Se le notifica que su solicitud de vacaciones ha sido autorizada:\n"
            f"Días solicitados: {dias}\n"
            f"Periodo: Del {f_ini} al {f_fin}\n"
            f"Fecha de regreso: {f_fin}\n"
            "✅ Disfrute su descanso. Recuerde presentarse puntual en su fecha de regreso."
        )
    if tipo == 'incapacidad':
        tipo_inc = incidencia.get('tipo_incapacidad') or motivo or 'General'
        return (
            "Notificación RH Cheong Woon\n"
            f"Hola, {nombre}. Se le notifica que su incapacidad ha sido registrada:\n"
            f"Tipo: {tipo_inc}\n"
            f"Días: {dias}\n"
            f"Periodo: Del {f_ini} al {f_fin}\n"
            f"Fecha de regreso: {f_fin}\n"
            "✅ Recuerde entregar el documento original del IMSS en Recursos Humanos al reincorporarse. Que tenga pronta recuperación."
        )
    tipo_label = _etiqueta_tipo_permiso(tipo, incidencia.get('opcion'))
    return (
        f"Permiso de {tipo_label}, válido únicamente el {fecha_creacion}, "
        "autorizado por Recursos Humanos de Cheong Woon.\n"
        "Presente su gafete físico junto con este código QR al guardia en caseta "
        "para validar y registrar su permiso. Este código es intransferible y expira "
        "al usarse o al finalizar el día."
    )


def encolar_whatsapp(cur, telefono: str, mensaje: str, imagen_png: bytes | None = None, incidencia_id: int | None = None):
    limpio = re.sub(r'\D', '', str(telefono or ''))
    if not limpio:
        return None
    if len(limpio) == 10:
        limpio = f"521{limpio}"
    asegurar_tabla_whatsapp_cola(cur)
    img_b64 = base64.b64encode(imagen_png).decode('ascii') if imagen_png else None
    cur.execute(
        """
        INSERT INTO whatsapp_cola (telefono, mensaje, imagen_base64, incidencia_id)
        VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        (limpio, mensaje, img_b64, incidencia_id),
    )
    fila = cur.fetchone()
    return fila[0] if fila else None


def crear_blueprint(get_db_connection):
    bp = Blueprint('seguridad', __name__)

    @bp.route('/api/empleados/<int:registro_id>/incidencias/<int:incidencia_id>/reenviar-qr', methods=['POST'])
    def reenviar_qr_incidencia(registro_id, incidencia_id):
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    asegurar_tabla_whatsapp_cola(cur)
                    cur.execute(
                        """
                        SELECT i.id, i.tipo, i.opcion, i.folio, i.qr_vence_at,
                               r.nombre_completo, i.no_empleado, r.tel_movil, r.fotografia_base64
                        FROM incidencias_empleado AS i
                        JOIN registro AS r ON r.id = i.registro_id
                        WHERE i.id = %s AND i.registro_id = %s
                        LIMIT 1
                        """,
                        (incidencia_id, registro_id),
                    )
                    row = cur.fetchone()
                    if not row:
                        return jsonify({'error': 'No se encontró la incidencia.'}), 404
                    tipo, opcion, folio, qr_vence_at = row[1], row[2], row[3], row[4]
                    nombre, no_emp, tel_movil, foto_b64 = row[5], row[6], row[7], row[8]
                    if not folio:
                        folio = generar_folio(cur, nombre, no_emp)
                    vence = qr_vence_at or (datetime.now(timezone.utc) + timedelta(hours=24))
                    cur.execute(
                        """
                        UPDATE incidencias_empleado
                        SET folio = %s, qr_vence_at = %s, qr_enviado = TRUE
                        WHERE id = %s
                        """,
                        (folio, vence, incidencia_id),
                    )
                    tipo_label = _etiqueta_tipo_permiso(tipo, opcion)
                    fecha_txt = _fecha_espanol(ahora_mx())
                    png = generar_imagen_qr_permiso(nombre, no_emp, tipo_label, fecha_txt, folio, foto_b64)
                    msg = formatear_mensaje_whatsapp(tipo, nombre, {'opcion': opcion})
                    cola_id = encolar_whatsapp(cur, tel_movil, msg, imagen_png=png, incidencia_id=incidencia_id)
                conn.commit()
            return jsonify({
                'ok': True,
                'folio': folio,
                'encolado': bool(cola_id),
                'sin_telefono': not bool(re.sub(r'\D', '', str(tel_movil or ''))),
                'qr_imagen_base64': base64.b64encode(png).decode('ascii'),
            }), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @bp.route('/api/seguridad/listas/<categoria>', methods=['GET'])
    def listar_seguridad(categoria):
        if categoria not in {'salidas', 'entradas', 'suspendidos', 'bajas'}:
            return jsonify({'error': 'Categoría no válida.'}), 400
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    items = []
                    if categoria in {'salidas', 'entradas'}:
                        opcion = 'salida' if categoria == 'salidas' else 'entrada'
                        cur.execute(
                            """
                            SELECT i.id, i.registro_id, r.nombre_completo, i.no_empleado,
                                   i.tipo, i.opcion, i.hora, i.motivo, i.folio,
                                   COALESCE(i.confirmado, FALSE),
                                   (r.fotografia_base64 IS NOT NULL AND r.fotografia_base64 <> '') AS tiene_foto,
                                   COALESCE(i.fecha_inicio, i.created_at::date) AS fecha
                            FROM incidencias_empleado AS i
                            JOIN registro AS r ON r.id = i.registro_id
                            WHERE i.tipo = 'entrada-salida'
                              AND i.opcion = %s
                              AND COALESCE(i.fecha_inicio, i.created_at::date) = CURRENT_DATE
                            ORDER BY i.created_at DESC, i.id DESC
                            """,
                            (opcion,),
                        )
                        for row in cur.fetchall():
                            items.append({
                                'incidencia_id': row[0],
                                'registro_id': row[1],
                                'nombre_completo': row[2],
                                'no_empleado': row[3],
                                'tipo': row[4],
                                'opcion': row[5],
                                'tipo_permiso': 'Salida' if row[5] == 'salida' else 'Entrada',
                                'hora': row[6].strftime('%H:%M') if hasattr(row[6], 'strftime') else (str(row[6])[:5] if row[6] else ''),
                                'motivo': row[7] or '',
                                'folio': row[8] or '',
                                'confirmado': bool(row[9]),
                                'tiene_foto': bool(row[10]),
                                'fecha': row[11].isoformat() if row[11] else ahora_mx().date().isoformat(),
                            })
                    elif categoria == 'suspendidos':
                        cur.execute(
                            """
                            SELECT i.id, i.registro_id, r.nombre_completo, i.no_empleado,
                                   i.tipo, i.opcion, i.hora, i.motivo, i.folio,
                                   COALESCE(i.confirmado, FALSE),
                                   (r.fotografia_base64 IS NOT NULL AND r.fotografia_base64 <> '') AS tiene_foto,
                                   i.fecha_inicio, i.fecha_reingreso
                            FROM incidencias_empleado AS i
                            JOIN registro AS r ON r.id = i.registro_id
                            WHERE i.tipo = 'suspension'
                              AND (
                                  i.created_at::date = CURRENT_DATE
                                  OR (i.fecha_inicio <= CURRENT_DATE AND i.fecha_reingreso > CURRENT_DATE)
                              )
                            ORDER BY i.created_at DESC, i.id DESC
                            """
                        )
                        for row in cur.fetchall():
                            items.append({
                                'incidencia_id': row[0],
                                'registro_id': row[1],
                                'nombre_completo': row[2],
                                'no_empleado': row[3],
                                'tipo': row[4],
                                'tipo_permiso': 'Suspensión',
                                'motivo': row[7] or '',
                                'folio': row[8] or '',
                                'confirmado': bool(row[9]),
                                'tiene_foto': bool(row[10]),
                                'fecha': row[11].isoformat() if row[11] else ahora_mx().date().isoformat(),
                                'fecha_reingreso': row[12].isoformat() if row[12] else '',
                            })
                    else:  # bajas
                        cur.execute(
                            """
                            SELECT r.id, r.nombre_completo,
                                   COALESCE(ip.no_empleado, r.numero_temporal, ''),
                                   (r.fotografia_base64 IS NOT NULL AND r.fotografia_base64 <> '') AS tiene_foto,
                                   r.fecha_inactivo
                            FROM registro AS r
                            LEFT JOIN LATERAL (
                                SELECT no_empleado FROM ingresos_puesto
                                WHERE registro_id = r.id
                                ORDER BY id DESC LIMIT 1
                            ) AS ip ON TRUE
                            WHERE COALESCE(NULLIF(UPPER(TRIM(r.estatus)), ''), 'ACTIVO') = 'INACTIVO'
                            ORDER BY r.fecha_inactivo DESC NULLS LAST, r.id DESC
                            LIMIT 40
                            """
                        )
                        for row in cur.fetchall():
                            items.append({
                                'incidencia_id': None,
                                'registro_id': row[0],
                                'nombre_completo': row[1],
                                'no_empleado': row[2] or 'S/N',
                                'tipo': 'baja',
                                'tipo_permiso': 'Baja Confirmada',
                                'motivo': 'Baja confirmada',
                                'folio': '',
                                'confirmado': True,
                                'tiene_foto': bool(row[3]),
                                'fecha': row[4].isoformat() if row[4] else ahora_mx().date().isoformat(),
                            })
            return jsonify({'categoria': categoria, 'items': items, 'fecha_texto': _fecha_espanol()}), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @bp.route('/api/seguridad/foto/<int:registro_id>', methods=['GET'])
    def foto_empleado_seguridad(registro_id):
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT fotografia_base64 FROM registro WHERE id = %s LIMIT 1", (registro_id,))
                    row = cur.fetchone()
            if not row or not row[0]:
                return jsonify({'error': 'Sin fotografía.'}), 404
            raw = str(row[0]).strip()
            if ',' in raw and raw.lower().startswith('data:'):
                raw = raw.split(',', 1)[1]
            datos = base64.b64decode(raw)
            return Response(datos, mimetype='image/jpeg')
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @bp.route('/api/seguridad/tarjeta/<int:registro_id>', methods=['GET'])
    def tarjeta_empleado_seguridad(registro_id):
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT r.nombre_completo,
                               COALESCE(ip.no_empleado, r.numero_temporal, 'S/N'),
                               r.fotografia_base64
                        FROM registro AS r
                        LEFT JOIN LATERAL (
                            SELECT no_empleado FROM ingresos_puesto
                            WHERE registro_id = r.id
                            ORDER BY id DESC LIMIT 1
                        ) AS ip ON TRUE
                        WHERE r.id = %s LIMIT 1
                        """,
                        (registro_id,),
                    )
                    row = cur.fetchone()
            if not row:
                return jsonify({'error': 'Empleado no encontrado.'}), 404
            png = generar_imagen_tarjeta_chica(row[0], row[1], row[2])
            return Response(png, mimetype='image/png')
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @bp.route('/api/seguridad/validar-qr', methods=['POST'])
    def validar_qr_seguridad():
        datos = request.get_json(silent=True) or {}
        folio = str(datos.get('folio') or '').strip().upper()
        if not folio:
            return jsonify({'valido': False, 'error': 'Folio vacío.'}), 400
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT i.id, i.registro_id, r.nombre_completo, i.no_empleado,
                               i.tipo, i.opcion, i.motivo, i.hora,
                               i.folio, i.qr_vence_at, COALESCE(i.confirmado, FALSE),
                               i.confirmado_at, i.confirmado_por,
                               (r.fotografia_base64 IS NOT NULL AND r.fotografia_base64 <> '') AS tiene_foto,
                               COALESCE(i.fecha_inicio, i.created_at::date) AS fecha
                        FROM incidencias_empleado AS i
                        JOIN registro AS r ON r.id = i.registro_id
                        WHERE UPPER(i.folio) = %s
                        LIMIT 1
                        """,
                        (folio,),
                    )
                    row = cur.fetchone()
            if not row:
                return jsonify({'valido': False, 'motivo_invalido': 'no_existe', 'error': 'QR no encontrado en el sistema.'}), 404
            vence_at = row[9]
            confirmado = bool(row[10])
            if confirmado:
                return jsonify({
                    'valido': False,
                    'motivo_invalido': 'ya_usado',
                    'error': 'Este código QR ya fue escaneado y confirmado anteriormente; ya no es válido.',
                }), 409
            if vence_at and vence_at < datetime.now(timezone.utc):
                return jsonify({
                    'valido': False,
                    'motivo_invalido': 'expirado',
                    'error': 'Este código QR superó las 24 horas de vigencia y ya expiró.',
                }), 410

            return jsonify({
                'valido': True,
                'item': {
                    'incidencia_id': row[0],
                    'registro_id': row[1],
                    'nombre_completo': row[2],
                    'no_empleado': row[3],
                    'tipo': row[4],
                    'opcion': row[5],
                    'tipo_permiso': _etiqueta_tipo_permiso(row[4], row[5]),
                    'motivo': row[6] or '',
                    'hora': row[7].strftime('%H:%M') if hasattr(row[7], 'strftime') else (str(row[7])[:5] if row[7] else ''),
                    'folio': row[8],
                    'confirmado': False,
                    'tiene_foto': bool(row[13]),
                    'fecha': row[14].isoformat() if row[14] else ahora_mx().date().isoformat(),
                    'fecha_texto': _fecha_espanol(),
                },
            }), 200
        except Exception as e:
            return jsonify({'valido': False, 'error': str(e)}), 500

    @bp.route('/api/seguridad/confirmar', methods=['POST'])
    def confirmar_permiso_seguridad():
        datos = request.get_json(silent=True) or {}
        incidencia_id = datos.get('incidencia_id')
        folio = str(datos.get('folio') or '').strip().upper()
        if not incidencia_id and not folio:
            return jsonify({'error': 'Se requiere incidencia_id o folio.'}), 400
        quien = (getattr(g, 'usuario', None) or {}).get('usuario') or 'seguridad'
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    if incidencia_id:
                        cur.execute(
                            """
                            UPDATE incidencias_empleado
                            SET confirmado = TRUE,
                                confirmado_at = now(),
                                confirmado_por = %s
                            WHERE id = %s AND COALESCE(confirmado, FALSE) = FALSE
                            RETURNING id, folio
                            """,
                            (quien, int(incidencia_id)),
                        )
                    else:
                        cur.execute(
                            """
                            UPDATE incidencias_empleado
                            SET confirmado = TRUE,
                                confirmado_at = now(),
                                confirmado_por = %s
                            WHERE UPPER(folio) = %s AND COALESCE(confirmado, FALSE) = FALSE
                            RETURNING id, folio
                            """,
                            (quien, folio),
                        )
                    row = cur.fetchone()
                conn.commit()
            if not row:
                return jsonify({'error': 'El permiso ya había sido confirmado o no existe.'}), 409
            return jsonify({'ok': True, 'incidencia_id': row[0], 'folio': row[1]}), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @bp.route('/api/exportar-permisos', methods=['GET'])
    def exportar_permisos():
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT COALESCE(i.fecha_inicio, i.created_at::date) AS fecha,
                               i.no_empleado,
                               r.nombre_completo,
                               i.tipo,
                               i.opcion,
                               i.motivo,
                               i.folio,
                               COALESCE(i.confirmado, FALSE) AS confirmado,
                               i.confirmado_por,
                               i.confirmado_at
                        FROM incidencias_empleado AS i
                        JOIN registro AS r ON r.id = i.registro_id
                        ORDER BY i.created_at DESC, i.id DESC
                        """
                    )
                    filas = cur.fetchall()

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = 'Lista de Permisos'
            encabezados = [
                'Fecha', 'N° Empleado', 'Nombre Completo',
                'Permiso', 'Motivo', 'Folio', 'Estado',
                'Confirmado Por', 'Fecha Confirmación',
            ]
            fill_header = PatternFill('solid', fgColor='0F2C59')
            font_header = Font(bold=True, color='FFFFFF')
            ws.append(encabezados)
            for col in range(1, len(encabezados) + 1):
                celda = ws.cell(row=1, column=col)
                celda.fill = fill_header
                celda.font = font_header
                celda.alignment = Alignment(horizontal='center')

            for row in filas:
                fecha_txt = row[0].strftime('%d/%m/%Y') if row[0] else ''
                permiso_txt = _etiqueta_tipo_permiso(row[3], row[4])
                estado_txt = 'Confirmada' if row[7] else 'No confirmada'
                conf_at = row[9].astimezone(ZONA_MX).strftime('%d/%m/%Y %H:%M') if row[9] else ''
                ws.append([
                    fecha_txt,
                    row[1] or '',
                    row[2] or '',
                    permiso_txt,
                    row[5] or '',
                    row[6] or '',
                    estado_txt,
                    row[8] or '',
                    conf_at,
                ])

            anchos = [14, 14, 34, 20, 32, 24, 16, 18, 20]
            for idx, ancho in enumerate(anchos, start=1):
                ws.column_dimensions[openpyxl.utils.get_column_letter(idx)].width = ancho

            salida = io.BytesIO()
            wb.save(salida)
            salida.seek(0)
            fecha_archivo = ahora_mx().strftime('%Y-%m-%d')
            return send_file(
                salida,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                as_attachment=True,
                download_name=f'lista_permisos_{fecha_archivo}.xlsx',
            )
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    return bp
