const wppconnect = require('@wppconnect-team/wppconnect');
const axios = require('axios');

wppconnect
  .create({
    session: 'kiosco-cheongwoon',
    catchQR: (base64Qr, asciiQR) => {
      console.log(asciiQR);
    },
    statusFind: (statusSession) => {
      console.log('Estado de la sesión: ', statusSession);
    },
    headless: true,
    devtools: false,
    puppeteerOptions: {
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-accelerated-2d-canvas',
        '--no-first-run',
        '--no-zygote',
        '--disable-gpu'
      ]
    },
    whatsappVersion: '2.3000.1013554286',
  })
  .then((client) => start(client))
  .catch((error) => {
    console.log('Error al iniciar WPPConnect: ', error);
  });

function start(client) {
  console.log('¡WhatsApp conectado correctamente y listo para recibir mensajes!');

  let procesandoCola = false;
  setInterval(async () => {
    if (procesandoCola) return;
    procesandoCola = true;
    try {
      const resp = await axios.get('http://localhost:5002/cola-whatsapp', { timeout: 5000 });
      const items = (resp.data && resp.data.items) || [];
      for (const item of items) {
        try {
          const destino = item.telefono.includes('@') ? item.telefono : `${item.telefono}@c.us`;
          if (item.imagen_base64) {
            await client.sendImageFromBase64(
              destino,
              `data:image/png;base64,${item.imagen_base64}`,
              'permiso_qr.png',
              item.mensaje || ''
            );
          } else {
            await client.sendText(destino, item.mensaje || '');
          }
          await axios.post(`http://localhost:5002/cola-whatsapp/${item.id}/resultado`, { ok: true });
        } catch (errEnvio) {
          console.error(`Error enviando mensaje cola #${item.id}:`, errEnvio.message);
          await axios.post(`http://localhost:5002/cola-whatsapp/${item.id}/resultado`, {
            ok: false,
            error: errEnvio.message || 'Error al enviar'
          }).catch(() => {});
        }
      }
    } catch (_) {
      // Silencioso si Flask aún está iniciando
    } finally {
      procesandoCola = false;
    }
  }, 4000);

  client.onMessage(async (message) => {
    if (message.isGroupMsg || message.fromMe) {
      return;
    }

    console.log(`Mensaje recibido de ${message.from}: ${message.body}`);

    try {
      const respuestaFlask = await axios.post('http://localhost:5002/webhook-whatsapp', {
        sender: message.from,
        text: message.body
      });

      if (respuestaFlask.data && respuestaFlask.data.message) {
        await client.sendText(message.from, respuestaFlask.data.message);
        console.log(`Respuesta enviada a ${message.from}`);
      }
    } catch (error) {
      console.error('Error al comunicarse con Flask:', error.message);
    }
  });
}