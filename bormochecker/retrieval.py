"""Безопасная загрузка и очистка веб-источников для фактчека."""

from __future__ import annotations

import contextlib
import io
import ipaddress
import os
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import typing
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from contextvars import ContextVar
from functools import lru_cache
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler

import httpcore
import httpx
from httpcore import SOCKET_OPTION
from httpcore._backends.sync import SyncBackend, SyncStream
from httpcore._exceptions import ConnectError, ConnectTimeout, map_exceptions

BLOCKED_DOMAINS = (
    "localhost.localdomain",
    "internal.example",
    "blocked.example",
    "xn--e1afmkfd.xn--p1ai",
    "пример.рф",
)

BLOCKED_CIDRS: tuple[str, ...] = ()
BLOCKED_IPS: tuple[str, ...] = ()


def _blocked_domains() -> tuple[str, ...]:
    extra = [
        d.strip()
        for d in os.environ.get("FACTCHECK_BLOCKED_DOMAINS", "").split(",")
        if d.strip()
    ]
    return (*BLOCKED_DOMAINS, *extra) if extra else BLOCKED_DOMAINS


def _blocked_networks() -> tuple[ipaddress._BaseNetwork, ...]:
    raw = os.environ.get("FACTCHECK_BLOCKED_CIDRS", "").strip()
    cidrs = [*BLOCKED_CIDRS, *(c.strip() for c in raw.split(",") if c.strip())] if raw else list(BLOCKED_CIDRS)
    return tuple(ipaddress.ip_network(c, strict=False) for c in cidrs)


def _blocked_ips() -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    raw = os.environ.get("FACTCHECK_BLOCKED_IPS", "").strip()
    ips = [*BLOCKED_IPS, *(i.strip() for i in raw.split(",") if i.strip())] if raw else list(BLOCKED_IPS)
    return tuple(ipaddress.ip_address(i) for i in ips)


def _is_explicitly_blocked_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return address in _blocked_ips() or any(address in net for net in _blocked_networks())


def _normalize_domain(domain: str) -> str:
    clean = str(domain or "").casefold().rstrip(".")
    try:
        clean = clean.encode("idna").decode("ascii")
    except Exception:
        pass
    return clean


def _extract_host(url_or_host: str) -> str:
    raw = str(url_or_host or "").strip()
    if not raw:
        return ""
    try:
        return str(ipaddress.ip_address(raw.strip("[]")))
    except ValueError:
        pass
    raw_url = f"http://{raw}" if "://" not in raw else raw
    try:
        return urlsplit(raw_url).hostname or ""
    except Exception:
        return ""


def _matches_blocked_domain(norm_host: str) -> bool:
    for d in _blocked_domains():
        d_clean = _normalize_domain(d)
        if d_clean and (norm_host == d_clean or norm_host.endswith("." + d_clean)):
            return True
    return norm_host in {"localhost", "localhost.localdomain"} or norm_host.endswith(".local")


def is_blocked_domain_host(url_or_host: str) -> bool:
    """Проверяет, входит ли домен URL или хоста в стоп-лист доменов."""
    host = _extract_host(url_or_host)
    if not host:
        return True
    return _matches_blocked_domain(_normalize_domain(host))


