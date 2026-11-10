from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import logging
import os
import sqlite3
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, urlopen

import moments


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


APP_NAME = "Chatlog Plus"
APP_VERSION = "0.2.0"
CORE_BASE = os.environ.get("CHATLOG_CORE_URL", "http://127.0.0.1:5030").rstrip("/")
PLUS_HOST = os.environ.get("CHATLOG_PLUS_HOST", "127.0.0.1")
PLUS_PORT = int(os.environ.get("CHATLOG_PLUS_PORT", "5031"))
DEFAULT_MODEL = os.environ.get("CHATLOG_PLUS_MODEL", "turbo")
DEFAULT_LANGUAGE = os.environ.get("CHATLOG_PLUS_LANGUAGE", "zh")
DEFAULT_PROMPT = os.environ.get(
    "CHATLOG_PLUS_INITIAL_PROMPT",
    "以下是微信聊天语音，可能包含中文、英语、人名、金额和日常口语。",
)

STATE_DIR = Path(
    os.environ.get(
        "CHATLOG_PLUS_STATE_DIR",
        Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ChatlogPlus",
    )
).expanduser()
MODEL_DIR = Path(os.environ.get("CHATLOG_PLUS_MODEL_DIR", STATE_DIR / "models")).expanduser()
CACHE_DB = Path(
    os.environ.get("CHATLOG_PLUS_CACHE_DB", STATE_DIR / "transcripts.sqlite3")
).expanduser()
LOG_PATH = Path(os.environ.get("CHATLOG_PLUS_LOG_PATH", STATE_DIR / "server.log")).expanduser()

MODEL_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
)
LOG = logging.getLogger("chatlog-plus")


@dataclass
class Transcript:
    voice_id: str
    text: str
    language: str
    language_probability: float
    duration: float
    model: str
    device: str
    cached: bool
    segments: list[dict[str, Any]]


def http_bytes(url: str, timeout: int = 60) -> tuple[bytes, dict[str, str], str, int]:
    req = Request(url, headers={"User-Agent": f"chatlog-plus/{APP_VERSION}"})
    with urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        headers = {k.lower(): v for k, v in resp.headers.items()}
        return body, headers, resp.geturl(), resp.status


def http_json(url: str, timeout: int = 60) -> Any:
    body, _, _, _ = http_bytes(url, timeout=timeout)
    return json.loads(body.decode("utf-8-sig"))


