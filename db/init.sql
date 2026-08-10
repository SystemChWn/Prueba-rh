CREATE TABLE IF NOT EXISTS registro (
    id                      SERIAL PRIMARY KEY,
    fecha_registro          DATE DEFAULT CURRENT_DATE,
    nombre_completo         VARCHAR(100) NOT NULL,
    fecha_nacimiento        DATE,
    edad                    INTEGER,
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
    fotografia_base64       TEXT,
    documentos_papeleria    TEXT
);

CREATE TABLE IF NOT EXISTS encuesta_reclutamiento (
    id              SERIAL PRIMARY KEY,
    fuente          VARCHAR(50),
    fecha_registro  DATE DEFAULT CURRENT_DATE
);

CREATE TABLE IF NOT EXISTS ingresos_puesto (
    id                  SERIAL PRIMARY KEY,
    nombre_candidato    VARCHAR(100),
    empresa             VARCHAR(150),
    no_empleado         VARCHAR(50),
    personal            VARCHAR(100),
    puesto              VARCHAR(150),
    area                VARCHAR(150),
    turno               VARCHAR(50),
    salario             VARCHAR(50),
    pago                VARCHAR(50),
    registro_id         INTEGER REFERENCES registro(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS personal_reclutamiento (
    id              SERIAL PRIMARY KEY,
    nombre          VARCHAR(200) NOT NULL UNIQUE,
    fecha_registro  DATE DEFAULT CURRENT_DATE
);