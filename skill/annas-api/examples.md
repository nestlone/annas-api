# annas-api examples

All examples target the default deployment, `https://annas.nestlone.com`.
Override the host with `ANNAS_API_BASE_URL` (or `--base-url`) — for example
while the service is still being validated on a staging IP:

```bash
export ANNAS_API_TOKEN=...                 # required
export ANNAS_API_BASE_URL=https://annas.nestlone.com
# staging: export ANNAS_API_BASE_URL=http://203.0.113.10:8000
```

## 1. Verify connectivity

```bash
python scripts/annas_cli.py health
# {"status": "ok"}
```

```bash
curl -s https://annas.nestlone.com/healthz
```

## 2. Search and read the results

```bash
python scripts/annas_cli.py search "The Great Gatsby" --ext epub --limit 5 --wait
```

```json
{
  "id": "5c1f0a9e-...",
  "kind": "search",
  "status": "completed",
  "result": [
    {"md5": "bcdcd8bd16771a4f03c71b89a490dd53", "title": "The Great Gatsby", "format": "epub"}
  ],
  "error": null
}
```

A search can be submitted and polled separately, which suits agents that need
to do other work in between:

```bash
python scripts/annas_cli.py search "The Great Gatsby" --ext epub   # → id
python scripts/annas_cli.py job <job-id>                           # → status
```

```python
from annas_client import AnnasClient

client = AnnasClient()
submitted = client.search("The Great Gatsby", ext="epub", limit=5)
job = client.wait(submitted["id"], timeout=120, on_poll=lambda j: print(j["status"]))
if job["status"] != "completed":
    raise SystemExit("search {}: {}".format(job["status"], job["error"]))
first = job["result"][0]
print(first["title"], first["md5"])
```

Raw HTTP:

```bash
curl -s -X POST https://annas.nestlone.com/v1/search \
  -H "X-API-Key: $ANNAS_API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"query":"The Great Gatsby","ext":"epub","limit":5}'
```

## 3. Download by MD5 and save the file

```bash
python scripts/annas_cli.py download --md5 bcdcd8bd16771a4f03c71b89a490dd53 \
  --wait --save ./books
```

`--save` takes a directory (the server filename is used) or an explicit file
path. The saved path is echoed back in the job JSON as `saved_to`.

```python
job = client.run_download(md5="bcdcd8bd16771a4f03c71b89a490dd53", timeout=1800)
if job["status"] != "completed":
    raise SystemExit("download {}: {}".format(job["status"], job["error"]))
path = client.save(job["download_url"], "./books")
print("wrote", path)
```

The one-liner equivalent:

```python
path, book = client.run_search_to_file("The Great Gatsby", "./books", ext="epub")
```

## 4. Download from a direct URL

Use this when you already hold a permitted public HTTPS link rather than an MD5.
Credentials in the URL are rejected.

```bash
python scripts/annas_cli.py download \
  --url https://example.org/public/paper.pdf --name paper.pdf --wait --save ./papers
```

## 5. Inspect the queue and cancel work

```bash
python scripts/annas_cli.py jobs --status queued          # what is waiting
python scripts/annas_cli.py jobs --limit 20               # newest 20, any state
python scripts/annas_cli.py jobs --status completed --limit 50 --offset 50
python scripts/annas_cli.py cancel <job-id>
```

```python
page = client.list_jobs(status="queued", limit=50)
for item in page["jobs"]:
    print(item["id"], item["kind"], item["status"])
```

Cancelling a job that already started or finished is a normal outcome, not a
failure — report the `409` and move on.

```python
from annas_client import AnnasApiError

try:
    client.cancel(job_id)
except AnnasApiError as exc:
    if exc.status == 409:
        print("already running or finished:", exc)
    else:
        raise
```

## 6. Handle errors by status

| Status | What it means | What to do |
| --- | --- | --- |
| `401` | Bad or missing API key. | Fix `ANNAS_API_TOKEN`; do not retry. |
| `403` | Signed link expired. | Re-fetch the job for a fresh `download_url`. |
| `404` | Job or file gone. | Report it; do not retry. |
| `409` | Cancel raced with the worker. | Report the actual state. |
| `422` | Invalid input. | Correct the request; do not retry unchanged. |
| `5xx` | Server fault. | Retry once with backoff, then report. |

```python
try:
    job = client.run_search("…")
except AnnasApiError as exc:
    print("status", exc.status, "detail", exc)
```

## 7. Deliver a validated file to the user

The complete path a user usually wants — find the best hit, download it, and
hand back a local path:

```python
from annas_client import AnnasClient, AnnasApiError

client = AnnasClient()

search = client.run_search("The Great Gatsby", ext="epub", limit=5, timeout=120)
if search["status"] != "completed":
    raise SystemExit("search failed: {}".format(search["error"]))
if not search["result"]:
    raise SystemExit("no matches")

book = search["result"][0]
download = client.run_download(md5=book["md5"], timeout=1800)
if download["status"] != "completed":
    raise SystemExit("download failed: {}".format(download["error"]))

path = client.save(download["download_url"], "./books")
print("{} -> {}".format(book["title"], path))
```

## 8. Update the skill

```bash
python scripts/check_update.py               # JSON: current vs. latest
python scripts/check_update.py --exit-code   # exit 3 when an update exists
```

```json
{
  "current": "1.0.1",
  "latest": "1.2.0",
  "update_available": true,
  "source": "version",
  "tag": "skill-v1.2.0",
  "zip_url": "https://github.com/nestlone/annas-api/releases/download/skill-v1.2.0/…zip",
  "sha256_url": "https://github.com/nestlone/annas-api/releases/download/skill-v1.2.0/…zip.sha256"
}
```

Other sources, when you want them:

```bash
python scripts/check_update.py --source api --token "$GITHUB_TOKEN"   # Releases API
python scripts/check_update.py --manifest-url https://annas.nestlone.com/skill/latest.json
```

Exit codes: `0` the check ran, `3` an update is available (with `--exit-code`),
`4` the check itself failed — for example an exhausted API rate limit, which the
default source avoids.
