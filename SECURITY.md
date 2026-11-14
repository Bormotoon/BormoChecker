# Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| `1.0.x` (`main`) | Yes |

## Built-in Security Controls

BormoChecker fetches and parses untrusted external URLs and uploaded documents. To mitigate risk:

- **SSRF & DNS Rebinding Defense (`bormochecker/retrieval.py`)**: `SafeDocumentFetcher` and `SafePinnedBackend` validate all IPv4/IPv6 forms (including hex, octal, DWORD, and IPv6-mapped addresses), block private/loopback/link-local CIDRs, pin resolved sockets, and re-verify every redirect (`max_redirects = 5`).
- **Bounded Downloads & Zip-Bomb Guards**: Responses are capped at `1 MB` (`1_048_576` bytes) with `15.0 s` timeouts; DOCX archives enforce `MAX_DOCX_UNCOMPRESSED_BYTES = 10 MB`, `MAX_DOCX_COMPRESSION_RATIO = 100.0`, and `MAX_DOCX_ARCHIVE_FILES = 100`.

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities (especially SSRF bypasses, parser crashes, or sandbox escapes).

Instead, report vulnerabilities privately via GitHub Security Advisories on `Bormotoon/BormoChecker` or contact the maintainer directly via [GitHub (@Bormotoon)](https://github.com/Bormotoon).
