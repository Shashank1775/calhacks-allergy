# 0013: Cartesia Ink batch transcription and an Apify crawler for menu import

- **Status:** proposed
- **Date:** 2026-10-23
- **Deciders:** director, mgr-orders (researched by worker/orders/orders-04)

## Context

Issue #12 (backlog key orders-04) asks how two vendors would be called, so that `kitchen/orders/transcribe.py` (Cartesia Ink, M3) and `kitchen/profiling/menu_import.py` (Apify, optional, M4) can be built later without guesswork. The issue named `kitchen/orders/docs/vendor-spike.md` as the output. This run's dispatch put the write-up here as an ADR instead, and the two team READMEs link to it.

Three contracts are fixed and this ADR does not change them:

- `Transcriber.transcribe(audio: bytes, *, sample_rate_hz: int) -> str` ([apis.md](../../contracts/apis.md#transcriber-and-speaker)).
- `POST /v1/transcribe` takes `audio/wav` and returns `{"text": str}`.
- `POST /v1/menu/import` takes `{"url": str}` and returns `{"items": int}` ([Orders HTTP API](../../contracts/apis.md#orders-http-api)).

**How the facts were checked.** No vendor account was made and no paid API was called. Every fact comes from public pages read on 2026-10-23 and is tagged with a source from the list at the end (`[C1]` for Cartesia, `[A1]` for Apify). Numbers marked *computed* are arithmetic on cited numbers. Anything marked **unverified** is a best guess that the build task must confirm. Two Cartesia reference pages (the OpenAPI spec and the OpenAI migration guide) redirect to a login page, so the exact wire format of the batch request is only partly confirmed.

### Findings: Cartesia Ink

| Topic | Finding | Source |
|---|---|---|
| Models | `ink-2`: streaming, English, French, Hindi, Japanese, Spanish, released 2026-09-17. `ink-whisper`: the cheapest Ink model, about 100 languages, stable snapshot `ink-whisper-2025-06-04` | [C3], [C4] |
| Endpoints | Batch `/stt` (whole recording in one request, `ink-whisper` only). Realtime manual finalize `/stt/websocket` (all models). Realtime auto finalize `/stt/turns/websocket` (`ink-2` only) | [C1] |
| Batch vs streaming | Realtime endpoints take audio at real-time pace (one second of audio per second). Batch takes the whole file at once and returns one complete transcript | [C1] |
| Auth | Server side: an API key in `Authorization: Bearer <key>` or `X-Api-Key`. Every request sends `Cartesia-Version: <date you tested>`; versions `2026-03-01` and later return structured JSON errors. Browsers must never hold the key; they use short-lived access tokens with an `stt` grant | [C6], [C7] |
| Batch request | `POST https://api.cartesia.ai/stt`, multipart form with `file`, `model=ink-whisper`, `language=en`, optional word timestamps. The response has `text` (and `words` with timestamps). Field names come from the SDK example and a third-party migration guide; **unverified** against Cartesia's own API reference (login-gated) | [C5], [C15] |
| Audio formats | Batch accepts container files such as WAV and MP3; send `encoding` and `sample_rate` only for raw audio. Realtime takes raw audio only, for example `pcm_s16le` at 8–48 kHz (16 kHz recommended for voice platforms). The server may not reject mismatched settings, so wrong values give a bad transcript, not an error | [C2] |
| Latency | No figure is published for batch: **unverified**, measure it. Cartesia's launch page claims "100ms transcript latency" for streaming with turn detection, not for batch | [C10] |
| Cost | Batch `ink-whisper`: 1 credit per 2 s of audio. Realtime `ink-whisper`: 1 credit per second. Realtime `ink-2`: 3 credits per second. Silence is billed | [C8] |
| Plans | Free: 20K credits a month, 8 concurrent STT requests. Pro: $5 for 100K. Startup: $49 for 1.25M. Overage only on paid plans; without it, requests are blocked when credits run out | [C9] |
| Cost per note | A 10 s note costs 5 credits on batch, so the Free plan covers about 4,000 such notes a month. On `ink-2` realtime it costs 30 credits (about 660 notes) | *computed* from [C8], [C9] |
| Accuracy aid | Keyterm prompting (up to 100 terms, 1,200 characters) biases the model toward words such as "sesame". It exists only on the WebSocket endpoints | [C11] |
| Offline | Ink is a cloud API. The docs index lists no on-device or self-hosted Ink, only enterprise regional endpoints. That absence is **unverified** | [C14] |
| Data retention | Zero Data Retention needs an enterprise plan. The default retention period for STT audio is not stated: **unverified** | [C13] |
| Errors | 429 `concurrency_limited`, `quota_exceeded`, `plan_upgrade_required` | [C7] |

### Findings: Apify

| Topic | Finding | Source |
|---|---|---|
| Candidate Actors | **Website Content Crawler** (`apify/website-content-crawler`, by Apify): 171K users, 4.62/5, 99.8% of runs succeed. **RAG Web Browser** (`apify/rag-web-browser`, by Apify): one URL in, Markdown out. Community menu scrapers: MenuMiner (9 users, no ratings), signal_lab Restaurant Menu Extractor (2 users), ezzatox Restaurant Menu Price Extractor (1 user) | [A1], [A6], [A7], [A8], [A9] |
| Menu-scraper limits | MenuMiner skips JavaScript-rendered menus, PDF and image menus, and bot-protected sites. signal_lab does no OCR. MenuMiner's price reads $25 per 1,000 results on its page but "$2.20 / 1,000" in a search snippet, so its price is **unverified** | [A7], [A8] |
| Run API | `POST https://api.apify.com/v2/actors/{actorId}/run-sync-get-dataset-items`, where `actorId` can be `apify~website-content-crawler`. The JSON body is the Actor's input. Query params include `timeout` (s), `memory` (MB), `maxItems` and `maxTotalChargeUsd`. Success is `201` with a JSON array of dataset items. A run longer than 300 s returns `408`. Errors are `{"error": {"type", "message"}}` with 400, 401, 402 (out of credit) and 403 | [A3] |
| Auth | `Authorization: Bearer <token>` | [A4] |
| Crawler input | `startUrls` (required), `crawlerType` (default `playwright:adaptive`, which switches between plain HTTP and a headless browser), `maxCrawlDepth` (0 means only the start URLs), `maxCrawlPages`, `saveMarkdown` (default true), `respectRobotsTxtFile` (default true), `dynamicContentWaitSecs` (default 10) | [A1], [A2] |
| Crawler output | One dataset item per page: `url`, `metadata.title`, `text`, `markdown` | [A1] |
| PDF menus | The crawler can download linked PDFs but the docs don't say it extracts their text: **unverified**, so treat PDF menus as unsupported | [A2] |
| Cost | The crawler is free; you pay for compute. About $0.5–$5 per 1,000 pages with a browser, $0.2 per 1,000 with plain HTTP. One compute unit (CU) is 1 GB of RAM for one hour. Free plan: $5 of usage a month, $0.2 per CU, 5 concurrent runs. (The crawler's page says $0.25 per CU, so the two pages disagree) | [A1], [A5] |
| Run time | No figure for this crawler: **unverified**, measure it. For comparison, RAG Web Browser averages 16–31 s per search query | [A6] |

## Decision

### Speech-to-text: Cartesia Ink batch with `ink-whisper`

1. **`CartesiaInkTranscriber` calls the batch endpoint** `POST https://api.cartesia.ai/stt` with `model=ink-whisper`, `language=en` and the recorded clip as `file`. The tablet posts one finished WAV to `/v1/transcribe`, and the server reads the transcript before it is parsed, so live partial transcripts add nothing. Batch takes WAV as is [C2], returns the whole text in one reply [C1], and costs a sixth of `ink-2` realtime [C8]. Realtime would also make us replay a finished clip at real-time pace [C1].
2. **The key stays on the orders server.** The adapter reads `CARTESIA_API_KEY` (already in `.env.example`) and sends it as `Authorization: Bearer` [C6]. The tablet never sees a key or a token, because it calls our API, not Cartesia. A missing key fails at startup with a clear message (AGENTS.md §6, data and secrets).
3. **Pin `Cartesia-Version: 2026-03-01`**, the first version with structured errors [C7]. Bump it only after a test with a real key.
4. **Fail toward typing.** Any network error, timeout, non-2xx reply, malformed reply or empty transcript raises `TranscriberUnavailable` inside `transcribe.py`. `/v1/transcribe` then returns an error body per the HTTP conventions, and the tablet treats any error as "type the note instead". An empty transcript is an error, never `{"text": ""}`. The request timeout starts at 10 s (**unverified** sizing; the M3 Ink task measures it with a real key).
5. **Offline means typing.** Ink has no documented offline mode [C14], so the M4 offline path is the manual-entry fallback already planned in `kitchen/orders/README.md`. Nothing waits on Cartesia.
6. **Not now: `ink-2` over `/stt/websocket` with keyterm prompting.** It promises better accuracy [C3] and could bias toward allergen words [C11], but it costs 6 times as much [C8] and needs raw PCM streamed at real-time pace [C2], [C12]. Revisit only if the voice cases in the eval set show allergen words misheard. The switch would stay inside the adapter, with the `Transcriber` signature unchanged.

### Menu import: Apify Website Content Crawler

1. **Use `apify/website-content-crawler`.** Apify maintains it, it is the most-used option by far, it renders JavaScript menus, and it follows sub-menu links (lunch, dinner, drinks) under the given URL [A1], [A2]. The community menu scrapers were rejected: 1–9 users and no ratings each, and the two that state their limits skip PDF menus (MenuMiner also skips JavaScript menus) [A7], [A8], [A9]. RAG Web Browser would also work for a single page, but it fetches only the URL it is given [A6].
2. **Run it synchronously and cap the run.** Call `run-sync-get-dataset-items` with `timeout=120` and `memory=4096`. One run can then use at most 4 GB × 120 s ≈ 0.13 CU, about $0.03 on the Free plan (*computed* from [A5]). The 120 s cap also stays well under the 300 s sync limit [A3]. Input: the menu URL as the only start URL, `maxCrawlDepth: 1`, `maxCrawlPages: 10`, `crawlerType: playwright:adaptive`, `saveMarkdown: true`, `respectRobotsTxtFile: true`.
3. **One page becomes one prep-sheet seed.** The `markdown` of every page that has some becomes one seed text for the M4 profiling flow, and `{"items": n}` reports how many seeds were stored. `apis.md` doesn't define what `items` counts. This ADR reads it as seeds, which keeps the response shape. If the Director wants it to count dishes instead, that needs a dish-level extractor and a one-line wording change in `apis.md` (an RFC), not a new shape.
4. **The token goes in a header.** `APIFY_TOKEN` (already in `.env.example`) is sent as `Authorization: Bearer` [A4], never as a `?token=` query parameter, so it can't leak into request logs.
5. **Fail clearly and seed nothing.** An HTTP error, a `408` or a run that returns no page text (a PDF or image-only menu, a bot wall) raises `MenuImportFailed`, and `/v1/menu/import` returns an error. There are no partial imports.
6. **Seeds never confirm anything.** Menu text only enters profiling as `prep_sheet_text` for a bin a person picks, and it still goes through Jev and chef confirmation. Menus leave out ingredients (frying oil, garnish, shared fryers), so a profile built from menu text alone is never written with `confirmed_by_chef: true` (safety rule 1).

### Adapter sketches

These are sketches for the build tasks, not product code. `httpx` is shown for brevity. Picking the HTTP client is a root `pyproject.toml` change, and the platform team owns that file.

```python
"""Cartesia Ink adapter for Transcriber (sketch from ADR 0013)."""

from dataclasses import dataclass

import httpx

CARTESIA_STT_URL = "https://api.cartesia.ai/stt"
CARTESIA_VERSION = "2026-03-01"
CARTESIA_MODEL = "ink-whisper"
REQUEST_TIMEOUT_S = 10.0


class TranscriberUnavailable(Exception):
    """Ink gave no usable transcript. The tablet falls back to typing the note."""


@dataclass
class CartesiaInkTranscriber:
    """Transcriber backed by Cartesia Ink batch speech-to-text."""

    api_key: str
    language: str = "en"

    def transcribe(self, audio: bytes, *, sample_rate_hz: int) -> str:
        """Send one recorded note to Ink and return its transcript.

        Batch, not streaming: the tablet sends one finished clip, and the server
        reads the transcript before it is parsed (ADR 0013).
        """
        form = {"model": CARTESIA_MODEL, "language": self.language}

        # A WAV header tells Ink the format. Raw PCM has no header, so we must
        # name the encoding and rate, or Ink returns a wrong transcript silently.
        is_wav = audio.startswith(b"RIFF")
        if not is_wav:
            form["encoding"] = "pcm_s16le"
            form["sample_rate"] = str(sample_rate_hz)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Cartesia-Version": CARTESIA_VERSION,
        }
        file_name = "note.wav" if is_wav else "note.pcm"

        try:
            response = httpx.post(
                CARTESIA_STT_URL,
                headers=headers,
                data=form,
                files={"file": (file_name, audio)},
                timeout=REQUEST_TIMEOUT_S,
            )
            response.raise_for_status()
            text = response.json()["text"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            raise TranscriberUnavailable(str(error)) from error

        # An empty note would parse to "no constraints" and could send a ticket
        # with no allergens on it, so an empty transcript is a failure.
        if not isinstance(text, str) or not text.strip():
            raise TranscriberUnavailable("empty transcript")

        return text
```

```python
"""Apify menu import for POST /v1/menu/import (sketch from ADR 0013)."""

import httpx

CRAWLER_RUN_URL = "https://api.apify.com/v2/actors/apify~website-content-crawler/run-sync-get-dataset-items"
RUN_TIMEOUT_S = 120  # well under the 300 s limit of a synchronous run
RUN_MEMORY_MB = 4096  # with the timeout, caps one run at about 0.13 compute units
MAX_MENU_PAGES = 10


class MenuImportFailed(Exception):
    """The menu could not be fetched or held no text. Nothing was seeded."""


def build_crawler_input(menu_url: str) -> dict:
    """Crawler input: the menu page plus the sub-menu pages it links to."""
    return {
        "startUrls": [{"url": menu_url}],
        "crawlerType": "playwright:adaptive",  # many menus are drawn by JavaScript
        "maxCrawlDepth": 1,  # the menu page and its sub-menus, no further
        "maxCrawlPages": MAX_MENU_PAGES,
        "saveMarkdown": True,
        "respectRobotsTxtFile": True,
    }


def fetch_menu_pages(menu_url: str, apify_token: str) -> list[dict]:
    """Run the crawler once and return its dataset items, one per page."""
    try:
        response = httpx.post(
            CRAWLER_RUN_URL,
            params={"timeout": RUN_TIMEOUT_S, "memory": RUN_MEMORY_MB},
            headers={"Authorization": f"Bearer {apify_token}"},
            json=build_crawler_input(menu_url),
            timeout=RUN_TIMEOUT_S + 30,  # let Apify end the run before we hang up
        )
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise MenuImportFailed(str(error)) from error


def import_menu(menu_url: str, apify_token: str) -> list[str]:
    """Fetch a menu and return its prep-sheet seeds. api.py answers {"items": len(seeds)}."""
    seeds = []
    for page in fetch_menu_pages(menu_url, apify_token):
        markdown = (page.get("markdown") or "").strip()
        if markdown:
            seeds.append(markdown)

    # PDF and image-only menus come back with no text. Say so, rather than
    # reporting a successful import of nothing.
    if not seeds:
        raise MenuImportFailed("no menu text found at that URL")

    return seeds
```

## Consequences

- **Easier:** the M3 Ink task can build the adapter against a fake HTTP transport (`httpx.MockTransport` or similar), with no network in tests (`kitchen/orders/README.md`). The M4 import is one function and one Actor call.
- **Contracts:** unchanged. `Transcriber`, `/v1/transcribe` and `/v1/menu/import` keep their signatures and shapes. `TranscriberUnavailable` and `MenuImportFailed` stay inside the orders folders. Only the meaning of `items` is open (Decision, menu import, point 3).
- **Watch:** no batch latency figure exists yet. Neither does a run time for the crawler, a default retention period for guest audio, or the exact batch field names. Each build task measures or confirms its items with a real key, then updates this ADR.
- **Tests the build tasks should add:**
  - `safety`: an empty or whitespace transcript raises `TranscriberUnavailable` and `/v1/transcribe` never returns `{"text": ""}`.
  - `safety`: a profile built only from menu text is never `confirmed_by_chef`.
  - Fake-transport tests for every error branch above (timeout, 401, 402, 408, 429, malformed JSON).
- **Questions for the owner** (they block nothing now: Ink goes live in M3, the import in M4):
  1. Create a Cartesia account and put its key in the local `.env` as `CARTESIA_API_KEY`. The Free plan (20K credits a month) covers about 4,000 ten-second notes. `.env.example` shares this key with outputs' Cartesia Sonic voice.
  2. Create an Apify account and put its token in `.env` as `APIFY_TOKEN`. The Free plan's $5 a month covers well over 100 capped imports (*computed*).
  3. Is it acceptable to send servers' voice notes about guest allergies to Cartesia without Zero Data Retention, which needs an enterprise plan?
  4. Which restaurant menu URL will the demo use? Checking it early tells us whether it is HTML, JavaScript or PDF.

## Sources

All read on 2026-10-23.

- [C1] Cartesia docs, Compare STT endpoints: https://docs.cartesia.ai/use-the-api/stt/compare-endpoints.md
- [C2] Cartesia docs, Audio input: https://docs.cartesia.ai/build-with-cartesia/stt/audio-input.md
- [C3] Cartesia docs, Ink 2: https://docs.cartesia.ai/build-with-cartesia/stt/latest.md
- [C4] Cartesia docs, Older STT models: https://docs.cartesia.ai/build-with-cartesia/stt/older-models.md
- [C5] Cartesia docs, Batch STT example: https://docs.cartesia.ai/examples/stt-transcribe.md
- [C6] Cartesia docs, Authenticate your applications: https://docs.cartesia.ai/get-started/authenticate-your-client-applications.md
- [C7] Cartesia docs, API conventions: https://docs.cartesia.ai/use-the-api/api-conventions.md
- [C8] Cartesia docs, Pricing: https://docs.cartesia.ai/pricing.md
- [C9] Cartesia pricing page: https://cartesia.ai/pricing
- [C10] Cartesia launch page (Sonic-3.6 and Ink-2): https://www.cartesia.ai/launch/
- [C11] Cartesia docs, Keyterm prompting: https://docs.cartesia.ai/use-the-api/stt/keyterms.md
- [C12] Cartesia docs, Manual-finalize WebSocket example: https://docs.cartesia.ai/examples/stt-manual-finalize-websocket.md
- [C13] Cartesia docs, Zero Data Retention: https://docs.cartesia.ai/enterprise/zero-data-retention.md
- [C14] Cartesia docs index: https://docs.cartesia.ai/llms.txt
- [C15] Sarvam docs, Migrating speech-to-text from Cartesia (third party, shows Cartesia's curl request): https://docs.sarvam.ai/api/migrations/from-cartesia/speech-to-text.md
- [A1] Apify Store, Website Content Crawler: https://apify.com/apify/website-content-crawler
- [A2] Website Content Crawler input schema: https://apify.com/apify/website-content-crawler/input-schema
- [A3] Apify API, Run Actor synchronously and get dataset items: https://docs.apify.com/api/v2/act-run-sync-get-dataset-items-post
- [A4] Apify API, Getting started: https://docs.apify.com/api/v2/getting-started
- [A5] Apify pricing: https://apify.com/pricing
- [A6] Apify Store, RAG Web Browser: https://apify.com/apify/rag-web-browser
- [A7] Apify Store, MenuMiner: https://apify.com/seeb/menuminer---restaurant-menu-scraper.md
- [A8] Apify Store, Restaurant Menu Extractor (signal_lab): https://apify.com/signal_lab/restaurant-menu-extractor.md
- [A9] Apify Store, Restaurant Menu Price Extractor (ezzatox): https://apify.com/ezzatox/restaurant-menu-price-extractor.md

Login-gated, not read: Cartesia OpenAPI spec (https://docs.cartesia.ai/2026-03-01/api.yml) and the OpenAI migration guide (https://docs.cartesia.ai/use-the-api/stt/migrate-from-openai-speech-to-text).
