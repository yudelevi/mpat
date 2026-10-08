"""Fetch the upstream source a lock entry was taken from, for `mpat diff`."""

from __future__ import annotations

import ast
import base64
import http.client
import inspect
import json
import netrc
import os
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from http.client import HTTPMessage
from pathlib import Path
from typing import IO, Any

from packaging.utils import (
    InvalidSdistFilename,
    InvalidWheelFilename,
    canonicalize_name,
    parse_sdist_filename,
    parse_wheel_filename,
)
from packaging.version import InvalidVersion, Version

from mpat._errors import MpatError, TargetNotFound
from mpat._fingerprint import (
    KIND_FILE,
    KIND_MODULE,
    SourceNode,
    definition_node,
    first_line,
    hash_bytes,
    hash_node,
    relative_source,
    source_node,
    source_path,
)
from mpat._lock import LockEntry
from mpat._targets import ResolvedFile, resolve

INDEX_ENV_VARS = ("UV_DEFAULT_INDEX", "UV_INDEX_URL", "PIP_INDEX_URL")
DEFAULT_INDEX = "https://pypi.org/simple"
CACHE_ENV = "MPAT_CACHE_DIR"
XDG_CACHE_ENV = "XDG_CACHE_HOME"
TIMEOUT_SECONDS = 10

_SIMPLE_JSON = "application/vnd.pypi.simple.v1+json"
_ACCEPT = f"{_SIMPLE_JSON}, text/html;q=0.1"
_PURE_WHEEL_TAG = "py3-none-any"
_SDIST_SUFFIXES = (".tar.gz", ".zip")
_INIT = "__init__.py"
_PY = ".py"
_AUTHORIZATION = "Authorization"
_SCHEMES = ("http", "https")


class UpstreamUnavailable(MpatError):
    pass


@dataclass(frozen=True)
class Source:
    path: str
    start: int
    lines: tuple[str, ...]
    matches_lock: bool = True


@dataclass(frozen=True)
class _Link:
    filename: str
    url: str


class _Anchors(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        href = dict(attrs).get("href")
        if tag == "a" and href:
            self.hrefs.append(href)


def _index_url() -> str:
    return next(
        (os.environ[name] for name in INDEX_ENV_VARS if os.environ.get(name)), DEFAULT_INDEX
    )


def _cache_dir() -> Path:
    if explicit := os.environ.get(CACHE_ENV):
        return Path(explicit)
    if xdg := os.environ.get(XDG_CACHE_ENV):
        return Path(xdg) / "mpat"
    try:
        return Path.home() / ".cache" / "mpat"
    except RuntimeError:
        return Path(tempfile.gettempdir()) / "mpat"


def _without_credentials(url: str) -> tuple[str, str | None]:
    """Split userinfo off the index URL; urllib never sends it on its own."""
    parts = urllib.parse.urlsplit(url)
    if parts.username is None:
        return url, None
    host = parts.hostname or ""
    netloc = host if parts.port is None else f"{host}:{parts.port}"
    user = urllib.parse.unquote(parts.username)
    password = urllib.parse.unquote(parts.password or "")
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return urllib.parse.urlunsplit(parts._replace(netloc=netloc)), f"Basic {token}"


def _netrc_auth(host: str) -> str | None:
    try:
        found = netrc.netrc().authenticators(host)
    except (FileNotFoundError, netrc.NetrcParseError):
        return None
    if found is None:
        return None
    login, _, password = found
    return "Basic " + base64.b64encode(f"{login}:{password or ''}".encode()).decode()


class _KeepAuthOnHost(urllib.request.HTTPRedirectHandler):
    """Private indexes redirect downloads to signed object-storage URLs; the index
    credentials must not travel there, and storage rejects a request carrying both."""

    def __init__(self, host: str | None) -> None:
        super().__init__()
        self.host = host

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).hostname != self.host:
            new.remove_header(_AUTHORIZATION)
        return new


