import os
import re
import traceback
import mimetypes
import psycopg2
import openpyxl
from flask import Flask, jsonify, request, send_from_directory, send_file
from flask_cors import CORS
from io import BytesIO
import requests

app = Flask(__name__)
CORS(app)


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,DELETE,OPTIONS"
    return response

CHEONG_WOON_DOCS_DIR = r"\\192.168.1.15\RH - System\DOCUMENTOS\CHEONG WOON"
KRONOS_DOCS_DIR = r"\\192.168.1.15\RH - System\DOCUMENTOS\KRONOS"

DOC_FILENAME_KEYS = {
    'ine': 'INE',
    'comprobante_domicilio': 'COMPROBANTE_DOMICILIO',
    'acta_nacimiento': 'ACTA_NACIMIENTO',
    'curp_doc': 'CURP',
    'rfc_doc': 'RFC',
    'nss_doc': 'NSS',
    'comprobante_estudios': 'COMPROBANTE_ESTUDIOS',
    'infonavit_doc': 'INFONAVIT',
    'carta_autorizacion': 'CARTA_AUTORIZACION',
    'contrato_firmado': 'CONTRATO_FIRMADO',
    'fotografia': 'FOTOGRAFIA',
}


def normalizar_empresa(empresa):
    texto = (empresa or '').strip().upper()
    if 'KRONOS' in texto:
        return 'KRONOS', KRONOS_DOCS_DIR
    return 'CHEONG WOON', CHEONG_WOON_DOCS_DIR


def limpiar_fragmento(texto):
    valor = (texto or '').strip().upper()
    valor = re.sub(r'[^A-Z0-9]+', '_', valor)
    valor = re.sub(r'_+', '_', valor).strip('_')
    return valor or 'SIN_DATO'


def extension_desde_nombre(nombre_archivo):
    _, extension = os.path.splitext(nombre_archivo or '')
    return extension.lower() if extension else '.dat'


def construir_nombre_documento(curp, empresa, doc_key, original_filename):
    empresa_normalizada, _ = normalizar_empresa(empresa)
    curp_texto = limpiar_fragmento(curp)
    empresa_texto = limpiar_fragmento(empresa_normalizada)
    doc_texto = DOC_FILENAME_KEYS.get(doc_key, limpiar_fragmento(doc_key))
    extension = extension_desde_nombre(original_filename)
    return f"{curp_texto}-{empresa_texto}-{doc_texto}{extension}"


def obtener_directorio_empresa(empresa):
    _, base_dir = normalizar_empresa(empresa)
    return base_dir


def asegurar_directorio(directorio):
    os.makedirs(directorio, exist_ok=True)
    return directorio


def buscar_documentos(curp, empresa):
    curp_texto = limpiar_fragmento(curp)
    empresa_texto = limpiar_fragmento(normalizar_empresa(empresa)[0])
    base_dir = obtener_directorio_empresa(empresa)
    if not os.path.isdir(base_dir):
        return {}

    documentos = {}
    prefijo = f"{curp_texto}-{empresa_texto}-"

    for file_name in os.listdir(base_dir):
        if not file_name.upper().startswith(prefijo):
            continue

        nombre_base, _ = os.path.splitext(file_name)
        parte_concepto = nombre_base[len(prefijo):]
        doc_key = None
        for key, value in DOC_FILENAME_KEYS.items():
            if value == parte_concepto:
                doc_key = key
                break

        if not doc_key:
            continue

        documentos[doc_key] = {
            'fileName': file_name,
            'previewUrl': f"/api/documentos/{empresa_texto}/{file_name}",
        }

    return documentos


def obtener_ruta_documento(empresa, file_name):
    _, base_dir = normalizar_empresa(empresa)
    return os.path.join(base_dir, file_name)


def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "postgres"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        database=os.getenv("POSTGRES_DB", "Rh-System"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "S1s73m4s!"),
    )


