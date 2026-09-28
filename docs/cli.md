# CLI reference

After `pip install -e ".[dev]"`, the `annas-api` command is available:

```bash
annas-api --help
```

The CLI is for local maintenance, debugging, and one-off operations; server
workloads should go through the HTTP API.

| Command | Description |
| --- | --- |
| `annas-api doctor` | Check dependencies, browser, proxy, DjVu tooling, and mirror connectivity. |
| `annas-api search <query>` | Search and list candidate records. |
| `annas-api probe --md5 <md5>` | Resolve the download address and read server-reported metadata. |
| `annas-api download --md5 <md5>` | Download, verify, and deliver a file. |
| `annas-api download --direct-url <url>` | Download a known public HTTPS file. |

## JSON output

`search` and `probe` support `--json`. stdout carries a single JSON value; progress
and diagnostics go to stderr, so scripts can parse it safely.

```bash
annas-api search 'example title' --ext epub --limit 5 --json
annas-api probe --md5 0123456789abcdef0123456789abcdef --json
```

## Download semantics

- Incomplete content is kept in a `.part` file; never treat it as final.
- Resume happens only when the server supports byte ranges and the resource identity is unchanged.
- With an MD5 the content hash is verified; PDFs are also checked for readability.
- A direct URL without an MD5 must report a trustworthy content length (weaker than a hash check).
- When `ddjvu` is available, DjVu is converted to PDF; on failure the original file is kept.