def _redacted(url: str) -> str:
    """Signed download URLs carry their token in the query string; keep it out of CI logs."""
    parts = urllib.parse.urlsplit(url)
    netloc = parts.netloc.rpartition("@")[2]
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _get(
    url: str, *, auth: str | None, auth_host: str | None, accept: str | None = None
) -> tuple[bytes, str]:
    if urllib.parse.urlsplit(url).scheme not in _SCHEMES:
        raise UpstreamUnavailable(f"refusing to download {_redacted(url)}: only http and https")
    request = urllib.request.Request(url)
    if accept:
        request.add_header("Accept", accept)
    if auth and urllib.parse.urlsplit(url).hostname == auth_host:
        request.add_header(_AUTHORIZATION, auth)
    opener = urllib.request.build_opener(_KeepAuthOnHost(auth_host))
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            return response.read(), response.headers.get_content_type()
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as exc:
        raise UpstreamUnavailable(f"cannot fetch {_redacted(url)}: {exc}") from exc


def _links(body: bytes, content_type: str, *, page: str) -> list[_Link]:
    if content_type == _SIMPLE_JSON:
        try:
            files: list[dict[str, Any]] = json.loads(body)["files"]
        except (ValueError, KeyError, TypeError) as exc:
            raise UpstreamUnavailable(f"unreadable index page {page}: {exc}") from exc
        pairs = [(f["filename"], f["url"]) for f in files]
    else:
        parser = _Anchors()
        parser.feed(body.decode("utf-8", errors="replace"))
        pairs = [(urllib.parse.urlsplit(h).path.rsplit("/", 1)[-1], h) for h in parser.hrefs]
    return [
        _Link(
            filename=urllib.parse.unquote(name),
            url=urllib.parse.urldefrag(urllib.parse.urljoin(page, url)).url,
        )
        for name, url in pairs
    ]


def _archive_version(filename: str, *, dist: str) -> tuple[Version, bool, bool] | None:
    """(version, is_wheel, is_pure) for an archive of `dist`, None for anything else.

    The filename comes from the index and becomes a path in the cache, so it must
    be a bare filename of this very project.
    """
    if Path(filename).name != filename or "\\" in filename:
        return None
    try:
        if filename.endswith(".whl"):
            name, version, _, tags = parse_wheel_filename(filename)
            is_wheel, is_pure = True, any(str(tag) == _PURE_WHEEL_TAG for tag in tags)
        elif filename.endswith(_SDIST_SUFFIXES):
            name, version = parse_sdist_filename(filename)
            is_wheel, is_pure = False, False
        else:
            return None
    except (InvalidWheelFilename, InvalidSdistFilename, InvalidVersion):
        return None
    if name != canonicalize_name(dist):
        return None
    return version, is_wheel, is_pure


def _pick(links: list[_Link], *, dist: str, version: Version) -> _Link | None:
    """Prefer a pure wheel, then any wheel, then an sdist, of exactly `version`."""
    ranked: list[tuple[bool, bool, str, _Link]] = []
    for link in links:
        parsed = _archive_version(link.filename, dist=dist)
        if parsed is None:
            continue
        found, is_wheel, is_pure = parsed
        if found == version:
            ranked.append((not is_wheel, not is_pure, link.filename, link))
    return min(ranked, key=lambda r: r[:3])[-1] if ranked else None


def _archive(dist: str, version: str) -> Path:
    try:
        wanted = Version(version)
    except InvalidVersion as exc:
        raise UpstreamUnavailable(f"{dist} {version}: {exc}") from exc
    index, auth = _without_credentials(_index_url())
    index_host = urllib.parse.urlsplit(index).hostname
    auth = auth or (_netrc_auth(index_host) if index_host else None)
    page = f"{index.rstrip('/')}/{canonicalize_name(dist)}/"
    body, content_type = _get(page, auth=auth, auth_host=index_host, accept=_ACCEPT)
    link = _pick(_links(body, content_type, page=page), dist=dist, version=wanted)
    if link is None:
        raise UpstreamUnavailable(f"no archive for {dist} {version} on {index}")
    cached = _cache_dir() / link.filename
    if cached.is_file():
        return cached
    data, _ = _get(link.url, auth=auth, auth_host=index_host)
    try:
        cached.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=cached.parent, delete=False) as fh:
            fh.write(data)
        os.replace(fh.name, cached)
    except OSError as exc:
        raise UpstreamUnavailable(
            f"cannot cache {link.filename} in {cached.parent}: {exc}"
        ) from exc
    return cached