def obtener_columnas(cur, tabla):
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = %s
        """,
        (tabla,),
    )
    return {row[0] for row in cur.fetchall()}


def obtener_empleado_dict(cur, registro_id):
    cols_registro = obtener_columnas(cur, 'registro')

    fecha_col = None
    for candidato in ('fecha_ingreso', 'fecha_registro', 'created_at', 'fecha_alta'):
        if candidato in cols_registro:
            fecha_col = candidato
            break

    fecha_select = f"r.{fecha_col} AS fecha_ingreso" if fecha_col else "NULL::timestamp AS fecha_ingreso"

    cur.execute(
        f"""
        SELECT
            r.id,
            r.nombre_completo,
            r.fecha_nacimiento,
            r.edad,
            r.genero,
            r.estado_civil,
            r.nacionalidad,
            r.curp,
            r.rfc,
            r.nss,
            r.tel_movil,
            r.correo,
            r.codigo_postal,
            r.colonia,
            r.calle,
            r.num_ext,
            r.num_int,
            r.municipio,
            r.estado,
            r.nombre_emergencia,
            r.parentesco_emergencia,
            r.telefono_emergencia,
            {fecha_select},
            ip.empresa,
            ip.no_empleado,
            ip.personal,
            ip.puesto,
            ip.area,
            ip.turno,
            ip.salario,
            ip.pago
        FROM registro r
        LEFT JOIN ingresos_puesto ip ON ip.registro_id = r.id
        WHERE r.id = %s
        LIMIT 1
        """,
        (registro_id,),
    )
    row = cur.fetchone()

    if not row:
        return None

    return {
        'registro_id': row[0],
        'nombre_completo': row[1],
        'fecha_nacimiento': row[2].strftime('%Y-%m-%d') if row[2] else None,
        'edad': row[3],
        'genero': row[4],
        'estado_civil': row[5],
        'nacionalidad': row[6],
        'curp': row[7],
        'rfc': row[8],
        'nss': row[9],
        'tel_movil': row[10],
        'correo': row[11],
        'codigo_postal': row[12],
        'colonia': row[13],
        'calle': row[14],
        'num_ext': row[15],
        'num_int': row[16],
        'municipio': row[17],
        'estado': row[18],
        'nombre_emergencia': row[19],
        'parentesco_emergencia': row[20],
        'telefono_emergencia': row[21],
        'fecha_ingreso': row[22].strftime('%Y-%m-%d') if row[22] else None,
        'empresa': row[23],
        'no_empleado': row[24],
        'personal': row[25],
        'puesto': row[26],
        'area': row[27],
        'turno': row[28],
        'salario': row[29],
        'pago': row[30],
    }


def separar_numeracion(valor):
    texto = (valor or '').strip()
    if not texto:
        return None, None

    if '/' in texto:
        parte_ext, parte_int = texto.split('/', 1)
        return parte_ext.strip() or None, parte_int.strip() or None

    return texto, None


def separar_ubicacion(valor):
    texto = (valor or '').strip()
    if not texto:
        return None, None, None

    partes = [parte.strip() for parte in texto.split('/')]
    while len(partes) < 3:
        partes.append(None)

    return (
        partes[0] or None,
        partes[1] or None,
        partes[2] or None,
    )


@app.route('/guardar-registro', methods=['POST'])
def guardar_registro():
    datos = request.form.to_dict()
    if not datos:
        return ("ERROR: No se recibieron datos", 400)

    try:
        firma_png = datos.get('firma_png') or None

        query = """
        INSERT INTO registro (
            nombre_completo, fecha_nacimiento, edad, genero, estado_civil, nacionalidad,
            curp, rfc, nss, tel_movil, correo, codigo_postal, colonia, calle,
            num_ext, num_int, municipio, estado, nombre_emergencia,
            parentesco_emergencia, telefono_emergencia, firma_archivo
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """

        values = (
            datos.get('nombre'), datos.get('fecha_nac'), datos.get('edad'), datos.get('genero'),
            datos.get('edo_civil'), datos.get('nacionalidad'), datos.get('curp'), datos.get('rfc'),
            datos.get('nss'), datos.get('tel_movil'), datos.get('correo'), datos.get('cp'),
            datos.get('colonia'), datos.get('calle'), datos.get('num_ext'), datos.get('num_int'),
            datos.get('municipio'), datos.get('estado'), datos.get('nom_eme1'), datos.get('par_eme1'),
            datos.get('tel_eme1'), firma_png,
        )

        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, values)
            conn.commit()

        return ("OK", 200)
    except Exception as e:
        print(f"Error: {e}")
        return (f"ERROR: {str(e)}", 500)


@app.route('/guardar-encuesta', methods=['POST'])
def guardar_encuesta():
    datos = request.form.to_dict()
    fuente = (datos.get('fuente') or '').strip()
    sub_fuente = (datos.get('sub_fuente') or '').strip()

    # Normalizar valores para que coincidan con las etiquetas usadas en el frontend
    def normalizar_fuente(fuente_raw, sub_raw):
        f = (fuente_raw or '').strip().upper()
        s = (sub_raw or '').strip().upper()

        if f == 'FACEBOOK':
            return 'FACEBOOK'
        if f == 'POSTEO':
            return 'POSTEO'
        if f == 'VOLANTE':
            return 'VOLANTE'
        if f.startswith('RECOM'):
            return 'RECOMENDACION'
        if f == 'AGENCIA':
            if s == 'CW':
                return 'RECLU CW'
            if s == 'PURO':
                return 'RECLU PURO'
            return s or 'AGENCIA'

        return f or s or 'OTRO'

    fuente_norm = normalizar_fuente(fuente, sub_fuente)

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO encuesta_reclutamiento (fuente, fecha_registro) VALUES (%s, now())",
                    (fuente_norm,)
                )
            conn.commit()
        return ("OK", 200)
    except Exception as e:
        print(f"ERROR: {e}")
        return (f"ERROR: {str(e)}", 500)


@app.route('/obtener-pendientes', methods=['GET'])
def obtener_pendientes():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT r.nombre_completo, r.id
                    FROM registro r
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM ingresos_puesto ip
                        WHERE ip.registro_id = r.id
                    )
                    ORDER BY r.id DESC
                    """
                )
                rows = cur.fetchall()

        data = [
            {
                'nombre_completo': row[0],
                'id': row[1],
            }
            for row in rows
        ]
        return jsonify(data), 200
    except Exception as e:
        print(f"Error: {e}")
        return (f"ERROR: {str(e)}", 500)


