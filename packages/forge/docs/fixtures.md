# HTTP fixtures: record and replay

Backend tests gate every merge without a network: real HTTP exchanges
are recorded once into a cassette file, secrets scrubbed, and replayed
from the file forever after. A replayed run proves the code makes the
requests the recording made, byte for byte, and CI needs no
credentials and no server.

The layer lives in `livery.forge.testing`: `Cassette`,
`RecordingOpener`, `ReplayOpener`, and the `UrlOpener` seam they all
satisfy. Any object with `open(request, *, timeout)` fits the seam;
urllib's own opener does, so code that takes its opener as a
constructor argument cannot tell recording, replay, and the real
network apart.

## The file format

Format 1. One JSON object per file:

```json
{
  "format": 1,
  "exchanges": [
    {
      "method": "GET",
      "url": "http://localhost:3000/api/v1/user?token=REDACTED",
      "request_body": "",
      "status": 200,
      "reason": "OK",
      "content_type": "application/json",
      "response_body": "{\"login\": \"someone\"}"
    }
  ]
}
```

- `exchanges` is ordered: the order requests happened is the order
  they must happen again.
- `request_body` and `response_body` are text. Only bytes request
  bodies can be recorded; a streaming body has no stable
  representation to match on replay and is refused at record time.
- Responses with status 300 and above are exchanges like any other:
  recorded refusals, refused redirects included, replay as the same
  `HTTPError`. A refused redirect's exchange carries the scrubbed
  `Location`, so a caller that deliberately follows one (a signed log
  URL) can do so from the replayed refusal too.
- Recording refuses redirects exactly as the live clients do: the
  default inner opener never forwards the Authorization header to a
  new location, and a recording session holds real credentials.
- A request whose body is nondeterministic by construction (a
  sealed-box encrypted secret carries an ephemeral key) is recorded
  with the `VOLATILE` marker as its body and matched by method and
  URL alone on replay; the recorder is told which URLs those are
  (`volatile_bodies`).
- A file whose `format` is not 1 is refused with the instruction to
  re-record.

## Scrubbing

Secrets never reach disk:

- Request headers are not stored at all. Tokens travel in headers, so
  the whole class is out by construction.
- The caller names its secrets at record time
  (`RecordingOpener(cassette, secrets=(token,))`), and every
  occurrence in the URL, the request body, and the response body is
  replaced with `REDACTED` before the exchange is stored.
- Replay applies the same scrubbing to incoming requests
  (`ReplayOpener(cassette, secrets=(dummy,))`), so a dummy credential
  in CI matches a recording made with a real one.
- Secrets the server mints inside its answers (a GitLab project's
  `runners_token`) cannot appear on any caller's secret list, so the
  recorder also takes `scrub_fields`: JSON field names whose string
  values are redacted in every stored response body.

## Matching

Replay is strict and in order. Each request must equal the next
recorded exchange on method, URL, and body, after scrubbing. A
mismatch raises `CassetteError` naming both sides verbatim: a drifted
request is the finding, not a nuisance. When the cassette runs out and
another request arrives, the error says which request was never
recorded; `verify_exhausted()` reports the quiet direction, a recorded
exchange the replayed run stopped making.

## Re-recording

A cassette is re-recorded, never edited. `fm forge.fixtures.record`
runs the conformance suites against the live targets (the local forge
containers, or the scratch repository for GitHub) and rewrites the
files. Review the diff like code: a changed exchange is a changed
contract with the server.
