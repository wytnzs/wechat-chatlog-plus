# -*- coding: utf-8 -*-
"""WeChat Moments (朋友圈) export from the local sns.db.

Read-only: decrypts a private copy of the Moments database with the data key
already stored by the Chatlog Plus stack (Windows DPAPI), parses SnsTimeLine,
and writes Markdown/JSON exports. The decrypted copy is deleted unless
--keep-db is given.

SQLCipher format (WeChat 4.x): 4096-byte pages, 80-byte reserve
(16-byte IV + 64-byte HMAC-SHA512), key derivation
    enc_key  = PBKDF2-HMAC-SHA512(passphrase, salt, 256000, 32)
    auth_key = PBKDF2-HMAC-SHA512(enc_key, salt ^ 0x3A, 2, 32)
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import struct
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from ctypes import wintypes
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except ImportError:  # pragma: no cover
    Cipher = None

PAGE = 4096
RESERVE = 80
PBKDF2_ITERATIONS = 256000
DEFAULT_STACK_HOME = Path(
    os.environ.get("WECHAT_CHATLOG_HOME")
    or (Path(os.environ.get("LOCALAPPDATA", Path.home())) / "WeChatChatlogStack")
)
DEFAULT_CONFIG = DEFAULT_STACK_HOME / "config" / "wechat.json"
DEFAULT_SECRET = DEFAULT_STACK_HOME / "secrets" / "data-key.dpapi"
LOCAL_TZ = timezone(timedelta(hours=8))

LOG = logging.getLogger("chatlog_plus.moments")


class MomentsError(RuntimeError):
    pass


# ---------------------------------------------------------------- DPAPI / key


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _unprotect_dpapi(blob: bytes) -> bytes:
    if sys.platform != "win32":
        raise MomentsError("DPAPI 只在 Windows 上可用")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    buffer = ctypes.create_string_buffer(blob, len(blob))
    data_in = _DataBlob(len(blob), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    data_out = _DataBlob()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(data_in), None, None, None, None, 0x01, ctypes.byref(data_out)
    ):
        raise MomentsError(f"DPAPI 解保护失败：{ctypes.WinError(ctypes.get_last_error())}")
    try:
        return ctypes.string_at(data_out.pbData, data_out.cbData)
    finally:
        kernel32.LocalFree(data_out.pbData)


def load_passphrase(secret_path: Path = DEFAULT_SECRET) -> bytes:
    """Return the 32-byte SQLCipher passphrase stored by save-wechat-keys.ps1."""
    if not secret_path.exists():
        raise MomentsError(f"找不到密钥文件：{secret_path}")
    raw = secret_path.read_bytes().strip()
    text = raw.decode("ascii", errors="ignore").strip()
    if text and re.fullmatch(r"[0-9a-fA-F]+", text) and len(text) % 2 == 0:
        blob = bytes.fromhex(text)
    else:
        blob = raw
    plain = _unprotect_dpapi(blob)
    if b"\x00" in plain and len(plain) % 2 == 0:
        key_hex = plain.decode("utf-16-le", errors="ignore").strip()
    else:
        key_hex = plain.decode("utf-8", errors="ignore").strip()
    key_hex = re.sub(r"[^0-9a-fA-F]", "", key_hex)
    if len(key_hex) != 64:
        raise MomentsError("密钥格式不对（应为 32 字节 / 64 位十六进制）")
    return bytes.fromhex(key_hex)


def load_config(config_path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    if not config_path.exists():
        raise MomentsError(f"找不到配置文件：{config_path}")
    return json.loads(config_path.read_text(encoding="utf-8-sig"))


def account_wxid(config: dict[str, Any]) -> str | None:
    data_dir = str(config.get("data_dir") or "")
    name = Path(data_dir).name
    match = re.match(r"^(wxid_[A-Za-z0-9]+)_[0-9a-fA-F]{4}$", name)
    return match.group(1) if match else None


# ------------------------------------------------------------------ decryption


def _keys(passphrase: bytes, salt: bytes) -> tuple[bytes, bytes]:
    enc_key = hashlib.pbkdf2_hmac("sha512", passphrase, salt, PBKDF2_ITERATIONS, 32)
    mac_salt = bytes(byte ^ 0x3A for byte in salt)
    auth_key = hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, 32)
    return enc_key, auth_key


def decrypt_database(source: Path, destination: Path, passphrase: bytes) -> int:
    """Decrypt a SQLCipher database; returns the number of pages written."""
    if Cipher is None:
        raise MomentsError("缺少 cryptography 依赖：pip install cryptography")
    data = source.read_bytes()
    pages = len(data) // PAGE
    if pages == 0:
        raise MomentsError(f"文件太小，不是微信数据库：{source}")
    data = data[: pages * PAGE]
    enc_key, auth_key = _keys(passphrase, data[:16])
    out = bytearray()
    for index in range(pages):
        page = data[index * PAGE : (index + 1) * PAGE]
        start = 16 if index == 0 else 0
        message = page[start : PAGE - 64] + struct.pack("<I", index + 1)
        if not hmac.compare_digest(
            hmac.new(auth_key, message, hashlib.sha512).digest(), page[PAGE - 64 :]
        ):
            raise MomentsError(f"第 {index + 1} 页校验失败，微信版本或密钥可能已变化")
        iv = page[PAGE - RESERVE : PAGE - 64]
        decryptor = Cipher(algorithms.AES(enc_key), modes.CBC(iv)).decryptor()
        plain = decryptor.update(page[start : PAGE - RESERVE]) + decryptor.finalize()
        if index == 0:
            if plain[:2] != b"\x10\x00":
                raise MomentsError("解密后的 SQLite 头不正确")
            out += b"SQLite format 3\x00"
        out += plain + b"\x00" * RESERVE
    destination.write_bytes(bytes(out))
    return pages


# --------------------------------------------------------------------- parsing


@dataclass
class Moment:
    id: str
    username: str
    created: datetime
    text: str
    media: list[dict[str, str]] = field(default_factory=list)


def _text_of(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return "".join(node.itertext()).strip()


def parse_moment(content: str) -> Moment | None:
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return None
    timeline = root.find(".//TimelineObject")
    if timeline is None:
        return None
    moment_id = (timeline.findtext("id") or "").strip()
    username = (timeline.findtext("username") or "").strip()
    try:
        epoch = int((timeline.findtext("createTime") or "0").strip())
    except ValueError:
        epoch = 0
    if not moment_id or epoch <= 0:
        return None
    text = (timeline.findtext("contentDesc") or "").strip()
    if not text:
        content_object = timeline.find(".//ContentObject")
        if content_object is not None:
            text = _text_of(content_object.find("title")) or _text_of(content_object.find("description"))
    media: list[dict[str, str]] = []
    for item in timeline.findall(".//mediaList/media"):
        url = (item.findtext("url") or "").strip()
        thumb = ""
        thumb_node = item.find("thumb")
        if thumb_node is not None:
            thumb = (thumb_node.text or "").strip()
        media.append(
            {
                "type": (item.findtext("type") or "").strip(),
                "url": url,
                "thumb": thumb,
            }
        )
    return Moment(
        id=moment_id,
        username=username,
        created=datetime.fromtimestamp(epoch, LOCAL_TZ),
        text=text,
        media=[m for m in media if m["url"] or m["thumb"]],
    )


def read_moments(
    db_path: Path,
    *,
    self_wxid: str | None = None,
    self_only: bool = False,
    since: datetime | None = None,
    until: datetime | None = None,
) -> tuple[list[Moment], dict[str, int]]:
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    stats = {"rows": 0, "parsed": 0, "failed": 0, "dup": 0, "filtered": 0}
    seen: set[str] = set()
    result: list[Moment] = []
    try:
        rows = connection.execute("select user_name, content from SnsTimeLine").fetchall()
        stats["rows"] = len(rows)
        for _username, content in rows:
            if not isinstance(content, str):
                stats["failed"] += 1
                continue
            moment = parse_moment(content)
            if moment is None:
                stats["failed"] += 1
                continue
            stats["parsed"] += 1
            if moment.id in seen:
                stats["dup"] += 1
                continue
            seen.add(moment.id)
            if self_only and self_wxid and moment.username != self_wxid:
                stats["filtered"] += 1
                continue
            if since and moment.created < since:
                stats["filtered"] += 1
                continue
            if until and moment.created > until:
                stats["filtered"] += 1
                continue
            result.append(moment)
    finally:
        connection.close()
    result.sort(key=lambda item: item.created)
    return result, stats


# --------------------------------------------------------------------- outputs


def _safe_unlink(path: Path, attempts: int = 5) -> None:
    for _ in range(attempts):
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return
        except PermissionError:
            time.sleep(0.2)


def find_contacts(query: str, config: dict[str, Any], passphrase: bytes) -> list[dict[str, str]]:
    """Resolve a name/remark to one or more WeChat contacts via contact.db."""
    data_dir = Path(config.get("data_dir") or "")
    contact_db = data_dir / "db_storage" / "contact" / "contact.db"
    if not contact_db.exists():
        raise MomentsError(f"找不到通讯录库：{contact_db}")
    temp_path = Path(tempfile.mkstemp(prefix="contact-plain-", suffix=".db")[1])
    try:
        decrypt_database(contact_db, temp_path, passphrase)
        connection = sqlite3.connect(f"file:{temp_path.as_posix()}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                "select username, remark, nick_name, alias from contact "
                "where remark=? or nick_name=? or alias=? or username=?",
                (query, query, query, query),
            ).fetchall()
            exact = True
            if not rows:
                like = f"%{query}%"
                rows = connection.execute(
                    "select username, remark, nick_name, alias from contact "
                    "where remark like ? or nick_name like ? or alias like ? limit 20",
                    (like, like, like),
                ).fetchall()
                exact = False
        finally:
            connection.close()
    finally:
        _safe_unlink(temp_path)
    result = []
    for username, remark, nick, alias in rows:
        result.append(
            {
                "username": username or "",
                "remark": remark or "",
                "nick": nick or "",
                "alias": alias or "",
                "exact": "1" if exact else "0",
            }
        )
    return result


def command_moments_person(args: argparse.Namespace) -> int:
    """On-demand: export only one person's Moments to stdout (no files by default)."""
    config = load_config(Path(args.config))
    data_dir = Path(args.data_dir or config.get("data_dir") or "")
    if not data_dir:
        raise MomentsError("配置里没有 data_dir，请用 --data-dir 指定")
    sns_path = Path(args.db) if args.db else data_dir / "db_storage" / "sns" / "sns.db"
    if not sns_path.exists():
        raise MomentsError(f"找不到朋友圈库：{sns_path}")
    passphrase = load_passphrase(Path(args.secret))

    person = {"query": args.name or args.wxid or "", "wxid": args.wxid or "", "remark": "", "nick": "", "alias": ""}
    if not args.wxid:
        candidates = find_contacts(args.name, config, passphrase)
        if not candidates:
            print(json.dumps({"status": "not_found", "query": args.name}, ensure_ascii=False))
            return 2
        preferred = [item for item in candidates if item["exact"] == "1"] or candidates
        if len(preferred) > 1 and not args.first:
            print(json.dumps({"status": "ambiguous", "candidates": preferred}, ensure_ascii=False, indent=1))
            return 2
        person.update(preferred[0])
        person["wxid"] = preferred[0]["username"]
    else:
        person["wxid"] = args.wxid

    temp_path = None
    try:
        handle = tempfile.NamedTemporaryFile(
            prefix="sns-person-", suffix=".db", dir=tempfile.gettempdir(), delete=False
        )
        temp_path = Path(handle.name)
        handle.close()
        decrypt_database(sns_path, temp_path, passphrase)
        moments, stats = read_moments(
            temp_path,
            since=_parse_date(args.since) if args.since else None,
            until=_parse_date(args.until, end_of_day=True) if args.until else None,
        )
    finally:
        if temp_path and not args.keep_db:
            _safe_unlink(temp_path)

    posts = [item for item in moments if item.username == person["wxid"]]
    posts.sort(key=lambda item: item.created, reverse=True)
    total = len(posts)
    if args.limit > 0:
        posts = posts[: args.limit]
    payload = {
        "status": "ok",
        "person": person,
        "posts_total": total,
        "range": [posts[-1].created.strftime("%Y-%m-%d"), posts[0].created.strftime("%Y-%m-%d")] if posts else [],
        "posts": [
            {
                "date": item.created.strftime("%Y-%m-%d %H:%M"),
                "text": item.text,
                "media_count": len(item.media),
                **({"media": [m.get("url") or m.get("thumb") for m in item.media]} if args.media_urls else {}),
            }
            for item in posts
        ],
        "stats": stats,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        print(json.dumps({"status": "ok", "file": str(out_path), "posts_total": total}, ensure_ascii=False))
    else:
        print(text)
    return 0


def write_json(moments: list[Moment], path: Path) -> None:
    payload = [
        {
            "id": item.id,
            "username": item.username,
            "created": item.created.isoformat(),
            "text": item.text,
            "media": item.media,
        }
        for item in moments
    ]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def write_markdown(
    moments: list[Moment], path: Path, *, self_wxid: str | None, self_only: bool
) -> None:
    total = len(moments)
    with_text = sum(1 for item in moments if item.text)
    with_media = sum(1 for item in moments if item.media)
    first = moments[0].created.strftime("%Y-%m-%d %H:%M") if moments else "-"
    last = moments[-1].created.strftime("%Y-%m-%d %H:%M") if moments else "-"
    lines = [
        "# 微信朋友圈导出",
        "",
        f"- 账号：{self_wxid or '-'}",
        f"- 范围：{'仅本人' if self_only else '全部（本人 + 好友）'}",
        f"- 时间：{first} ~ {last}",
        f"- 条数：{total}（有文字 {with_text} / 带图 {with_media}）",
        f"- 导出时间：{datetime.now(LOCAL_TZ).strftime('%Y-%m-%d %H:%M')}",
        "- 说明：只含本机已同步数据；图片为引用地址，不是图片文件。",
        "",
    ]
    current_month = None
    for item in moments:
        month = item.created.strftime("%Y-%m")
        if month != current_month:
            lines += [f"## {month}", ""]
            current_month = month
        lines.append(f"### {item.created.strftime('%Y-%m-%d %H:%M')} · {item.username}")
        lines.append("")
        lines.append(item.text if item.text else "（无文字）")
        if item.media:
            lines.append("")
            for index, media in enumerate(item.media, 1):
                url = media.get("url") or media.get("thumb")
                lines.append(f"- 图片{index}：{url}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


# ------------------------------------------------------------------------ CLI


def _parse_date(value: str, end_of_day: bool = False) -> datetime:
    parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=LOCAL_TZ)
    if end_of_day:
        parsed = parsed.replace(hour=23, minute=59, second=59)
    return parsed


def command_moments_export(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))
    data_dir = Path(args.data_dir or config.get("data_dir") or "")
    if not data_dir:
        raise MomentsError("配置里没有 data_dir，请用 --data-dir 指定")
    db_path = Path(args.db) if args.db else data_dir / "db_storage" / "sns" / "sns.db"
    if not db_path.exists():
        raise MomentsError(f"找不到朋友圈库：{db_path}")
    wal = db_path.with_name(db_path.name + "-wal")
    if wal.exists() and wal.stat().st_size > 0:
        LOG.warning("检测到 sns.db-wal（%s KB），最新几条可能不在本次导出里", wal.stat().st_size // 1024)

    self_wxid = args.wxid or account_wxid(config)
    passphrase = load_passphrase(Path(args.secret))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="sns-plain-", suffix=".db", dir=tempfile.gettempdir(), delete=False
        ) as handle:
            temp_path = Path(handle.name)
        pages = decrypt_database(db_path, temp_path, passphrase)
        LOG.info("已解密 %s 页", pages)
        moments, stats = read_moments(
            temp_path,
            self_wxid=self_wxid,
            self_only=args.self_only,
            since=_parse_date(args.since) if args.since else None,
            until=_parse_date(args.until, end_of_day=True) if args.until else None,
        )
    finally:
        if temp_path and not args.keep_db:
            _safe_unlink(temp_path)

    formats = {"md", "json"} if args.format == "both" else {args.format}
    written = []
    if "md" in formats:
        path = out_dir / f"moments{'-self' if args.self_only else ''}.md"
        write_markdown(moments, path, self_wxid=self_wxid, self_only=args.self_only)
        written.append(str(path))
    if "json" in formats:
        path = out_dir / f"moments{'-self' if args.self_only else ''}.json"
        write_json(moments, path)
        written.append(str(path))
    print(
        json.dumps(
            {
                "status": "ok",
                "exported": len(moments),
                "stats": stats,
                "files": written,
                "self_wxid": self_wxid,
            },
            ensure_ascii=False,
        )
    )
    return 0


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[attr-defined]
    moments = sub.add_parser("moments", help="export WeChat Moments (朋友圈) from local sns.db")
    inner = moments.add_subparsers(dest="moments_command", required=True)
    export = inner.add_parser("export", help="decrypt sns.db and export Markdown/JSON")
    export.add_argument("--out", required=True, help="导出目录（私密目录）")
    export.add_argument("--self-only", action="store_true", help="只导出本人发布的朋友圈")
    export.add_argument("--since", help="起始日期 YYYY-MM-DD")
    export.add_argument("--until", help="结束日期 YYYY-MM-DD")
    export.add_argument("--format", choices=["md", "json", "both"], default="md")
    export.add_argument("--config", default=str(DEFAULT_CONFIG))
    export.add_argument("--secret", default=str(DEFAULT_SECRET))
    export.add_argument("--data-dir", help="覆盖配置里的微信账号数据目录")
    export.add_argument("--db", help="直接指定 sns.db 路径")
    export.add_argument("--wxid", help="本人 wxid（默认从数据目录名推断）")
    export.add_argument("--keep-db", action="store_true", help="保留解密出的明文库（调试用）")
    export.set_defaults(func=command_moments_export)

    person = inner.add_parser("person", help="on-demand: one person's Moments to stdout")
    who = person.add_mutually_exclusive_group(required=True)
    who.add_argument("--name", help="备注/昵称/微信号，先在 contact.db 里解析")
    who.add_argument("--wxid", help="直接指定 wxid，跳过通讯录解析")
    person.add_argument("--since", help="起始日期 YYYY-MM-DD")
    person.add_argument("--until", help="结束日期 YYYY-MM-DD")
    person.add_argument("--limit", type=int, default=20, help="最多返回几条，0 表示全部")
    person.add_argument("--media-urls", action="store_true", help="附带图片引用地址")
    person.add_argument("--first", action="store_true", help="命中多人时取第一个")
    person.add_argument("--out", help="可选：写入文件；默认只打印到终端")
    person.add_argument("--config", default=str(DEFAULT_CONFIG))
    person.add_argument("--secret", default=str(DEFAULT_SECRET))
    person.add_argument("--data-dir", help="覆盖配置里的微信账号数据目录")
    person.add_argument("--db", help="直接指定 sns.db 路径")
    person.add_argument("--keep-db", action="store_true", help="保留解密出的明文库（调试用）")
    person.set_defaults(func=command_moments_person)