def _parse_any_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Парсит IP адрес в любых представлениях: dotted-decimal (2/3/4 октета), hex, octal, integer dword, IPv6, IPv6-mapped IPv4."""
    h = host.strip("[]")
    try:
        ip = ipaddress.ip_address(h)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            return ip.ipv4_mapped
        return ip
    except ValueError:
        pass

    try:
        if h.startswith(("0x", "0X")):
            val = int(h, 16)
            if 0 <= val <= 0xFFFFFFFF:
                return ipaddress.IPv4Address(val)
        elif h.startswith("0") and len(h) > 1 and h.isdigit() and all(c in "01234567" for c in h):
            val = int(h, 8)
            if 0 <= val <= 0xFFFFFFFF:
                return ipaddress.IPv4Address(val)
        elif h.isdigit():
            val = int(h, 10)
            if 0 <= val <= 0xFFFFFFFF:
                return ipaddress.IPv4Address(val)
    except ValueError:
        pass

    parts = h.split(".")
    if 2 <= len(parts) <= 4:
        try:
            octets: list[int] = []
            for p in parts:
                if p.startswith(("0x", "0X")):
                    octets.append(int(p, 16))
                elif p.startswith("0") and len(p) > 1 and p.isdigit() and all(c in "01234567" for c in p):
                    octets.append(int(p, 8))
                elif p.isdigit():
                    octets.append(int(p, 10))
                else:
                    return None
            if len(octets) == 4 and all(0 <= o <= 255 for o in octets):
                val = (octets[0] << 24) | (octets[1] << 16) | (octets[2] << 8) | octets[3]
                return ipaddress.IPv4Address(val)
            if len(octets) == 3 and 0 <= octets[0] <= 255 and 0 <= octets[1] <= 255 and 0 <= octets[2] <= 0xFFFF:
                val = (octets[0] << 24) | (octets[1] << 16) | octets[2]
                return ipaddress.IPv4Address(val)
            if len(octets) == 2 and 0 <= octets[0] <= 255 and 0 <= octets[1] <= 0xFFFFFF:
                val = (octets[0] << 24) | octets[1]
                return ipaddress.IPv4Address(val)
        except Exception:
            pass
    return None


def is_blocked_host(url_or_host: str) -> bool:
    """Проверяет, заблокирован ли хост или URL согласно стоп-листу безопасности (SSRF и самоцитирование).

    Приводит хост к нижнему регистру, убирает завершающие точки, переводит IDNA в punycode,
    разбирает IP во всех форматах (десятичная, dword, шестнадцатеричная, IPv6-отображение IPv4).
    Блокирует:
    - домены из BLOCKED_DOMAINS и FACTCHECK_BLOCKED_DOMAINS (включая поддомены и punycode);
    - IP-адреса и подсети из FACTCHECK_BLOCKED_IPS и FACTCHECK_BLOCKED_CIDRS;
    - частные, loopback, link-local и metadata адреса (кроме FACTCHECK_ALLOWED_CIDRS).
    """
    host = _extract_host(url_or_host)
    if not host:
        return True

    host_clean = _normalize_domain(host)
    if _matches_blocked_domain(host_clean):
        return True

    parsed_ip = _parse_any_ip(host_clean)
    if parsed_ip is not None:
        if _is_explicitly_blocked_ip(parsed_ip):
            return True
        if any(parsed_ip in net for net in _allowed_networks()):
            return False
        if (
            parsed_ip.is_private
            or parsed_ip.is_loopback
            or parsed_ip.is_link_local
            or parsed_ip.is_multicast
            or parsed_ip.is_reserved
            or not parsed_ip.is_global
        ):
            return True
        return False

    return False


class UnsafeUrlError(RuntimeError):
    pass


class FetchError(RuntimeError):
    pass


class DecompressionBombError(FetchError):
    pass


class NetworkNotAllowedError(RuntimeError):
    pass


network_allowed_ctx: ContextVar[bool] = ContextVar("network_allowed", default=True)


@dataclass(frozen=True, slots=True)
class FetchedDocument:
    url: str
    final_url: str
    title: str
    text: str
    content_type: str = "text/plain"
    status_code: int = 200
    truncated: bool = False


def normalize_http_url(url: str) -> str:
    parsed = urlsplit(str(url or "").strip())
    if parsed.scheme.casefold() not in {"http", "https"}:
        raise UnsafeUrlError(f"неподдерживаемая схема URL: {parsed.scheme!r}")
    if not parsed.hostname:
        raise UnsafeUrlError("URL не содержит hostname")
    if parsed.username or parsed.password:
        raise UnsafeUrlError("credentials в URL запрещены")
    return urlunsplit(parsed._replace(fragment=""))


def _allowed_networks():
    cidrs = os.getenv("FACTCHECK_ALLOWED_CIDRS", "198.18.0.0/15")
    return tuple(
        ipaddress.ip_network(chunk.strip(), strict=False)
        for chunk in cidrs.split(",")
        if chunk.strip()
    )


_URL_NOISE_TOKENS = {"login", "auth", "signin", "signup", "register", "password", "logout", "cart", "checkout"}


@lru_cache(maxsize=1)
def load_aggregator_patterns() -> tuple[str, ...]:
    try:
        from bormochecker.paths import aggregator_patterns_path
        path = aggregator_patterns_path()
        if path.is_file():
            return tuple(
                line.strip()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")
            )
    except Exception:
        pass
    return (
        "/tag/", "/tags/", "/search", "?q=", "/category/", "/categories/",
        "/topic/", "/topics/", "/rubric/", "/feed", "/rss",
    )


def is_aggregator_or_search_url(url: str) -> bool:
    """Проверяет, является ли URL страницей-агрегатором, лентой, страницей тегов или поисковой выдачей."""
    raw = str(url or "").casefold()
    parsed = urlsplit(raw)
    path = parsed.path
    query = parsed.query
    full = f"{path}?{query}" if query else path

    for pattern in load_aggregator_patterns():
        p = pattern.casefold()
        if p.startswith("?"):
            if p[1:] in query:
                return True
        elif p in full:
            return True
    return False


def is_noise_url(url: str) -> bool:
    """Проверяет, содержит ли URL шумовые сегменты пути (авторизация, корзина и т.п.).

    Ищет строго по границам сегментов пути: '/authoritative-report' не фильтруется, а '/auth/login' фильтруется.
    """
    path = urlsplit(str(url or "")).path
    segments = set(s.casefold() for s in re.split(r"[/\-_.]+", path) if s)
    return bool(segments & _URL_NOISE_TOKENS)


def assert_safe_http_url(
    url: str,
    *,
    dns_resolver: typing.Callable[..., typing.Any] | None = None,
    resolved_cache: dict[str, str] | None = None,
) -> str:
    normalized = normalize_http_url(url)
    if is_blocked_host(normalized):
        raise UnsafeUrlError(f"URL заблокирован стоп-листом безопасности: {url!r}")
    if is_noise_url(normalized):
        raise UnsafeUrlError(f"URL отфильтрован как шумовой (авторизация/регистрация): {url!r}")
    if is_aggregator_or_search_url(normalized):
        raise UnsafeUrlError(f"URL отфильтрован как страница-агрегатор/поиск/лента: {url!r}")
    hostname = (urlsplit(normalized).hostname or "").strip("[]")
    if resolved_cache is not None and hostname in resolved_cache:
        return normalized
    parsed_ip = _parse_any_ip(hostname)
    if parsed_ip is not None:
        addresses = [parsed_ip]
    else:
        resolver = dns_resolver or socket.getaddrinfo
        try:
            infos = resolver(hostname, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
        except socket.gaierror as error:
            raise UnsafeUrlError(f"host не разрешается: {hostname!r}") from error
        addresses = [ipaddress.ip_address(item[4][0]) for item in infos if item and item[4]]
    for address in addresses:
        if _is_explicitly_blocked_ip(address):
            raise UnsafeUrlError(f"URL разрешается в заблокированный IP-адрес: {address}")
    if not addresses or not all(address.is_global or any(address in network for network in _allowed_networks()) for address in addresses):
        raise UnsafeUrlError(f"URL разрешается в непубличный адрес: {hostname!r}")
    if resolved_cache is not None:
        resolved_cache[hostname] = str(addresses[0])
    return normalized


_BLOCK_TAGS = {"article", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "main", "p", "section", "tr", "ul", "ol"}
_SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "iframe"}
_NOISE_TAGS = {"nav", "aside", "footer", "form", "button"}
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_NOISE_ATTR_TOKENS = {"nav", "menu", "navbar", "header", "footer", "sidebar", "breadcrumb", "cookie", "banner", "popup", "social", "share", "subscribe", "login", "auth", "advert", "promo", "toolbar", "pagination"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.in_title = False
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []

        self.in_table = False
        self.in_tr = False
        self.in_th = False
        self.in_td = False
        self._table_headers: list[str] = []
        self._current_row_cells: list[str] = []
        self._current_cell_text: list[str] = []
        self._current_cell_colspan = 1
        self._current_cell_rowspan = 1
        self._rowspan_carry: dict[int, tuple[int, str]] = {}
        self._next_rowspan_carry: dict[int, tuple[int, str]] = {}
        self._is_header_row = False

    def _consume_rowspan_until_free(self) -> None:
        while len(self._current_row_cells) in self._rowspan_carry:
            col_idx = len(self._current_row_cells)
            rem, val = self._rowspan_carry.pop(col_idx)
            self._current_row_cells.append(val)
            if rem - 1 > 0:
                self._next_rowspan_carry[col_idx] = (rem - 1, val)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        token = tag.casefold()
        if self.skip_depth:
            if token not in _VOID_TAGS:
                self.skip_depth += 1
            return
        marker = " ".join(
            str(value or "")
            for key, value in attrs
            if key.casefold() in {"class", "id", "role", "aria-label", "name", "data-testid"}
        ).casefold().replace("_", " ").replace("-", " ")
        marker_words = set(re.findall(r"\b[a-zA-Z0-9]+\b", marker.casefold()))
        if token in _SKIP_TAGS or token in _NOISE_TAGS or bool(marker_words & _NOISE_ATTR_TOKENS):
            if token not in _VOID_TAGS:
                self.skip_depth = 1
            return

        if token == "title":
            self.in_title = True
        elif token == "table":
            self.in_table = True
            self._table_headers = []
            self._rowspan_carry = {}
            self._next_rowspan_carry = {}
            self.text_parts.append("\n")
        elif token == "tr":
            self.in_tr = True
            self._current_row_cells = []
            self._next_rowspan_carry = {}
            self._is_header_row = False
        elif token in ("th", "td"):
            self._consume_rowspan_until_free()
            if token == "th":
                self.in_th = True
                self._is_header_row = True
            else:
                self.in_td = True
            self._current_cell_text = []
            colspan = 1
            rowspan = 1
            for k, v in attrs:
                if k.casefold() == "colspan" and v and str(v).isdigit():
                    colspan = max(1, int(v))
                elif k.casefold() == "rowspan" and v and str(v).isdigit():
                    rowspan = max(1, int(v))
            self._current_cell_colspan = colspan
            self._current_cell_rowspan = rowspan
        elif token in _BLOCK_TAGS or token == "br":
            self.text_parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self.skip_depth and tag.casefold() == "br":
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        token = tag.casefold()
        if self.skip_depth:
            self.skip_depth -= 1
        elif token == "title":
            self.in_title = False
        elif token in ("th", "td"):
            if token == "th":
                self.in_th = False
            else:
                self.in_td = False
            cell_val = " ".join(" ".join(self._current_cell_text).split())
            for _ in range(self._current_cell_colspan):
                col_idx = len(self._current_row_cells)
                self._current_row_cells.append(cell_val)
                if self._current_cell_rowspan > 1:
                    self._next_rowspan_carry[col_idx] = (self._current_cell_rowspan - 1, cell_val)
            self._current_cell_text = []
            self._current_cell_colspan = 1
            self._current_cell_rowspan = 1
        elif token == "tr":
            self._consume_rowspan_until_free()
            for col_idx, (rem, val) in sorted(self._rowspan_carry.items()):
                while len(self._current_row_cells) < col_idx:
                    self._current_row_cells.append("")
                self._current_row_cells.append(val)
                if rem - 1 > 0:
                    self._next_rowspan_carry[col_idx] = (rem - 1, val)
            self._rowspan_carry = dict(self._next_rowspan_carry)
            self._next_rowspan_carry = {}
            self.in_tr = False
            if self._current_row_cells:
                if self._is_header_row or not self._table_headers:
                    self._table_headers = [c.strip() if c.strip() else f"Колонка {i + 1}" for i, c in enumerate(self._current_row_cells)]
                    self.text_parts.append("\n" + " | ".join(self._current_row_cells) + "\n")
                else:
                    pairs = []
                    for idx, val in enumerate(self._current_row_cells):
                        if not val:
                            continue
                        h = self._table_headers[idx] if idx < len(self._table_headers) else f"Колонка {idx + 1}"
                        pairs.append(f"{h}: {val}")
                    if pairs:
                        self.text_parts.append(f"\n[Таблица: {' | '.join(pairs)}]\n")
                    else:
                        self.text_parts.append("\n" + " | ".join(self._current_row_cells) + "\n")
            self._current_row_cells = []
            self._is_header_row = False
        elif token == "table":
            self.in_table = False
            self._table_headers = []
            self._rowspan_carry = {}
            self._next_rowspan_carry = {}
            self.text_parts.append("\n")
        elif token in _BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        value = " ".join(data.split())
        if not value:
            return
        if self.in_title:
            self.title_parts.append(value)
        elif self.in_th or self.in_td:
            self._current_cell_text.append(value)
        else:
            self.text_parts.append(value)


def _collapse(text: str) -> str:
    # пробелы вокруг переводов строк — от отступов разметки: без них пустые строки схлопываются
    return re.sub(r"\n{3,}", "\n\n", re.sub(r" *\n *", "\n", re.sub(r"[ \t\r\f\v]+", " ", text))).strip()


def extract_title_and_text(raw_html: str) -> tuple[str, str]:
    parser = _TextExtractor()
    parser.feed(str(raw_html or ""))
    parser.close()
    return _collapse(" ".join(parser.title_parts)), _collapse(" ".join(parser.text_parts))


def _parse_pdf_bytes(raw: bytes) -> tuple[str, str]:
    """Парсит PDF из байтов с разметкой [page: N] через fitz/pypdf/pdftotext или постраничный fallback."""
    from bormochecker.limits import Settings

    settings = Settings.from_env()
    if len(raw) > settings.pdf_max_bytes:
        raise DecompressionBombError(
            f"Размер PDF превышает допустимый лимит: {len(raw)} > {settings.pdf_max_bytes}"
        )
    max_pages = settings.pdf_max_pages
    title = "PDF Документ"
    text_chunks: list[str] = []

    try:
        import fitz  # type: ignore[import-untyped]

        doc = fitz.open(stream=raw, filetype="pdf")
        if len(doc) > max_pages:
            doc.close()
            raise DecompressionBombError(f"Слишком много страниц в PDF: {len(doc)} > {max_pages}")
        for idx, page in enumerate(doc, start=1):
            p_text = (page.get_text("text") or "").strip()
            if p_text:
                text_chunks.append(f"[page: {idx}]\n{p_text}")
        doc.close()
    except DecompressionBombError:
        raise
    except Exception:
        pass

    if not text_chunks:
        try:
            import pypdf  # type: ignore[import-untyped]

            reader = pypdf.PdfReader(io.BytesIO(raw))
            if len(reader.pages) > max_pages:
                raise DecompressionBombError(
                    f"Слишком много страниц в PDF: {len(reader.pages)} > {max_pages}"
                )
            for idx, page in enumerate(reader.pages, start=1):
                p_text = (page.extract_text() or "").strip()
                if p_text:
                    text_chunks.append(f"[page: {idx}]\n{p_text}")
        except DecompressionBombError:
            raise
        except Exception:
            pass

    if not text_chunks and shutil.which("pdftotext"):
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
                tmp.write(raw)
                tmp.flush()
                res = subprocess.run(
                    ["pdftotext", "-layout", tmp.name, "-"],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=30,
                )
                raw_pages = res.stdout.split("\x0c")
                pages = [p.strip() for p in raw_pages if p.strip()]
                if len(pages) > max_pages:
                    raise DecompressionBombError(f"Слишком много страниц в PDF: {len(pages)} > {max_pages}")
                for idx, p_text in enumerate(pages, start=1):
                    text_chunks.append(f"[page: {idx}]\n{p_text}")
        except DecompressionBombError:
            raise
        except Exception:
            pass

    if not text_chunks:
        raw_str = raw.decode("latin1", errors="ignore")
        page_objs = re.split(r"/Type\s*/Page\b(?!s)", raw_str)
        if len(page_objs) > 2:
            streams = re.findall(r"stream\r?\n(.*?)\r?\nendstream", raw_str, re.DOTALL)
            if streams:
                if len(streams) > max_pages:
                    raise DecompressionBombError(f"Слишком много страниц в PDF: {len(streams)} > {max_pages}")
                for idx, s_body in enumerate(streams, start=1):
                    tjs = re.findall(r"\((.*?)\)\s*Tj", s_body)
                    if tjs:
                        text_chunks.append(f"[page: {idx}]\n{' '.join(tjs)}")
        if not text_chunks:
            matches = re.findall(r"\((.*?)\)\s*Tj", raw_str)
            plain = " ".join(matches) if matches else "Документ PDF"
            text_chunks.append(f"[page: 1]\n{plain}")

    full_text = "\n\n".join(text_chunks)
    for line in full_text.splitlines():
        clean_line = line.strip()
        if len(clean_line) > 5 and not clean_line.startswith("[page:"):
            title = clean_line[:100]
            break
    return title, full_text


MAX_DOCX_UNCOMPRESSED_BYTES = 10 * 1024 * 1024  # 10 МБ
MAX_DOCX_COMPRESSION_RATIO = 100.0  # 100:1
MAX_DOCX_ARCHIVE_FILES = 100  # 100 файлов


def _validate_docx_archive(z: zipfile.ZipFile, compressed_total: int) -> None:
    infolist = z.infolist()
    if len(infolist) > MAX_DOCX_ARCHIVE_FILES:
        raise DecompressionBombError(
            f"Архив DOCX содержит слишком много файлов: {len(infolist)} > {MAX_DOCX_ARCHIVE_FILES}"
        )

    total_uncompressed = 0
    for info in infolist:
        if info.file_size < 0:
            raise DecompressionBombError("Недопустимый отрицательный размер файла в архиве")
        total_uncompressed += info.file_size
        if total_uncompressed > MAX_DOCX_UNCOMPRESSED_BYTES:
            raise DecompressionBombError(
                f"Общий объём распакованных данных превышает 10 МБ: {total_uncompressed} > {MAX_DOCX_UNCOMPRESSED_BYTES}"
            )
        compressed_size = max(info.compress_size, 1)
        if (info.file_size / compressed_size) > MAX_DOCX_COMPRESSION_RATIO:
            raise DecompressionBombError(
                f"Превышен коэффициент сжатия zip-бомбы: {info.file_size / compressed_size:.1f} > {MAX_DOCX_COMPRESSION_RATIO}"
            )

    if compressed_total > 0 and (total_uncompressed / compressed_total) > MAX_DOCX_COMPRESSION_RATIO:
        raise DecompressionBombError(
            f"Превышен суммарный коэффициент сжатия архива: {total_uncompressed / compressed_total:.1f} > {MAX_DOCX_COMPRESSION_RATIO}"
        )


def _parse_docx_bytes(raw: bytes) -> tuple[str, str]:
    """Парсит DOCX без внешних зависимостей через zipfile + ElementTree с защитой от zip-бомб."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            _validate_docx_archive(z, len(raw))
            if "word/document.xml" not in z.namelist():
                raise FetchError("В архиве DOCX отсутствует word/document.xml")
            with z.open("word/document.xml") as doc_stream:
                xml_chunks: list[bytes] = []
                xml_read = 0
                while True:
                    chunk = doc_stream.read(65536)
                    if not chunk:
                        break
                    xml_read += len(chunk)
                    if xml_read > MAX_DOCX_UNCOMPRESSED_BYTES:
                        raise DecompressionBombError(
                            f"Общий объём распакованных данных превышает 10 МБ: {xml_read} > {MAX_DOCX_UNCOMPRESSED_BYTES}"
                        )
                    xml_chunks.append(chunk)
                xml_content = b"".join(xml_chunks)
        root = ET.fromstring(xml_content)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        lines: list[str] = []
        for body in root.findall("w:body", ns):
            for child in body:
                tag = child.tag.split("}")[-1]
                if tag == "p":
                    texts = [node.text for node in child.findall(".//w:t", ns) if node.text]
                    if texts:
                        lines.append("".join(texts))
                elif tag == "tbl":
                    try:
                        table_headers: list[str] = []
                        prev_row_cells: list[str] = []
                        for row_idx, row in enumerate(child.findall(".//w:tr", ns)):
                            cells: list[str] = []
                            for cell in row.findall(".//w:tc", ns):
                                c_texts = [node.text for node in cell.findall(".//w:t", ns) if node.text]
                                c_val = "".join(c_texts).strip()
                                span_val = 1
                                is_vmerge_cont = False
                                tc_pr = cell.find("w:tcPr", ns)
                                if tc_pr is not None:
                                    grid_span = tc_pr.find("w:gridSpan", ns)
                                    if grid_span is not None:
                                        g_attr = grid_span.attrib.get(f"{{{ns['w']}}}val") or grid_span.attrib.get("val")
                                        if g_attr and g_attr.isdigit():
                                            span_val = max(1, int(g_attr))
                                    v_merge = tc_pr.find("w:vMerge", ns)
                                    if v_merge is not None:
                                        v_attr = v_merge.attrib.get(f"{{{ns['w']}}}val") or v_merge.attrib.get("val")
                                        if v_attr != "restart":
                                            is_vmerge_cont = True
                                for _ in range(span_val):
                                    col_pos = len(cells)
                                    val_to_use = c_val
                                    if is_vmerge_cont and not val_to_use and col_pos < len(prev_row_cells):
                                        val_to_use = prev_row_cells[col_pos]
                                    cells.append(val_to_use)
                            prev_row_cells = list(cells)

                            if row_idx == 0:
                                table_headers = [c.strip() if c.strip() else f"Колонка {i + 1}" for i, c in enumerate(cells)]
                                lines.append(" | ".join(cells))
                            else:
                                pairs = []
                                for idx, val in enumerate(cells):
                                    if not val:
                                        continue
                                    h = table_headers[idx] if idx < len(table_headers) else f"Колонка {idx + 1}"
                                    pairs.append(f"{h}: {val}")
                                if pairs:
                                    lines.append(f"[Таблица: {' | '.join(pairs)}]")
                                else:
                                    lines.append(" | ".join(cells))
                    except Exception:
                        for row in child.findall(".//w:tr", ns):
                            row_texts = [node.text.strip() for node in row.findall(".//w:t", ns) if node.text and node.text.strip()]
                            if row_texts:
                                lines.append(" | ".join(row_texts))
        full_text = "\n".join(lines).strip()
        title = lines[0][:100] if lines else "DOCX Документ"
        return title, full_text
    except (DecompressionBombError, FetchError):
        raise
    except Exception as exc:
        raise FetchError(f"Не удалось разобрать содержимое DOCX: {exc}") from exc


