# Security and privacy

- Keep both Chatlog core and Chatlog Plus bound to `127.0.0.1`.
- Do not expose ports 5030 or 5031 to the internet or an untrusted LAN.
- Never commit WeChat data keys, image keys, databases, media, transcripts, logs, or `.env` files.
- Saved keys are encrypted with Windows current-user DPAPI under the configured stack home.
  They can be decrypted by processes running as that Windows user, so protect the Windows
  account and do not share the DPAPI blobs as if they were harmless.
- The patched core receives plaintext keys through its child-process environment. It does not
  receive them as command-line arguments and the launcher clears its temporary environment
  entries after spawning the core.
- The raw `chatlog key` command and the interactive `scripts\save-wechat-keys.ps1` flow display
  discovered keys to the current terminal user. Pass `-Quiet` to the wrapper when terminal
  display or scrollback history is unwanted.
- A missing image key does not block text-chat startup. A previously protected image key is
  retained when a later Weixin process exposes only the database key.
- The transcript cache contains private message content. Store it on a trusted local disk and delete it when no longer needed.
- Chatlog Plus sends no chat data to a cloud service. Faster Whisper runs locally, although its model files may be downloaded on first use.

Run `scripts\uninstall.ps1 -WhatIf` to preview removal. Without `-KeepKeys`, uninstall deletes
the DPAPI blobs, decrypted working database, transcript cache, models, and logs under the managed
stack home.

To report a vulnerability, open a GitHub issue without including private chat data, file paths,
logs containing personal metadata, or credentials.