@app.route('/obtener-empleados/<empresa>', methods=['GET'])
@app.route('/obtener-ingresos', methods=['GET'])
def obtener_ingresos(empresa=None):
    if empresa is None:
        empresa = request.args.get('empresa')
    empresa = (empresa or '').strip().upper()
    if not empresa:
        return ("ERROR: El parámetro empresa es obligatorio", 400)

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'registro'
                    """
                )
                cols_registro = {row[0] for row in cur.fetchall()}

                fecha_col = None
                for candidato in ('fecha_ingreso', 'fecha_registro', 'created_at', 'fecha_alta'):
                    if candidato in cols_registro:
                        fecha_col = candidato
                        break

                fecha_select = f"r.{fecha_col}" if fecha_col else "NULL::timestamp"

                cur.execute(
                    f"""
                    SELECT ingresos_puesto.registro_id,
                           ingresos_puesto.nombre_candidato,
                           ingresos_puesto.no_empleado,
                           ingresos_puesto.empresa
                         , {fecha_select} AS fecha_ingreso
                    FROM ingresos_puesto
                    LEFT JOIN registro r ON r.id = ingresos_puesto.registro_id
                    WHERE UPPER(REPLACE(ingresos_puesto.empresa, ' ', '_')) = %s
                    ORDER BY ingresos_puesto.registro_id DESC
                    """,
                    (empresa,),
                )
                rows = cur.fetchall()

        data = [
            {
                'registro_id': row[0],
                'nombre': row[1],
                'no_empleado': row[2],
                'empresa': row[3],
                'fecha_ingreso': row[4].strftime('%Y-%m-%d') if row[4] else None,
                'estatus': 'ACTIVO',
            }
            for row in rows
        ]
        return jsonify(data), 200
    except Exception as e:
        print(f"Error detallado: {e}")
        return (f"ERROR: {str(e)}", 500)


@app.route('/obtener-empleado/<int:registro_id>', methods=['GET'])
def obtener_empleado(registro_id):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                data = obtener_empleado_dict(cur, registro_id)

        if not data:
            return ("ERROR: Empleado no encontrado", 404)
        return jsonify(data), 200
    except Exception as e:
        print(f"Error detallado: {e}")
        return (f"ERROR: {str(e)}", 500)


@app.route('/actualizar-empleado/<int:registro_id>', methods=['POST'])
def actualizar_empleado(registro_id):
    payload = request.get_json(silent=True) or {}

    if not isinstance(payload, dict) or not payload:
        return ("ERROR: No se recibieron datos para actualizar", 400)

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cols_registro = obtener_columnas(cur, 'registro')
                cols_ingresos = obtener_columnas(cur, 'ingresos_puesto')

                nombre_ext, nombre_int = separar_numeracion(payload.get('numeracion'))
                municipio, estado, codigo_postal = separar_ubicacion(payload.get('ubicacion'))

                fecha_ingreso_valor = payload.get('fecha_ingreso') or payload.get('fecha_ingreso_raw') or payload.get('fecha_registro')
                fecha_col = None
                for candidato in ('fecha_ingreso', 'fecha_registro', 'created_at', 'fecha_alta'):
                    if candidato in cols_registro:
                        fecha_col = candidato
                        break

                registro_map = {
                    'nombre_completo': payload.get('nombre_completo'),
                    'fecha_nacimiento': payload.get('fecha_nacimiento'),
                    'edad': payload.get('edad'),
                    'genero': payload.get('genero'),
                    'estado_civil': payload.get('estado_civil'),
                    'curp': payload.get('curp'),
                    'rfc': payload.get('rfc'),
                    'nss': payload.get('nss'),
                    'tel_movil': payload.get('tel_movil'),
                    'correo': payload.get('correo'),
                    'calle': payload.get('calle'),
                    'colonia': payload.get('colonia'),
                    'num_ext': nombre_ext,
                    'num_int': nombre_int,
                    'municipio': municipio,
                    'estado': estado,
                    'codigo_postal': codigo_postal,
                    'nombre_emergencia': payload.get('emergencia_nombre'),
                    'parentesco_emergencia': payload.get('emergencia_parentesco'),
                    'telefono_emergencia': payload.get('emergencia_telefono'),
                }

                if fecha_col:
                    registro_map[fecha_col] = fecha_ingreso_valor

                registro_updates = []
                registro_values = []
                for campo, valor in registro_map.items():
                    if campo in cols_registro and valor not in (None, ''):
                        registro_updates.append(f"{campo} = %s")
                        registro_values.append(valor)

                if registro_updates:
                    registro_values.append(registro_id)
                    cur.execute(
                        f"UPDATE registro SET {', '.join(registro_updates)} WHERE id = %s",
                        registro_values,
                    )

                cur.execute(
                    "SELECT 1 FROM ingresos_puesto WHERE registro_id = %s LIMIT 1",
                    (registro_id,),
                )
                existe_ingreso = cur.fetchone() is not None

                ingreso_map = {
                    'empresa': payload.get('empresa'),
                    'no_empleado': payload.get('no_empleado'),
                    'personal': payload.get('personal'),
                    'puesto': payload.get('puesto'),
                    'area': payload.get('area'),
                    'turno': payload.get('turno'),
                    'salario': payload.get('salario'),
                    'pago': payload.get('pago'),
                }

                ingreso_updates = []
                ingreso_values = []
                for campo, valor in ingreso_map.items():
                    if campo in cols_ingresos and valor not in (None, ''):
                        ingreso_updates.append(f"{campo} = %s")
                        ingreso_values.append(valor)

                if existe_ingreso:
                    if ingreso_updates:
                        ingreso_values.append(registro_id)
                        cur.execute(
                            f"UPDATE ingresos_puesto SET {', '.join(ingreso_updates)} WHERE registro_id = %s",
                            ingreso_values,
                        )
                elif ingreso_updates:
                    columnas = []
                    valores = []
                    for campo, valor in ingreso_map.items():
                        if campo in cols_ingresos and valor not in (None, ''):
                            columnas.append(campo)
                            valores.append(valor)

                    if 'registro_id' in cols_ingresos:
                        columnas.append('registro_id')
                        valores.append(registro_id)

                    if columnas:
                        placeholders = ', '.join(['%s'] * len(valores))
                        cur.execute(
                            f"INSERT INTO ingresos_puesto ({', '.join(columnas)}) VALUES ({placeholders})",
                            valores,
                        )

                conn.commit()

                data = obtener_empleado_dict(cur, registro_id)

        if not data:
            return ("ERROR: Empleado no encontrado después de actualizar", 404)

        return jsonify(data), 200
    except Exception as e:
        print(f"Error detallado: {e}")
        if hasattr(e, 'pgerror') and e.pgerror:
            print(f"PostgreSQL error: {e.pgerror}")
        if hasattr(e, 'diag') and e.diag:
            if getattr(e.diag, 'message_detail', None):
                print(f"Detalle: {e.diag.message_detail}")
            if getattr(e.diag, 'constraint_name', None):
                print(f"Constraint: {e.diag.constraint_name}")
            if getattr(e.diag, 'column_name', None):
                print(f"Columna: {e.diag.column_name}")
        print(f"Payload recibido en /actualizar-empleado: {payload}")
        return (f"ERROR: {str(e)}", 500)


@app.route('/api/employee-files', methods=['GET'])
def listar_archivos_empleado():
    curp = request.args.get('curp', '').strip()
    empresa = request.args.get('empresa', '').strip()

    if not curp or not empresa:
        return ("ERROR: curp y empresa son obligatorios", 400)

    documentos = buscar_documentos(curp, empresa)
    fotografia = documentos.get('fotografia')
    if fotografia:
        documentos = {k: v for k, v in documentos.items() if k != 'fotografia'}

    return jsonify({
        'documentos': documentos,
        'fotografia': fotografia,
    }), 200


@app.route('/api/employee-files', methods=['POST'])
def subir_archivo_empleado():
    curp = (request.form.get('curp') or '').strip()
    empresa = (request.form.get('empresa') or '').strip()
    doc_key = (request.form.get('doc_key') or '').strip()
    archivo = request.files.get('file')

    if not curp or not empresa or not doc_key or archivo is None:
        return ("ERROR: curp, empresa, doc_key y file son obligatorios", 400)

    if doc_key not in DOC_FILENAME_KEYS:
        return ("ERROR: doc_key inválido", 400)

    empresa_normalizada, directorio = normalizar_empresa(empresa)
    asegurar_directorio(directorio)

    file_name = construir_nombre_documento(curp, empresa_normalizada, doc_key, archivo.filename)
    file_path = os.path.join(directorio, file_name)
    archivo.save(file_path)

    return jsonify({
        'ok': True,
        'docKey': doc_key,
        'fileName': archivo.filename,
        'previewUrl': f"/api/documentos/{limpiar_fragmento(empresa_normalizada)}/{file_name}",
    }), 200


@app.route('/api/employee-files', methods=['DELETE'])
def eliminar_archivo_empleado():
    curp = (request.args.get('curp') or '').strip()
    empresa = (request.args.get('empresa') or '').strip()
    doc_key = (request.args.get('doc_key') or '').strip()

    if not curp or not empresa or not doc_key:
        return ("ERROR: curp, empresa y doc_key son obligatorios", 400)

    if doc_key not in DOC_FILENAME_KEYS:
        return ("ERROR: doc_key inválido", 400)

    empresa_normalizada, directorio = normalizar_empresa(empresa)
    file_name = construir_nombre_documento(curp, empresa_normalizada, doc_key, 'archivo.dat')
    ruta = os.path.join(directorio, file_name)

    if os.path.exists(ruta):
        os.remove(ruta)
        return ("OK", 200)

    prefijo = f"{limpiar_fragmento(curp)}-{limpiar_fragmento(empresa_normalizada)}-{DOC_FILENAME_KEYS[doc_key]}"
    for existing_file in os.listdir(directorio) if os.path.isdir(directorio) else []:
        if existing_file.upper().startswith(prefijo):
            os.remove(os.path.join(directorio, existing_file))
            return ("OK", 200)

    return ("ERROR: Archivo no encontrado", 404)


@app.route('/api/documentos/<empresa>/<path:file_name>', methods=['GET'])
def servir_documento_empleado(empresa, file_name):
    empresa_normalizada, directorio = normalizar_empresa(empresa)
    if not os.path.isdir(directorio):
        return ("ERROR: Directorio no disponible", 404)

    mimetype, _ = mimetypes.guess_type(file_name)
    return send_from_directory(directorio, file_name, mimetype=mimetype or 'application/octet-stream')


def guardar_o_actualizar_ingreso(cur, payload):
    required_keys = [
        'nombre_candidato', 'empresa', 'no_empleado', 'personal', 'puesto', 'area',
        'turno', 'salario', 'pago', 'registro_id'
    ]

    faltantes = [k for k in required_keys if not str(payload.get(k, '')).strip()]
    if faltantes:
        return (f"ERROR: Fila incompleta ({', '.join(faltantes)})", 400)

    try:
        registro_id = int(str(payload.get('registro_id')).strip())
    except Exception:
        return ("ERROR: registro_id inválido", 400)

    valores = (
        str(payload.get('nombre_candidato')).strip(),
        str(payload.get('empresa')).strip(),
        str(payload.get('no_empleado')).strip(),
        str(payload.get('personal')).strip(),
        str(payload.get('puesto')).strip(),
        str(payload.get('area')).strip(),
        str(payload.get('turno')).strip(),
        str(payload.get('salario')).strip(),
        str(payload.get('pago')).strip(),
        registro_id,
    )

    cur.execute(
        """
        SELECT 1
        FROM ingresos_puesto
        WHERE registro_id = %s
        LIMIT 1
        """,
        (registro_id,),
    )

    existe = cur.fetchone() is not None

    if existe:
        cur.execute(
            """
            UPDATE ingresos_puesto
            SET nombre_candidato = %s,
                empresa = %s,
                no_empleado = %s,
                personal = %s,
                puesto = %s,
                area = %s,
                turno = %s,
                salario = %s,
                pago = %s
            WHERE registro_id = %s
            """,
            valores,
        )
    else:
        cur.execute(
            """
            INSERT INTO ingresos_puesto (
                nombre_candidato, empresa, no_empleado, personal, puesto, area,
                turno, salario, pago, registro_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            valores,
        )

    return ("OK", 200)


