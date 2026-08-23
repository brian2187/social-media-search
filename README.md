# Social Media Search

Local app (Post Ledger): enter social IDs and/or drop official data-export ZIPs. Paginate public official APIs until they stop. Parse posts into a canonical store. Categorize. Browse offline at http://127.0.0.1:8768/

Does not forge guest tokens, solve CAPTCHAs, or rotate IPs. If a platform stops serving pages, we stop. X unauthenticated bulk history is closed by X; put `x_bearer` in `config.local.json` to paginate API v2. Own-account ZIP is still the complete attic.

## v1 ingest

| Platform | How | Complete history |
|----------|-----|------------------|
| **X / x.com** | Official archive ZIP (`Your archive.html` + `data/*.js`) | Yes, **own account only** |
| X handle (live) | Official API v2, paginate until empty (`x_bearer` in config.local.json) | Until X stops — not a Hitch cap |
| Reddit | Public JSON / official export if present | Recent public; full = export |
| YouTube | Data API if a key is in local config | Uploads list, not "all comments ever" without quota |
| Instagram / Facebook / TikTok / LinkedIn | Official "download your data" ZIP when we add a parser | Own export only |
| Scrape / login-steal / ToS bypass | **Refuse** | — |

## Run

```
python serve.py
```

Opens http://127.0.0.1:8768/

Drop an X archive ZIP on the page or into `inbox\`. Parsed rows land in `data\ledger.sqlite`.

Do not commit `inbox\`, `data\`, or API keys. Keys stay in `config.local.json` (gitignored).

## Categories (first-pass, rules)

`original` `reply` `repost` `quote` `thread` `has_media` `has_link` `has_mention` `question` `short` `long` `dated`

Ollama can refine later. Not Core. Not a second Grok.