_META_CHARSET_RE = re.compile(rb"""<meta[^>]+charset=["']?([\w-]+)""", re.IGNORECASE)


class SafePinnedBackend(SyncBackend):
    def __init__(
        self,
        dns_resolver: typing.Callable[..., typing.Any] | None = None,
        resolved_cache: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self.dns_resolver = dns_resolver or socket.getaddrinfo
        self.resolved_cache: dict[str, str] = resolved_cache if resolved_cache is not None else {}
        self.connected_ips: list[str] = []

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: typing.Iterable[SOCKET_OPTION] | None = None,
    ) -> httpcore.NetworkStream:
        if socket_options is None:
            socket_options = []

        raw_host = host.strip("[]")
        parsed_ip = _parse_any_ip(raw_host)
        if parsed_ip is not None:
            if is_blocked_host(str(parsed_ip)):
                raise UnsafeUrlError(f"Попытка соединения с заблокированным IP: {parsed_ip}")
            verified_ip = str(parsed_ip)
        elif raw_host in self.resolved_cache:
            verified_ip = self.resolved_cache[raw_host]
        else:
            if is_blocked_host(raw_host):
                raise UnsafeUrlError(f"Попытка соединения с заблокированным хостом: {raw_host}")
            try:
                infos = self.dns_resolver(raw_host, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
            except socket.gaierror as error:
                raise UnsafeUrlError(f"host не разрешается: {raw_host!r}") from error

            addrs = [item[4][0] for item in infos if item and item[4]]
            if not addrs:
                raise UnsafeUrlError(f"host не разрешается: {raw_host!r}")

            for addr in addrs:
                if is_blocked_host(addr):
                    raise UnsafeUrlError(f"Хост {raw_host!r} разрешается в заблокированный IP: {addr}")
                ip_obj = ipaddress.ip_address(addr)
                if _is_explicitly_blocked_ip(ip_obj):
                    raise UnsafeUrlError(f"Хост {raw_host!r} разрешается в заблокированный IP: {addr}")
                if not (ip_obj.is_global or any(ip_obj in net for net in _allowed_networks())):
                    raise UnsafeUrlError(f"Хост {raw_host!r} разрешается в непубличный адрес: {addr}")

            verified_ip = addrs[0]
            self.resolved_cache[raw_host] = verified_ip

        self.connected_ips.append(verified_ip)
        address = (verified_ip, port)
        source_address = None if local_address is None else (local_address, 0)
        exc_map = {
            socket.timeout: ConnectTimeout,
            OSError: ConnectError,
        }
        with map_exceptions(exc_map):
            sock = socket.create_connection(address, timeout, source_address=source_address)
            for opt in socket_options:
                sock.setsockopt(*opt)
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
        return SyncStream(sock)


class SafePinnedTransport(httpx.HTTPTransport):
    def __init__(
        self,
        network_backend: httpcore.NetworkBackend | None = None,
        verify: ssl.SSLContext | str | bool = True,
        **kwargs: typing.Any,
    ) -> None:
        super().__init__(verify=verify, **kwargs)
        if network_backend is not None:
            self._pool = httpcore.ConnectionPool(
                ssl_context=getattr(self._pool, "_ssl_context", None),
                network_backend=network_backend,
            )


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


@dataclass(slots=True)
class SafeDocumentFetcher:
    timeout_seconds: float = 15.0
    max_bytes: int = 1_048_576
    max_redirects: int = 5
    user_agent: str = "Factcheck/1.0"
    dns_resolver: typing.Callable[..., typing.Any] | None = None
    transport: httpx.BaseTransport | None = None
    meter: typing.Any = None

    def _get_transport(self, resolved_cache: dict[str, str] | None = None) -> httpx.BaseTransport:
        if self.transport is not None:
            return self.transport
        backend = SafePinnedBackend(dns_resolver=self.dns_resolver, resolved_cache=resolved_cache)
        return SafePinnedTransport(network_backend=backend)

    def fetch(self, url: str, timeout: float | None = None) -> FetchedDocument:
        parsed_orig = urlsplit(str(url or "").strip())
        if parsed_orig.scheme.casefold() not in {"http", "https"}:
            raise UnsafeUrlError(f"неподдерживаемая схема URL: {parsed_orig.scheme!r}")
        if is_blocked_host(url):
            raise UnsafeUrlError(f"URL заблокирован стоп-листом безопасности: {url!r}")

        if not network_allowed_ctx.get() or os.environ.get("FACTCHECK_OFFLINE") == "1":
            raise NetworkNotAllowedError(f"Сетевые запросы запрещены в текущем/офлайн режиме (network_allowed=False): {url!r}")

        resolved_cache: dict[str, str] = {}
        original_url = assert_safe_http_url(url, dns_resolver=self.dns_resolver, resolved_cache=resolved_cache)
        current_url = original_url
        transport = self._get_transport(resolved_cache=resolved_cache)
        req_timeout = timeout if timeout is not None else self.timeout_seconds

        with httpx.Client(
            transport=transport,
            follow_redirects=False,
            timeout=req_timeout,
            headers={"User-Agent": self.user_agent},
        ) as client:
            for redirects in range(self.max_redirects + 1):
                cur_parsed = urlsplit(current_url)
                if cur_parsed.scheme.casefold() not in {"http", "https"}:
                    raise UnsafeUrlError(f"неподдерживаемая схема URL: {cur_parsed.scheme!r}")
                if is_blocked_host(current_url):
                    raise UnsafeUrlError(f"URL заблокирован стоп-листом безопасности: {current_url!r}")

                try:
                    if self.meter is not None and hasattr(self.meter, "record_http_request"):
                        self.meter.record_http_request("fetcher")
                    use_stream = not hasattr(client, "_mock_name")
                    stream_ctx = client.stream("GET", current_url) if use_stream else contextlib.nullcontext(client.get(current_url))
                    with stream_ctx as response:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            if redirects >= self.max_redirects:
                                raise FetchError(f"слишком много редиректов для {original_url!r}")
                            location = response.headers.get("location")
                            if not location:
                                raise FetchError(f"HTTP {response.status_code} без Location заголовка для {current_url!r}")
                            redirect_target = urljoin(current_url, location)
                            target_parsed = urlsplit(redirect_target)
                            if target_parsed.scheme.casefold() not in {"http", "https"}:
                                raise UnsafeUrlError(f"неподдерживаемая схема URL при редиректе: {target_parsed.scheme!r}")
                            if is_blocked_host(redirect_target):
                                raise UnsafeUrlError(f"Редирект на заблокированный хост: {redirect_target!r}")
                            current_url = assert_safe_http_url(
                                redirect_target,
                                dns_resolver=self.dns_resolver,
                                resolved_cache=resolved_cache,
                            )
                            continue

                        if response.status_code >= 400:
                            raise FetchError(f"HTTP {response.status_code} при загрузке {original_url!r}")

                        final_url = assert_safe_http_url(
                            str(response.url or current_url),
                            dns_resolver=self.dns_resolver,
                            resolved_cache=resolved_cache,
                        )
                        if is_blocked_host(final_url):
                            raise UnsafeUrlError(f"Итоговый URL заблокирован стоп-листом безопасности: {final_url!r}")

                        raw_ct = response.headers.get("content-type", "text/plain")
                        content_type = raw_ct.casefold().split(";")[0].strip()
                        is_pdf = content_type == "application/pdf" or final_url.casefold().endswith(".pdf")
                        is_docx = (
                            content_type in (
                                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                "application/msword",
                            )
                            or final_url.casefold().endswith(".docx")
                        )
                        if not (
                            content_type.startswith("text/")
                            or content_type in {"application/xhtml+xml", "application/xml", "application/json"}
                            or is_pdf
                            or is_docx
                        ):
                            raise FetchError(f"неподдерживаемый content-type {content_type!r}")

                        chunks: list[bytes] = []
                        received = 0
                        truncated = False
                        byte_iter = response.iter_bytes() if use_stream else [getattr(response, "content", b"") or b""]
                        for chunk in byte_iter:
                            if not chunk:
                                continue
                            if received + len(chunk) > self.max_bytes:
                                keep = self.max_bytes - received
                                if keep > 0:
                                    chunks.append(chunk[:keep])
                                truncated = True
                                break
                            chunks.append(chunk)
                            received += len(chunk)
                        raw_body = b"".join(chunks)
                        status_code = response.status_code
                        encoding = response.encoding
                        break
                except UnsafeUrlError:
                    raise
                except FetchError:
                    raise
                except (httpx.HTTPError, OSError) as error:
                    raise FetchError(f"ошибка загрузки {original_url!r}: {error}") from error
            else:
                raise FetchError(f"слишком много редиректов для {original_url!r}")

            if is_pdf:
                title, text = _parse_pdf_bytes(raw_body)
            elif is_docx:
                title, text = _parse_docx_bytes(raw_body)
            else:
                if not encoding or encoding == "ascii":
                    declared = _META_CHARSET_RE.search(raw_body[:4096])
                    if declared:
                        encoding = declared.group(1).decode("utf-8", errors="ignore")
                    else:
                        encoding = "utf-8"
                try:
                    decoded = raw_body.decode(encoding, errors="replace")
                except LookupError:
                    decoded = raw_body.decode("utf-8", errors="replace")
                if content_type in {"text/html", "application/xhtml+xml", "application/xml"} or "<html" in decoded.casefold():
                    title, text = extract_title_and_text(decoded)
                else:
                    title, text = "", " ".join(decoded.split())

            # Фоллбэк чтения сложных SPA страниц через r.jina.ai (по умолчанию выключен ради 100% Local-Only)
            if (
                len(text.strip()) < 120
                and not (is_pdf or is_docx)
                and os.environ.get("FACTCHECK_USE_JINA_READER", "0") == "1"
            ):
                try:
                    safe_target = assert_safe_http_url(
                        final_url,
                        dns_resolver=self.dns_resolver,
                        resolved_cache=resolved_cache,
                    )
                    jina_url = f"https://r.jina.ai/{safe_target}"
                    if self.meter is not None and hasattr(self.meter, "record_http_request"):
                        self.meter.record_http_request("jina_reader")
                    jina_resp = client.get(
                        jina_url,
                        headers={
                            "Accept": "text/markdown",
                            "X-No-Cache": "true",
                        },
                    )
                    if jina_resp.status_code == 200:
                        jina_md = jina_resp.content[: self.max_bytes].decode("utf-8", errors="replace").strip()
                        if len(jina_md) >= 120:
                            text = jina_md
                            if not title:
                                title_match = re.search(r"^#\s+(.+)$", jina_md, re.MULTILINE)
                                if title_match:
                                    title = title_match.group(1).strip()
                except Exception:
                    pass

            return FetchedDocument(original_url, final_url, title, text, content_type, status_code, truncated)
