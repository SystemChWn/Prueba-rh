const wppconnect = require('@wppconnect-team/wppconnect');
const axios = require('axios');

wppconnect
  .create({
    session: 'kiosco-cheongwoon',
    catchQR: (base64Qr, asciiQR) => {
      console.log(asciiQR); // Muestra el código QR en texto directamente en la terminal
    },
    statusFind: (statusSession) => {
      console.log('Estado de la sesión: ', statusSession);
    },
    headless: true,
    devtools: false,
  })
  .then((client) => start(client))
  .catch((error) => {
    console.log('Error al iniciar WPPConnect: ', error);
  });

function start(client) {
  console.log('¡WhatsApp conectado correctamente y listo para recibir mensajes!');

  client.onMessage(async (message) => {
    if (message.isGroupMsg || message.fromMe) {
      return;
    }

    console.log(`Mensaje recibido de ${message.from}: ${message.body}`);

    try {
      const respuestaFlask = await axios.post('http://localhost:5000/webhook-whatsapp', {
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