@app.route('/guardar-ingreso', methods=['POST'])
def guardar_ingreso():
    payload = request.get_json(silent=True) or {}

    if not isinstance(payload, dict) or not payload:
        return ("ERROR: No se recibieron datos para guardar", 400)

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                resultado = guardar_o_actualizar_ingreso(cur, payload)
            conn.commit()

        return resultado
    except Exception as e:
        print(f"Error detallado: {e}")
        if hasattr(e, 'pgerror') and e.pgerror:
            print(f"PostgreSQL error: {e.pgerror}")
        if hasattr(e, 'diag') and e.diag:
            if getattr(e.diag, 'message_detail', None):
                print(f"Detalle: {e.diag.message_detail}")
            if getattr(e.diag, 'constraint_name', None):
                print(f"Constraint: {e.diag.constraint_name}")
            if getattr(e.diag, 'column_name', None):
                print(f"Columna: {e.diag.column_name}")
        print(f"Payload recibido en /guardar-ingreso: {payload}")
        return (f"ERROR: {str(e)}", 500)


@app.route('/guardar-ingresos', methods=['POST'])
def guardar_ingresos():
    payload = request.get_json(silent=True) or []

    if not isinstance(payload, list) or not payload:
        return ("ERROR: No se recibieron filas para guardar", 400)

    required_keys = [
        'nombre_candidato', 'empresa', 'no_empleado', 'personal', 'puesto',
        'area', 'turno', 'salario', 'pago', 'registro_id'
    ]

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                for idx, fila in enumerate(payload, start=1):
                    if not isinstance(fila, dict):
                        return (f"ERROR: Fila {idx} inválida", 400)

                    resultado = guardar_o_actualizar_ingreso(cur, fila)
                    if resultado[1] != 200:
                        return (f"ERROR: Fila {idx}: {resultado[0]}", resultado[1])

            conn.commit()

        return ("OK", 200)
    except Exception as e:
        print(f"Error detallado: {e}")
        if hasattr(e, 'pgerror') and e.pgerror:
            print(f"PostgreSQL error: {e.pgerror}")
        if hasattr(e, 'diag') and e.diag:
            if getattr(e.diag, 'message_detail', None):
                print(f"Detalle: {e.diag.message_detail}")
            if getattr(e.diag, 'constraint_name', None):
                print(f"Constraint: {e.diag.constraint_name}")
            if getattr(e.diag, 'column_name', None):
                print(f"Columna: {e.diag.column_name}")
        print(f"Payload recibido en /guardar-ingresos: {payload}")
        return (f"ERROR: {str(e)}", 500)

