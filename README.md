# Prueba-rh

Sistema de RH de Cheong Woon Mexico: formulario público de registro de candidatos + portal interno de RH. Frontend estático (HTML/CSS/JS sin build) servido por nginx, backend Flask, base de datos Postgres — todo orquestado con Docker Compose.

## Estructura

```
backend/    Flask app (app.py), requirements.txt, Dockerfile
frontend/   Páginas HTML estáticas, cp.csv, nginx.conf, Dockerfile
db/         init.sql (esquema de las tablas registro y encuesta_reclutamiento)
docker-compose.yml
.env.example
```

## Despliegue con Docker (paso a paso, sin experiencia previa)

Docker empaqueta cada parte del proyecto (frontend, backend, base de datos) en "contenedores" — como cajas aisladas que ya traen todo lo necesario para correr, sin tener que instalar Python, Postgres ni nginx a mano en el servidor. `docker compose` es la herramienta que levanta y coordina esas tres cajas juntas leyendo el archivo `docker-compose.yml` de este repo.

### 1. Instalar Docker en el servidor Ubuntu

Si el servidor todavía no tiene Docker instalado (si ya corre el otro proyecto en contenedores, probablemente ya lo tiene y puedes saltar a este paso solo para confirmarlo):

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
```

Después de `usermod`, cierra la sesión SSH y vuelve a entrar (o ejecuta `newgrp docker`) para que el cambio de permisos tome efecto. Confirma que quedó instalado:

```bash
docker --version
docker compose version
```

Si `docker compose version` da error de "unknown command", prueba con el guion: `docker-compose version` (es una versión más antigua de la misma herramienta; en ese caso usa `docker-compose` en vez de `docker compose` en todos los comandos de abajo).

### 2. Obtener el código en el servidor

```bash
git clone git@github.com:SystemChWn/Prueba-rh.git
cd Prueba-rh
```

(Si ya existe una copia porque hiciste esto antes, en vez de clonar de nuevo entra a la carpeta y corre `git pull` para traer los cambios más recientes.)

### 3. Configurar las variables de entorno

El proyecto no trae contraseñas escritas en el código — las lee de un archivo `.env` que tú creas localmente y que nunca se sube a git. Cópialo desde la plantilla y edítalo:

```bash
cp .env.example .env
nano .env
```

Cambia el valor de `POSTGRES_PASSWORD` (actualmente `changeme`) por una contraseña real y segura — es la contraseña de la base de datos de este proyecto, nadie más la necesita saber. Guarda con `Ctrl+O`, `Enter`, y sal con `Ctrl+X`.

### 4. Construir y levantar los contenedores

```bash
docker compose up -d --build
```

- `--build` arma las imágenes del frontend y backend desde cero la primera vez (o cuando cambie el código).
- `-d` ("detached") los deja corriendo en segundo plano; sin esa opción verías los logs en vivo y el comando "se quedaría pegado" en la terminal.

La primera vez puede tardar uno o dos minutos mientras descarga las imágenes base (Python, nginx, Postgres) e instala las dependencias.

### 5. Confirmar que todo quedó corriendo

```bash
docker compose ps
```

Deberías ver los servicios `postgres`, `backend`, `chatbot` y `frontend` con estado `Up` (o `Up (healthy)`). Si alguno dice `Restarting` o `Exited`, algo falló; revisa sus logs con:

```bash
docker compose logs frontend   # o backend, o postgres
```

Prueba abrir el sitio desde un navegador (o con `curl` desde el mismo servidor):

```bash
curl -I http://localhost:3001/
```

Si responde `HTTP/1.1 200 OK`, el frontend está sirviendo correctamente. Desde fuera del servidor, se accede con `http://<ip-del-servidor>:3001/`.

### Comandos útiles del día a día

