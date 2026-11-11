# GitHub Push → Telegram

Bot independiente del proyecto principal. Recibe webhooks de GitHub y envía un aviso a Telegram por cada push a las ramas configuradas.

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

El servicio responde en `/health`. GitHub reintentará automáticamente si Telegram falla; las entregas repetidas ya aceptadas se deduplican.

## Variables

- `GITHUB_REPOS`: `owner/repo,owner/otro-repo`. Vacío significa cualquier repositorio cuyo webhook llegue aquí.
- `WATCHED_BRANCHES`: ramas separadas por comas. Vacío significa todas.
- `PORT`: Railway la establece automáticamente; localmente usa `8080`.

No subas `.env` ni compartas los tokens. Si un secreto se filtra, rótalo en Telegram/GitHub y en el despliegue.