#----------------------------------------------------------------------------------------------------
# GAFETE

def generar_gafete_excel(datos):
    datos = datos or {}

    try:
        url_plantilla_nueva = "https://drive.google.com/uc?export=download&id=1rdKx-wvjIUXuuMoGQdNH_gwIKhwvZ61B"
        print(f"Descargando plantilla desde Drive: {url_plantilla_nueva}")

        response = requests.get(url_plantilla_nueva, timeout=60)
        response.raise_for_status()

        if not response.content:
            raise FileNotFoundError("No se pudo descargar el archivo desde Drive")

        wb = openpyxl.load_workbook(BytesIO(response.content), data_only=False)
        ws = wb.active

        nombre_completo = str(datos.get('nombre_completo', '')).strip()
        palabras = nombre_completo.split()

        if len(palabras) >= 2:
            apellido = ' '.join(palabras[-2:])
            nombre = ' '.join(palabras[:-2])
        elif len(palabras) == 1:
            nombre = palabras[0]
            apellido = ''
        else:
            nombre = ''
            apellido = ''

        asignaciones = {
            'C14': nombre,
            'C16': apellido,
            'D18': datos.get('nss', ''),
            'D21': datos.get('curp', ''),
            'G5': datos.get('area', ''),
            'G9': datos.get('nom_emergencias', ''),
            'G11': datos.get('cont_emergencias', ''),
            'G14': datos.get('fecha_ingreso', ''),
        }

        for celda, valor in asignaciones.items():
            ws[celda] = '' if valor is None else str(valor)

        excel_buffer_out = BytesIO()
        wb.save(excel_buffer_out)
        excel_buffer_out.seek(0)

        return excel_buffer_out, "Formato_Gafete.xlsx"

    except Exception as e:
        print(f"Error al generar la credencial: {e}")
        traceback.print_exc()
        return None, None