| Qué quieres hacer | Comando |
|---|---|
| Ver logs en vivo de un servicio | `docker compose logs -f backend` |
| Parar todo (sin borrar datos) | `docker compose stop` |
| Volver a arrancar lo que ya estaba parado | `docker compose start` |
| Apagar y quitar los contenedores (los datos de Postgres se conservan en su volumen) | `docker compose down` |
| Reconstruir después de cambiar código y volver a levantar | `docker compose up -d --build` |
| Ver qué contenedores/servicios están corriendo | `docker compose ps` |

**Importante:** nunca uses `docker compose down -v` a menos que quieras borrar por completo la base de datos de este proyecto — el flag `-v` elimina también el volumen donde vive la información de Postgres.

Puertos publicados en el host:

| Servicio  | Puerto host | Notas |
|-----------|-------------|-------|
| frontend  | 3001        | nginx sirve las páginas estáticas y reenvía `/api/` al backend |
| backend   | 8001        | API Flask (gunicorn) |
| chatbot   | 5002        | Webhook de WhatsApp y envío de avisos automáticos |
| postgres  | (interno)   | sin puerto publicado; solo el backend lo alcanza por la red interna de Docker |

Estos puertos (3001/8001) se eligieron para no chocar con el otro proyecto que ya corre en el mismo servidor (`produccion_frontend` en 3000, `produccion_backend` en 8000, `produccion_pgadmin` en 5050 y su Postgres publicado en el host 5433). Este proyecto tiene su propio contenedor y volumen de Postgres, aislado del de ese otro proyecto, y por eso puede quedar sin puerto publicado en el host — evita justamente chocar con el 5433 que ya usa el otro proyecto.

El esquema de las tablas (`registro`, `encuesta_reclutamiento`, `incidencias_chatbot`) se crea automáticamente la primera vez que el volumen de Postgres está vacío, vía `db/init.sql`. Cambios de esquema posteriores requieren un `ALTER TABLE` manual — no hay herramienta de migraciones.

### Avisos de incidencias por WhatsApp

En el archivo `.env`, configura `CHATBOT_INTERNAL_TOKEN` con un valor aleatorio y privado. Compose lo comparte entre el backend y el chatbot para proteger el endpoint interno que envía las notificaciones. También configura `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN` y `WHATSAPP_VERIFY_TOKEN` con los datos de Meta WhatsApp Cloud API.

Los avisos automatizados usan plantillas de WhatsApp aprobadas, porque algunos mensajes inician una conversación o se envían después de 24 horas. Crea y aprueba en Meta las plantillas con prefijo `rh_aviso` y el idioma `es_MX` (o configura `WHATSAPP_NOTIFICATION_TEMPLATE_PREFIX` y `WHATSAPP_TEMPLATE_LANGUAGE` con los valores que uses):