def probe_url(url: str, timeout: int = 30) -> dict[str, Any]:
    req = Request(
        url,
        headers={"User-Agent": f"chatlog-plus/{APP_VERSION}", "Range": "bytes=0-0"},
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            return {
                "available": True,
                "status": resp.status,
                "content_type": resp.headers.get("Content-Type", ""),
                "content_length": int(resp.headers.get("Content-Length", "0") or 0),
                "resolved_url": resp.geturl(),
            }
    except HTTPError as exc:
        return {
            "available": False,
            "status": exc.code,
            "error": str(exc.reason),
            "resolved_url": url,
        }
    except (URLError, TimeoutError, OSError) as exc:
        return {"available": False, "status": 0, "error": str(exc), "resolved_url": url}


def media_keys(contents: dict[str, Any], media_type: str) -> list[str]:
    fields = {
        "image": ("md5", "path", "thumbpath"),
        "video": ("md5", "rawmd5", "path"),
        "voice": ("voice",),
        "file": ("md5", "path", "file_name", "filename"),
    }[media_type]
    keys: list[str] = []
    for field in fields:
        value = contents.get(field)
        if value is None:
            continue
        value = str(value).strip().replace("\\", "/")
        if value and value not in keys:
            keys.append(value)
    return keys


def media_url(media_type: str, keys: list[str]) -> str:
    encoded = ",".join(quote(key, safe="/") for key in keys)
    return f"{CORE_BASE}/{media_type}/{encoded}"


def media_type_for_message(message: dict[str, Any]) -> str | None:
    msg_type = int(message.get("type", 0) or 0)
    if msg_type == 3:
        return "image"
    if msg_type == 34:
        return "voice"
    if msg_type == 43:
        return "video"
    if msg_type == 49 and int(message.get("subType", 0) or 0) == 6:
        return "file"
    if msg_type == 49:
        contents = message.get("contents") or {}
        if any(contents.get(k) for k in ("path", "file_name", "filename")):
            return "file"
    return None


def has_cuda_runtime() -> bool:
    if os.name != "nt":
        return False
    try:
        ctypes.WinDLL("cublas64_12.dll")
        ctypes.WinDLL("cudnn64_9.dll")
        return True
    except OSError:
        return False


class Transcriber:
    def __init__(self) -> None:
        self._models: dict[tuple[str, str], Any] = {}
        self._model_lock = threading.Lock()
        self._transcribe_lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(CACHE_DB, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS transcripts (
                    voice_id TEXT NOT NULL,
                    audio_sha256 TEXT NOT NULL,
                    model TEXT NOT NULL,
                    requested_language TEXT NOT NULL,
                    detected_language TEXT NOT NULL,
                    language_probability REAL NOT NULL,
                    duration REAL NOT NULL,
                    text TEXT NOT NULL,
                    segments_json TEXT NOT NULL,
                    device TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY (voice_id, audio_sha256, model, requested_language)
                )
                """
            )

    @staticmethod
    def _runtime() -> tuple[str, str]:
        requested = os.environ.get("CHATLOG_PLUS_DEVICE", "auto").lower()
        if requested == "cuda" or (requested == "auto" and has_cuda_runtime()):
            return "cuda", os.environ.get("CHATLOG_PLUS_COMPUTE_TYPE", "float16")
        return "cpu", os.environ.get("CHATLOG_PLUS_COMPUTE_TYPE", "int8")

    def _get_model(self, model_name: str) -> tuple[Any, str]:
        device, compute_type = self._runtime()
        cache_key = (model_name, f"{device}:{compute_type}")
        with self._model_lock:
            if cache_key in self._models:
                return self._models[cache_key], device
            from faster_whisper import WhisperModel

            LOG.info("Loading ASR model=%s device=%s compute=%s", model_name, device, compute_type)
            model = WhisperModel(
                model_name,
                device=device,
                compute_type=compute_type,
                download_root=str(MODEL_DIR),
                cpu_threads=max(1, min(12, os.cpu_count() or 4)),
            )
            self._models[cache_key] = model
            return model, device

    def transcribe(
        self,
        voice_id: str,
        model_name: str = DEFAULT_MODEL,
        language: str = DEFAULT_LANGUAGE,
        force: bool = False,
    ) -> Transcript:
        voice_id = str(voice_id).strip()
        if not voice_id or not voice_id.isdigit():
            raise ValueError("voice_id must contain digits only")

        audio, headers, _, _ = http_bytes(f"{CORE_BASE}/voice/{voice_id}", timeout=60)
        if not audio:
            raise RuntimeError("empty voice response")
        content_type = headers.get("content-type", "")
        suffix = ".mp3" if "mp3" in content_type else ".silk"
        audio_hash = hashlib.sha256(audio).hexdigest()
        requested_language = "auto" if language in ("", "auto", "none") else language

        if not force:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT * FROM transcripts
                    WHERE voice_id=? AND audio_sha256=? AND model=? AND requested_language=?
                    """,
                    (voice_id, audio_hash, model_name, requested_language),
                ).fetchone()
            if row:
                return Transcript(
                    voice_id=voice_id,
                    text=row["text"],
                    language=row["detected_language"],
                    language_probability=row["language_probability"],
                    duration=row["duration"],
                    model=row["model"],
                    device=row["device"],
                    cached=True,
                    segments=json.loads(row["segments_json"]),
                )

        model, device = self._get_model(model_name)
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
            handle.write(audio)
            temp_path = Path(handle.name)
        try:
            with self._transcribe_lock:
                segment_iter, info = model.transcribe(
                    str(temp_path),
                    language=None if requested_language == "auto" else requested_language,
                    beam_size=5,
                    vad_filter=True,
                    condition_on_previous_text=False,
                    initial_prompt=DEFAULT_PROMPT,
                )
                segments = [
                    {
                        "start": round(float(segment.start), 3),
                        "end": round(float(segment.end), 3),
                        "text": segment.text.strip(),
                    }
                    for segment in segment_iter
                    if segment.text.strip()
                ]
        finally:
            temp_path.unlink(missing_ok=True)

        text = "".join(segment["text"] for segment in segments).strip()
        detected_language = str(getattr(info, "language", requested_language))
        language_probability = float(getattr(info, "language_probability", 0.0) or 0.0)
        duration = float(getattr(info, "duration", 0.0) or 0.0)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO transcripts
                (voice_id, audio_sha256, model, requested_language, detected_language,
                 language_probability, duration, text, segments_json, device, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    voice_id,
                    audio_hash,
                    model_name,
                    requested_language,
                    detected_language,
                    language_probability,
                    duration,
                    text,
                    json.dumps(segments, ensure_ascii=False),
                    device,
                    int(time.time()),
                ),
            )
        return Transcript(
            voice_id=voice_id,
            text=text,
            language=detected_language,
            language_probability=language_probability,
            duration=duration,
            model=model_name,
            device=device,
            cached=False,
            segments=segments,
        )