@app.route('/descargar-credencial', methods=['POST'])
def descargar_credencial():
    datos = request.get_json(silent=True) or {}

    buffer, nombre_archivo = generar_gafete_excel(datos)
    if not buffer:
        return jsonify({"error": "No se pudo generar el archivo de credencial"}), 500

    return send_file(
        buffer,
        as_attachment=True,
        download_name=nombre_archivo,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


@app.route('/generar-credencial', methods=['POST'])
def generar_credencial():
    return descargar_credencial()


@app.route('/admin/normalize-encuestas', methods=['POST'])
def admin_normalize_encuestas():
    """Endpoint temporal: normaliza los valores de `fuente` en encuesta_reclutamiento.
    Devuelve los recuentos antes y después. Usar con precaución (solo local).
    """
    case_sql = '''
UPDATE encuesta_reclutamiento
SET fuente = CASE
  WHEN UPPER(COALESCE(fuente, '')) LIKE '%FACEBOOK%' THEN 'FACEBOOK'
  WHEN UPPER(COALESCE(fuente, '')) LIKE '%POSTEO%' THEN 'POSTEO'
  WHEN UPPER(COALESCE(fuente, '')) LIKE '%VOLANTE%' THEN 'VOLANTE'
  WHEN UPPER(COALESCE(fuente, '')) LIKE '%RECOM%' THEN 'RECOMENDACION'
  WHEN UPPER(COALESCE(fuente, '')) LIKE '%AGENCIA%' AND (UPPER(COALESCE(fuente, '')) LIKE '%CW%' OR UPPER(COALESCE(fuente, '')) LIKE '% C W%') THEN 'RECLU CW'
  WHEN UPPER(COALESCE(fuente, '')) LIKE '%AGENCIA%' AND UPPER(COALESCE(fuente, '')) LIKE '%PURO%' THEN 'RECLU PURO'
  WHEN UPPER(COALESCE(fuente, '')) LIKE '%CW%' THEN 'RECLU CW'
  WHEN UPPER(COALESCE(fuente, '')) LIKE '%PURO%' THEN 'RECLU PURO'
  ELSE UPPER(TRIM(COALESCE(fuente, 'OTRO')))
END
WHERE fuente IS NOT NULL;
'''

    summary_sql = "SELECT fuente, COUNT(*) FROM encuesta_reclutamiento GROUP BY fuente ORDER BY COUNT(*) DESC;"

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(summary_sql)
                before = cur.fetchall()

                cur.execute(case_sql)
                conn.commit()

                cur.execute(summary_sql)
                after = cur.fetchall()

        return jsonify({'before': before, 'after': after}), 200
    except Exception as e:
        traceback.print_exc()
        return (f"ERROR: {str(e)}", 500)


@app.route('/admin/preview-encuestas', methods=['GET'])
def admin_preview_encuestas():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM encuesta_reclutamiento LIMIT 20")
                filas = cur.fetchall()
                cols = [d[0] for d in cur.description]
        # convert to list of dicts
        data = [dict(zip(cols, f)) for f in filas]
        return jsonify(data), 200
    except Exception as e:
        traceback.print_exc()
        return (f"ERROR: {str(e)}", 500)

#-----------------------------------------------------------------------------------------------------------------
@app.route('/api/datos-grafica')
def api_datos_grafica():
    periodo = request.args.get('periodo')
    tz = request.args.get('tz') or 'America/Mexico_City'

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                if periodo == 'mes':
                    mes = int(request.args.get('mes', '1'))

                    # Prefer explicit sub_fuente when present; detect column existence first
                    cur.execute("""
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'encuesta_reclutamiento' AND column_name = 'sub_fuente'
                    """)
                    has_sub = cur.fetchone() is not None

                    if has_sub:
                        query = """
                            SELECT
                              CASE
                                WHEN UPPER(COALESCE(sub_fuente, '')) LIKE '%%CW%%' THEN 'RECLU CW'
                                WHEN UPPER(COALESCE(sub_fuente, '')) LIKE '%%PURO%%' THEN 'RECLU PURO'
                                WHEN UPPER(TRIM(COALESCE(fuente, ''))) IN ('RECLU CW','RECLU PURO') THEN UPPER(TRIM(COALESCE(fuente, '')))
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%FACEBOOK%%' THEN 'FACEBOOK'
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%POSTEO%%' THEN 'POSTEO'
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%VOLANTE%%' THEN 'VOLANTE'
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%RECOM%%' THEN 'RECOMENDACION'
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%AGENCIA%%' AND (UPPER(COALESCE(fuente, '')) LIKE '%%CW%%' OR UPPER(COALESCE(fuente, '')) LIKE '%% C W%%') THEN 'RECLU CW'
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%AGENCIA%%' AND UPPER(COALESCE(fuente, '')) LIKE '%%PURO%%' THEN 'RECLU PURO'
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%CW%%' THEN 'RECLU CW'
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%PURO%%' THEN 'RECLU PURO'
                                ELSE UPPER(TRIM(COALESCE(fuente, 'OTRO')))
                              END as fuente_norm,
                              COUNT(*)
                            FROM encuesta_reclutamiento
                            WHERE EXTRACT(MONTH FROM timezone(%s, fecha_registro)) = %s
                              AND EXTRACT(YEAR FROM timezone(%s, fecha_registro)) = EXTRACT(YEAR FROM timezone(%s, now()))
                            GROUP BY fuente_norm
                        """
                    else:
                        query = """
                            SELECT
                              CASE
                                WHEN UPPER(TRIM(COALESCE(fuente, ''))) IN ('RECLU CW','RECLU PURO') THEN UPPER(TRIM(COALESCE(fuente, '')))
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%FACEBOOK%%' THEN 'FACEBOOK'
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%POSTEO%%' THEN 'POSTEO'
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%VOLANTE%%' THEN 'VOLANTE'
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%RECOM%%' THEN 'RECOMENDACION'
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%AGENCIA%%' AND (UPPER(COALESCE(fuente, '')) LIKE '%%CW%%' OR UPPER(COALESCE(fuente, '')) LIKE '%% C W%%') THEN 'RECLU CW'
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%AGENCIA%%' AND UPPER(COALESCE(fuente, '')) LIKE '%%PURO%%' THEN 'RECLU PURO'
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%CW%%' THEN 'RECLU CW'
                                WHEN UPPER(COALESCE(fuente, '')) LIKE '%%PURO%%' THEN 'RECLU PURO'
                                ELSE UPPER(TRIM(COALESCE(fuente, 'OTRO')))
                              END as fuente_norm,
                              COUNT(*)
                            FROM encuesta_reclutamiento
                            WHERE EXTRACT(MONTH FROM timezone(%s, fecha_registro)) = %s
                              AND EXTRACT(YEAR FROM timezone(%s, fecha_registro)) = EXTRACT(YEAR FROM timezone(%s, now()))
                            GROUP BY fuente_norm
                        """
                    cur.execute(query, (tz, mes, tz, tz))
                    filas_raw = cur.fetchall()
                    resultados = {}
                    for row in filas_raw:
                        if isinstance(row, (list, tuple)) and len(row) >= 2:
                            try:
                                resultados[str(row[0])] = int(row[1])
                            except Exception:
                                resultados[str(row[0])] = 0

                    labels_orden = ['FACEBOOK', 'POSTEO', 'VOLANTE', 'RECOMENDACION', 'RECLU CW', 'RECLU PURO']
                    datos_ordenados = [int(resultados.get(label, 0)) for label in labels_orden]

                    return jsonify(datos_ordenados)

                elif periodo == '12meses':
                    query = """
                        SELECT EXTRACT(MONTH FROM timezone(%s, fecha_registro)) as mes, COUNT(*)
                        FROM encuesta_reclutamiento
                        WHERE EXTRACT(YEAR FROM timezone(%s, fecha_registro)) = EXTRACT(YEAR FROM timezone(%s, now()))
                        GROUP BY mes
                    """
                    cur.execute(query, (tz, tz, tz))
                    filas = cur.fetchall()

                    meses_counts = [0] * 12
                    for mes_val, total in filas:
                        m = int(mes_val)
                        if 1 <= m <= 12:
                            meses_counts[m - 1] = int(total)

                    return jsonify(meses_counts)

    except Exception as e:
        print(f"Error en API gráfica: {e}")
        traceback.print_exc()
        return jsonify([0] * 12), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=True)
