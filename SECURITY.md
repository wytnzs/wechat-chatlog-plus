# Security and privacy

- Keep both Chatlog core and Chatlog Plus bound to `127.0.0.1`.
- Do not expose ports 5030 or 5031 to the internet or an untrusted LAN.
- Never commit WeChat data keys, image keys, databases, media, transcripts, logs, or `.env` files.
- The transcript cache contains private message content. Store it on a trusted local disk and delete it when no longer needed.
- Chatlog Plus sends no chat data to a cloud service. Faster Whisper runs locally, although its model files may be downloaded on first use.

To report a vulnerability, open a GitHub issue without including private chat data or credentials.