TRANSCRIBER = Transcriber()


def enrich_messages(
    messages: list[dict[str, Any]],
    resolve_media: bool = False,
    transcribe_voice: bool = False,
    transcript_limit: int = 20,
    model_name: str = DEFAULT_MODEL,
    language: str = DEFAULT_LANGUAGE,
) -> list[dict[str, Any]]:
    transcript_count = 0
    for message in messages:
        contents = message.get("contents")
        if not isinstance(contents, dict):
            contents = {}
            message["contents"] = contents
        kind = media_type_for_message(message)
        if not kind:
            continue
        keys = media_keys(contents, kind)
        if not keys:
            continue
        url = media_url(kind, keys)
        media: dict[str, Any] = {"type": kind, "keys": keys, "url": url}
        if resolve_media:
            media.update(probe_url(url))
            if kind == "video" and media.get("content_type", "").startswith("image/"):
                media["thumbnail_only"] = True
        contents["media"] = media
        if kind == "voice" and transcribe_voice:
            if transcript_limit > 0 and transcript_count >= transcript_limit:
                contents["transcript_error"] = "transcript limit reached"
                continue
            transcript_count += 1
            try:
                transcript = TRANSCRIBER.transcribe(keys[0], model_name=model_name, language=language)
                contents["transcript"] = asdict(transcript)
            except Exception as exc:
                LOG.exception("Voice transcription failed for voice ID")
                contents["transcript_error"] = str(exc)
    return messages


def core_chatlog_url(params: dict[str, list[str]]) -> str:
    core_params: list[tuple[str, str]] = []
    plus_only = {
        "resolve_media",
        "transcribe_voice",
        "transcript_limit",
        "transcript_model",
        "transcript_language",
    }
    for key, values in params.items():
        if key in plus_only:
            continue
        for value in values:
            core_params.append((key, value))
    core_params = [(key, value) for key, value in core_params if key != "format"]
    core_params.append(("format", "json"))
    return f"{CORE_BASE}/api/v1/chatlog?{urlencode(core_params)}"


def bool_param(params: dict[str, list[str]], name: str, default: bool = False) -> bool:
    value = params.get(name, ["1" if default else "0"])[-1].lower()
    return value in {"1", "true", "yes", "on"}


class Handler(BaseHTTPRequestHandler):
    server_version = f"ChatlogPlus/{APP_VERSION}"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        LOG.info(
            "%s - %s %s - %s",
            self.client_address[0],
            self.command,
            urlparse(self.path).path,
            code,
        )

    def send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query, keep_blank_values=True)
        try:
            if parsed.path == "/health":
                core = probe_url(f"{CORE_BASE}/health", timeout=5)
                self.send_json(
                    {
                        "status": "ok" if core.get("available") else "degraded",
                        "core": core,
                        "model": DEFAULT_MODEL,
                        "language": DEFAULT_LANGUAGE,
                        "asr_device": Transcriber._runtime()[0],
                    }
                )
                return

            if parsed.path.startswith("/api/v1/transcript/"):
                voice_id = parsed.path.rsplit("/", 1)[-1]
                transcript = TRANSCRIBER.transcribe(
                    voice_id,
                    model_name=params.get("model", [DEFAULT_MODEL])[-1],
                    language=params.get("language", [DEFAULT_LANGUAGE])[-1],
                    force=bool_param(params, "force"),
                )
                self.send_json(asdict(transcript))
                return

            if parsed.path == "/api/v1/media/status":
                media_type = params.get("type", [""])[-1]
                key = params.get("key", [""])[-1]
                if media_type not in {"image", "video", "voice", "file"} or not key:
                    self.send_json({"error": "type and key are required"}, HTTPStatus.BAD_REQUEST)
                    return
                url = media_url(media_type, [key])
                self.send_json({"type": media_type, "key": key, "url": url, **probe_url(url)})
                return

            if parsed.path == "/api/v1/chatlog":
                messages = http_json(core_chatlog_url(params), timeout=120)
                if not isinstance(messages, list):
                    raise RuntimeError("unexpected Chatlog response")
                result = enrich_messages(
                    messages,
                    resolve_media=bool_param(params, "resolve_media"),
                    transcribe_voice=bool_param(params, "transcribe_voice"),
                    transcript_limit=int(params.get("transcript_limit", ["20"])[-1]),
                    model_name=params.get("transcript_model", [DEFAULT_MODEL])[-1],
                    language=params.get("transcript_language", [DEFAULT_LANGUAGE])[-1],
                )
                self.send_json(result)
                return

            if parsed.path in {"/api/v1/session", "/api/v1/contact", "/api/v1/chatroom"}:
                query = f"?{parsed.query}" if parsed.query else ""
                body, headers, _, status = http_bytes(f"{CORE_BASE}{parsed.path}{query}", timeout=60)
                self.send_response(status)
                self.send_header("Content-Type", headers.get("content-type", "application/json"))
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            self.send_json(
                {
                    "name": APP_NAME,
                    "version": APP_VERSION,
                    "core": CORE_BASE,
                    "endpoints": [
                        "/api/v1/chatlog",
                        "/api/v1/transcript/<voice_id>",
                        "/api/v1/media/status?type=<type>&key=<key>",
                        "/health",
                    ],
                }
            )
        except HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:
                detail = str(exc)
            self.send_json({"error": detail, "status": exc.code}, exc.code)
        except Exception as exc:
            LOG.exception("Request failed: %s", parsed.path)
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)


