CREATE TABLE IF NOT EXISTS registro (
    id                      SERIAL PRIMARY KEY,
    nombre_completo         VARCHAR(255) NOT NULL,
    fecha_nacimiento        DATE,
    edad                    SMALLINT,
    genero                  VARCHAR(30),
    estado_civil            VARCHAR(30),
    nacionalidad            VARCHAR(60),
    curp                    VARCHAR(18),
    rfc                     VARCHAR(13),
    nss                     VARCHAR(20),
    tel_movil               VARCHAR(20),
    correo                  VARCHAR(255),
    codigo_postal           VARCHAR(10),
    colonia                 VARCHAR(150),
    calle                   VARCHAR(150),
    num_ext                 VARCHAR(20),
    num_int                 VARCHAR(20),
    municipio               VARCHAR(150),
    estado                  VARCHAR(100),
    nombre_emergencia       VARCHAR(255),
    parentesco_emergencia   VARCHAR(100),
    telefono_emergencia     VARCHAR(20),
    firma_archivo           TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS encuesta_reclutamiento (
    id                      SERIAL PRIMARY KEY,
    curp                    VARCHAR(18),
    fuente                  VARCHAR(100) NOT NULL,
    sub_fuente              VARCHAR(100),
    nombre_reclutador       VARCHAR(255),
    nombre_empleado         VARCHAR(255),
    detalle                 TEXT,
    fecha_registro          TIMESTAMPTZ NOT NULL DEFAULT now()
);
