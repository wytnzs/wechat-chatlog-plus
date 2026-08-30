# Windows key extraction and validation

This repository reproduces the Windows WeChat key workflow from the pinned Chatlog source at
commit `eb9572ee028cbaa72a50749b9dfe6630b317228f`. The checked-in patch adds safe secret handling,
metadata output, and support for the masked database-key layout used by current Weixin 4.x.

## Process and data discovery

1. Enumerate supported WeChat desktop processes.
2. Inspect their open database files to identify the account data directory and WeChat major
   version.
3. If several processes are reported, `save-wechat-keys.ps1` tries each PID until a candidate
   validates. `-Pid` can select one explicitly.

## WeChat 4.x scan

1. Load `Weixin.dll` from the running client's version directory and locate internal 32-byte
   database masks encoded by four adjacent `mov rdx, imm64` instructions.
2. Open the selected process with `PROCESS_VM_READ | PROCESS_QUERY_INFORMATION`.
3. Walk virtual memory with `VirtualQueryEx` over the user-mode address range and keep committed,
   private, read/write regions.
4. Read large regions in bounded 4 MiB chunks and process them concurrently with between 2 and
   16 workers.
5. Search backward for the 24-byte structure marker consisting of an all-zero qword, the
   little-endian value `0x20`, and the little-endian value `0x2f`.
6. Interpret the preceding qword as a possible pointer, deduplicate it per process, and read the
   32 bytes at that address.
7. Reject low-entropy and predominantly printable candidates before expensive validation.
8. Validate each plausible candidate directly, then XOR it with each discovered internal mask
   and validate the transformed candidate. Stop as soon as the database key is confirmed.

## WeChat 3.x scan

1. Locate `WeChatWin.dll` in the selected process.
2. Scan committed writable regions within that module, skipping regions smaller than 100 KiB.
3. Find the key-length marker (`0x20`), read the preceding 32- or 64-bit pointer, and read the
   32-byte candidate.
4. Validate candidates against the local database header and stop on the first match.

## Validation and secret lifecycle

- Database candidates are cryptographically tested against the account database; random memory
  strings are not accepted merely because they are 32 bytes long.
- Image candidates are tested when present, but current Weixin 4.x does not consistently keep
  the image key in readable process memory. Its absence does not block text-chat startup, and a
  previously protected image key is retained.
- The wrapper captures the successful command output in memory, checks strict hexadecimal
  lengths, and saves each secret with Windows current-user DPAPI. Interactive runs display the
  user's keys in the current terminal unless `-Quiet` is supplied.
- Plaintext keys are not placed in command-line arguments, configuration files, Git, or logs.
- At startup the DPAPI blobs are decrypted only long enough to populate the child core process
  environment, then the parent PowerShell variables and environment entries are cleared.
- The patched core redacts keys from debug and configuration logs and does not write keys from
  the extraction command to `chatlog.json`.

The upstream source is the authoritative implementation. Rebuild it with
`scripts\build-chatlog-core.ps1`; the script pins the commit and applies
`patches\chatlog-safe-local.patch` before compiling.