def command_doctor(_: argparse.Namespace) -> int:
    report = {
        "core": probe_url(f"{CORE_BASE}/health", timeout=5),
        "core_url": CORE_BASE,
        "plus_url": f"http://{PLUS_HOST}:{PLUS_PORT}",
        "model": DEFAULT_MODEL,
        "model_dir": str(MODEL_DIR),
        "cache_db": str(CACHE_DB),
        "asr_runtime": {"device": Transcriber._runtime()[0], "cuda_runtime": has_cuda_runtime()},
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["core"].get("available") else 1


def command_transcribe(args: argparse.Namespace) -> int:
    transcript = TRANSCRIBER.transcribe(
        args.voice_id,
        model_name=args.model,
        language=args.language,
        force=args.force,
    )
    print(json.dumps(asdict(transcript), ensure_ascii=False, indent=2))
    return 0


def command_chatlog(args: argparse.Namespace) -> int:
    params: dict[str, list[str]] = {
        "time": [args.time],
        "talker": [args.talker],
        "format": ["json"],
    }
    if args.sender:
        params["sender"] = [args.sender]
    if args.keyword:
        params["keyword"] = [args.keyword]
    if args.limit:
        params["limit"] = [str(args.limit)]
    messages = http_json(core_chatlog_url(params), timeout=120)
    result = enrich_messages(
        messages,
        resolve_media=args.resolve_media,
        transcribe_voice=args.transcribe_voice,
        transcript_limit=args.transcript_limit,
        model_name=args.model,
        language=args.language,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def command_serve(_: argparse.Namespace) -> int:
    server = ThreadingHTTPServer((PLUS_HOST, PLUS_PORT), Handler)
    LOG.info("Chatlog Plus listening on http://%s:%d; core=%s", PLUS_HOST, PLUS_PORT, CORE_BASE)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local media and voice layer for Chatlog")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="check the core service and ASR runtime")
    doctor.set_defaults(func=command_doctor)

    serve = sub.add_parser("serve", help="start the enhanced HTTP API")
    serve.set_defaults(func=command_serve)

    transcribe = sub.add_parser("transcribe", help="transcribe one Chatlog voice ID")
    transcribe.add_argument("voice_id")
    transcribe.add_argument("--model", default=DEFAULT_MODEL)
    transcribe.add_argument("--language", default=DEFAULT_LANGUAGE)
    transcribe.add_argument("--force", action="store_true")
    transcribe.set_defaults(func=command_transcribe)

    chatlog = sub.add_parser("chatlog", help="query and enrich Chatlog messages")
    chatlog.add_argument("--time", required=True)
    chatlog.add_argument("--talker", required=True)
    chatlog.add_argument("--sender")
    chatlog.add_argument("--keyword")
    chatlog.add_argument("--limit", type=int, default=0)
    chatlog.add_argument("--resolve-media", action="store_true")
    chatlog.add_argument("--transcribe-voice", action="store_true")
    chatlog.add_argument("--transcript-limit", type=int, default=20)
    chatlog.add_argument("--model", default=DEFAULT_MODEL)
    chatlog.add_argument("--language", default=DEFAULT_LANGUAGE)
    chatlog.set_defaults(func=command_chatlog)

    moments.add_parser(sub)
    return parser


def main() -> int:
    args = parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
