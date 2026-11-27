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

async function resolverDestinoWhatsApp(client, telefono) {
  const limpio = String(telefono || '').replace(/\D/g, '');
  if (!limpio) return null;
  const candidatos = [];
  if (limpio.length === 10) {
    candidatos.push(`521${limpio}@c.us`, `52${limpio}@c.us`);
  } else if (limpio.startsWith('521') && limpio.length === 13) {
    candidatos.push(`${limpio}@c.us`, `52${limpio.slice(3)}@c.us`);
  } else if (limpio.startsWith('52') && limpio.length === 12) {
    candidatos.push(`521${limpio.slice(2)}@c.us`, `${limpio}@c.us`);
  } else {
    candidatos.push(`${limpio}@c.us`);
  }
  for (const cand of candidatos) {
    try {
      const st = await client.checkNumberStatus(cand);
      if (st && st.numberExists && st.id && st.id._serialized) {
        return st.id._serialized;
      }
    } catch (_) {
      // probar siguiente formato
    }
  }
  return candidatos[0];
}

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
          const destino = await resolverDestinoWhatsApp(client, item.telefono);
          if (!destino) throw new Error('Teléfono vacío');
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
          console.log(`Mensaje automático enviado a ${destino} (cola #${item.id})`);
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