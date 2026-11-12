# GitHub Push + Railway → Telegram

Bot independiente del proyecto principal. Avisa de dos cosas distintas:

- **Código subido:** un push de GitHub a las ramas configuradas, con el autor de los commits.
- **Despliegue publicado o fallido:** un evento de Railway, incluidos los despliegues directos por CLI que no generan un push nuevo.

Un cambio en Google Cloud u otro proveedor que no genere ni un push ni un despliegue no produce estos eventos.

## Configuración

1. Crea un bot con `@BotFather` y copia su token en `TELEGRAM_BOT_TOKEN`.
2. Añade el bot a tu chat o canal y envíale un mensaje (en un canal, publica uno). Ejecuta `python get_chat_id.py` para obtener el identificador.
3. Copia `.env.example` como `.env` y rellena `TELEGRAM_CHAT_ID`, `GITHUB_REPOS` y un `GITHUB_WEBHOOK_SECRET` largo.
4. Ejecuta `start.bat` en local o despliega esta carpeta en Railway.
5. En cada repositorio de GitHub: `Settings → Webhooks → Add webhook`:
   - `Payload URL`: `https://TU-DOMINIO/github/webhook`
   - `Content type`: `application/json`
   - `Secret`: exactamente el valor de `GITHUB_WEBHOOK_SECRET`
   - `Which events`: `Just the push event`
   - marca `Active`

El servicio responde en `/health`. Solo confirma una entrega cuando Telegram acepta el mensaje. Las entregas simultáneas del mismo evento comparten el envío y las aceptadas se deduplican en memoria (hasta 1.000 eventos). Al reiniciar el bot se pierde esa memoria: no se garantiza exactamente un aviso entre reinicios.

Si Telegram no acepta el aviso, el receptor devuelve `502` y permite intentarlo otra vez. **GitHub no reenvía automáticamente las entregas fallidas:** hay que revisarlas y usar `Redeliver` o un reconciliador propio. [Documentación de GitHub](https://docs.github.com/en/webhooks/using-webhooks/handling-failed-webhook-deliveries).

## Avisos de Railway

1. Configura `RAILWAY_WEBHOOK_SECRET` con un secreto aleatorio de al menos 32 caracteres, `RAILWAY_PROJECT_IDS` con los UUID permitidos y `RAILWAY_ENVIRONMENTS=production` en el servicio del bot.
2. Despliega el receptor y comprueba `/health`.
3. En cada proyecto Railway permitido, crea un webhook dirigido a `https://TU-DOMINIO/railway/webhook`.
4. Añade la cabecera **Authorization** con valor `Bearer <RAILWAY_WEBHOOK_SECRET>`. No pongas el secreto en la URL. Railway no firma estos mensajes: el receptor exige esa cabecera y filtra proyecto y entorno.
5. Selecciona `Deployment.deployed`, `Deployment.failed`, `Deployment.crashed` y `Deployment.oom_killed`. Excluye los entornos efímeros.

Las etapas intermedias de construcción no generan avisos. Un fallo posterior al despliegue sí genera otro aviso. Railway reintenta las entregas fallidas hasta tres veces con backoff; la entrega es de mejor esfuerzo, no garantizada. [Documentación de Railway](https://docs.railway.com/observability/webhooks).

Para comprobar el endpoint sin enviar a Telegram puede utilizarse un payload autenticado `{"type":"Webhook.test"}`: devuelve `200` con `ignored=event`. La prueba con un despliegue real sí genera una notificación en el chat configurado.

## Pruebas

```sh
python -m pip install -r requirements.txt pytest pytest-asyncio
python -m pytest -q
```

Las pruebas simulan Telegram: no envían mensajes a chats reales. Cubren firma GitHub, identidad del autor, autenticación Railway, filtros, mensajes largos, reintentos y entregas concurrentes.

## Variables

- `GITHUB_REPOS`: `owner/repo,owner/otro-repo`. Vacío significa cualquier repositorio cuyo webhook llegue aquí.
- `WATCHED_BRANCHES`: ramas separadas por comas. Vacío significa todas.
- `PORT`: Railway la establece automáticamente; localmente usa `8080`.

No subas `.env` ni compartas los tokens. Si un secreto se filtra, rótalo en Telegram/GitHub y en el despliegue.