| Nombre de plantilla | Cuerpo (con variables en orden) |
|---|---|
| `rh_aviso_suspension_colaborador` | Buen día. Te notificamos que estarás en suspensión a partir del {{1}} por el motivo de: {{2}}, y regresarás a tus labores el {{3}}. Confirmo de mensaje recibido |
| `rh_aviso_suspension_emergencia` | Buen día. Intentamos comunicarnos con el empleado {{1}} para notificarle que estará ausente por suspensión a partir del {{2}} debido a: {{3}}, y regresará a sus labores el {{4}}, pero al no recibir su respuesta acudimos a ti. Te pedimos de tu apoyo para hacerle llegar este aviso. |
| `rh_aviso_indisciplina_colaborador` | Buen día. Te notificamos que se te ha generado un reporte de indisciplina por el motivo de: {{1}}. Se le recuerda que la acumulación de 2 reportes será motivo de suspensión. Confirmo de mensaje recibido |
| `rh_aviso_indisciplina_segundo_colaborador` | Buen día. Te notificamos que se te ha generado un reporte de indisciplina por el motivo de: {{1}}. Has acumulado tu segundo reporte, por lo que procede tu suspensión laboral. Confirmo de mensaje recibido |
| `rh_aviso_indisciplina_emergencia` | Buen día. Intentamos comunicarnos con el empleado {{1}} para notificarle un reporte de indisciplina generado por: {{2}}. Se le recuerda que la acumulación de 2 reportes será motivo de suspensión. Te pedimos de tu apoyo para hacerle llegar este aviso. |
| `rh_aviso_indisciplina_segundo_emergencia` | Buen día. Intentamos comunicarnos con el empleado {{1}} para notificarle un reporte de indisciplina generado por: {{2}}. El empleado ha acumulado su segundo reporte, por lo que procede la suspensión laboral. Te pedimos de tu apoyo para hacerle llegar este aviso. |
| `rh_aviso_carta_compromiso_colaborador` | Buen día. Se ha registrado tu carta compromiso en el sistema. Te recordamos que si acumulas una falta más, será motivo de baja directa de la empresa. Confirmo de mensaje recibido |
| `rh_aviso_carta_compromiso_emergencia` | Buen día. Intentamos comunicarnos con el empleado {{1}} para notificarle que si acumula una falta más será motivo de baja directa de la empresa, pero no recibimos su respuesta después de 24 horas. Agradecemos tu apoyo para pedirle que se comunique a la brevedad. Confirmo de mensaje recibido |
| `rh_aviso_baja_confirmada_colaborador` | Buen día. Te notificamos que has sido dado de baja de la empresa por el motivo de: {{1}}. Favor de presentarte en la oficina de Recursos Humanos para realizar el trámite final. Confirmo de mensaje recibido |

Los sufijos de los nombres corresponden al rol (`colaborador` o `emergencia`) y, para indisciplina, al primer o segundo reporte. Los avisos de emergencia se envían únicamente si no se recibe confirmación en 24 horas; el mensaje del contacto menciona esa falta de respuesta.

Desde **Asistencia**, Recursos Humanos puede registrar suspensiones, reportes de indisciplina, cartas compromiso y bajas confirmadas. El aviso al colaborador se envía al registrar la incidencia; el aviso al contacto de emergencia se programa para 24 horas después y se cancela si el colaborador responde `Confirmo de mensaje recibido`. Para suspensiones se requieren las fechas de inicio y reingreso. El número móvil y el contacto de emergencia se consultan desde el registro del empleado. Las indisciplinas se cuentan a partir de los registros nuevos de esta función; no existe historial anterior de incidencias en la base de datos actual.

El historial de incidencias del expediente de empleado se consulta y guarda en PostgreSQL mediante `incidencias_chatbot`; el frontend ya no usa `localStorage` para guardar incidencias. Las suspensiones e indisciplinas siguen pasando por el chatbot para conservar sus avisos y temporizador de 24 horas; vacaciones, incapacidades y entradas/salidas se insertan directamente desde el backend.

Después de configurar las variables y aprobar las plantillas, reconstruye y levanta los servicios con `docker compose up -d --build`. Si una plantilla aún no está aprobada o el número telefónico no es válido, el registro mostrará el error del envío y no confirmará que el aviso fue enviado.

## Limitaciones conocidas

- El login de `portalrh.html` es una validación fija en el cliente (`admin` / `123`), sin backend ni sesión real — cualquiera puede saltarlo navegando directo a `iniciorh.html`. No se corrigió en esta reorganización; queda pendiente.
- El endpoint `/api/incidencias` conserva el esquema de acceso actual del backend y no incorpora autenticación de servidor. Antes de publicar el puerto del backend en Internet, protégelo detrás de una VPN o de un mecanismo de autenticación del servidor.
- `main.html` y `cheong-woon.html` llaman a `/api/datos-grafica` y `/api/baja-empleado` respectivamente, pero esos endpoints todavía no están implementados en el backend.
- No hay HTTPS ni dominio configurado — nginx sirve solo HTTP en el puerto 3001. Si el servidor ya tiene un reverse proxy compartido (nginx/Traefik) para el otro proyecto, este stack puede integrarse detrás de él apuntando ese proxy a `localhost:3001`.
