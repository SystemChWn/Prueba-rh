import os

import psycopg2
from flask import Flask, request

app = Flask(__name__)


def get_db_connection():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        database=os.environ.get("POSTGRES_DB", "rh_system"),
        user=os.environ.get("POSTGRES_USER", "postgres"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


@app.route('/api/health', methods=['GET'])
def health():
    return ("ok", 200)


@app.route('/api/guardar-registro', methods=['POST'])
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


@app.route('/api/guardar-encuesta', methods=['POST'])
def guardar_encuesta():
    datos = request.form.to_dict()
    if not datos:
        return ("ERROR: No se recibieron datos de encuesta", 400)

    fuente = (datos.get('fuente') or '').strip()
    if not fuente:
        return ("ERROR: La fuente es obligatoria", 400)

    curp = (datos.get('curp') or '').strip()
    sub_fuente = (datos.get('sub_fuente') or '').strip()
    nombre_reclutador = (datos.get('nombre_reclutador') or '').strip()
    nombre_empleado = (datos.get('nombre_empleado') or '').strip()
    detalle = (datos.get('detalle') or '').strip()

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO encuesta_reclutamiento (
                        curp, fuente, sub_fuente, nombre_reclutador, nombre_empleado, detalle, fecha_registro
                    ) VALUES (%s, %s, %s, %s, %s, %s, now())
                    """,
                    (curp, fuente, sub_fuente, nombre_reclutador, nombre_empleado, detalle)
                )
            conn.commit()

        return ("OK", 200)
    except Exception as e:
        print(f"Error: {e}")
        return (f"ERROR: {str(e)}", 500)


if __name__ == '__main__':
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host='0.0.0.0', port=5000, debug=debug)
