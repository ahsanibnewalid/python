# Render PostgreSQL + Chat Seen Status

## 1. Create the persistent database

In Render, create a PostgreSQL database for the University Connect service.

Then open the **web service -> Environment** settings and add:

- `DATABASE_URL` = the PostgreSQL database's **Internal Database URL**
- Keep `FLASK_SECRET_KEY` fixed between deployments.
- Keep `ADMIN_PASSWORD_HASH` configured for production.
- Set `APP_ENV=production`.
- Set `COOKIE_SECURE=1` when the site is served over HTTPS.
- Set `TRUST_PROXY=1` when the Render reverse proxy is trusted for the deployment.

Do not put `DATABASE_URL` in GitHub.

## 2. Database behavior

- Local development: if `DATABASE_URL` is empty, the app uses `database.db` (SQLite).
- Render production: when `DATABASE_URL` is present, the app uses PostgreSQL.
- Existing tables are created automatically on first startup.
- The application keeps its existing SQL-based data model; PostgreSQL compatibility is handled by the database layer.

## 3. Chat status

Messages now have:

- `is_read`
- `delivered_at`
- `read_at`

The chat UI displays:

- `✓` = sent/stored
- `✓✓` = delivered to the open conversation
- `✓✓ Seen` = recipient has opened/polled the conversation and the message is read

The browser polls the conversation every two seconds. This keeps the feature compatible with ordinary Gunicorn/WSGI deployment without requiring a WebSocket server.

## 4. Important media note

PostgreSQL fixes permanent **database** storage, but uploaded profile pictures and social-feed media are still stored on the web service filesystem. Render's service filesystem should not be treated as permanent media storage. For a production social network, move those uploads to object storage (for example S3-compatible storage) in a later step.

## 5. Existing live SQLite data

Before switching a currently running SQLite deployment to PostgreSQL, export the existing SQLite database and migrate the data. Do not simply point the application at a new PostgreSQL database if the current deployment contains user data you need to preserve.


## End-to-end encrypted chat

The chat UI now uses browser Web Crypto (ECDH P-256 + HKDF + AES-256-GCM) for new messages. The server receives only ciphertext, IV and delivery/read metadata. Public keys are stored in PostgreSQL; private keys remain in the user's browser local storage. If a user clears browser storage or changes devices without a key backup mechanism, old encrypted messages cannot be decrypted on that device. Existing pre-E2EE plaintext messages are retained for backward compatibility.

The current implementation is a practical single-browser E2EE layer, not a full Signal Protocol implementation. For production-grade multi-device cryptography, add device keys, authenticated key verification/safety numbers, key rotation, forward secrecy and a secure recovery mechanism.

## Phone registration

Registration uses `intl-tel-input` and attempts country detection from the user's network/IP, then submits an E.164 number such as `+8801712345678`. A website cannot directly read a device's SIM country code, so the detected country is a network-based default and the user can change it manually.