def _member(archive: Path, source_file: str) -> bytes:
    suffix = "/" + source_file
    try:
        if archive.name.endswith((".whl", ".zip")):
            with zipfile.ZipFile(archive) as zf:
                name = next(
                    (n for n in zf.namelist() if n == source_file or n.endswith(suffix)), None
                )
                if name is not None:
                    return zf.read(name)
        else:
            with tarfile.open(archive) as tf:
                member = next(
                    (m for m in tf.getmembers() if m.isfile() and m.name.endswith(suffix)), None
                )
                extracted = tf.extractfile(member) if member is not None else None
                if extracted is not None:
                    return extracted.read()
    except (zipfile.BadZipFile, tarfile.TarError, OSError) as exc:
        raise UpstreamUnavailable(f"unreadable archive {archive.name}: {exc}") from exc
    raise UpstreamUnavailable(f"{source_file} is not in {archive.name}")


def _module_of(source_file: str) -> str:
    path = source_file.removesuffix(_PY)
    if source_file.endswith("/" + _INIT):
        path = path.removesuffix("/" + _INIT.removesuffix(_PY))
    return path.replace("/", ".")


def _span(lines: list[str], node: SourceNode) -> tuple[int, tuple[str, ...]]:
    if isinstance(node, ast.Module):
        return 1, tuple(lines)
    start = first_line(node)
    return start, tuple(lines[start - 1 : node.end_lineno])


def locked_source(entry: LockEntry) -> Source:
    locked = entry.fingerprint
    if not (locked.dist and locked.dist_version and locked.source_file):
        raise UpstreamUnavailable(f"{entry.target}: no distribution recorded in the lock")
    data = _member(_archive(locked.dist, locked.dist_version), locked.source_file)
    if locked.kind == KIND_FILE:
        return Source(
            path=locked.source_file,
            start=1,
            lines=tuple(data.decode("utf-8", errors="replace").splitlines()),
            matches_lock=hash_bytes(data) == locked.source_hash,
        )
    text = data.decode("utf-8", errors="replace")
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError) as exc:
        raise UpstreamUnavailable(f"cannot parse {locked.source_file}: {exc}") from exc
    module = _module_of(locked.source_file)
    if locked.kind == KIND_MODULE:
        node: SourceNode | None = tree
    elif locked.resolved.startswith(module + "."):
        node = definition_node(tree, locked.resolved.removeprefix(module + "."))
    else:
        raise UpstreamUnavailable(f"{locked.resolved} is not defined in {locked.source_file}")
    if node is None:
        raise UpstreamUnavailable(f"{locked.resolved} not found in {locked.source_file}")
    start, lines = _span(text.splitlines(), node)
    return Source(
        path=locked.source_file,
        start=start,
        lines=lines,
        matches_lock=hash_node(node) == locked.source_hash,
    )


def installed_source(target: str) -> Source | None:
    """The installed definition of `target`, or None when it is gone or has no source."""
    try:
        resolved = resolve(target)
    except TargetNotFound:
        return None
    if isinstance(resolved, ResolvedFile):
        text = resolved.read_bytes().decode("utf-8", errors="replace")
        return Source(path=target, start=1, lines=tuple(text.splitlines()))
    obj = resolved.obj.fget if isinstance(resolved.obj, property) else resolved.obj
    unwrapped = inspect.unwrap(obj)
    file = source_path(unwrapped)
    node = source_node(unwrapped, file) if file else None
    if file is None or node is None:
        return None
    start, lines = _span(Path(file).read_text(encoding="utf-8").splitlines(), node)
    return Source(path=relative_source(file, target.split(".")[0]), start=start, lines=lines)
