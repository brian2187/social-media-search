# Social Media Search

Local app (Post Ledger): enter social IDs and/or drop official data-export ZIPs. Paginate public official APIs until they stop. Parse posts into a canonical store. Categorize. Browse offline at http://127.0.0.1:8768/

Does not forge guest tokens, solve CAPTCHAs, or rotate IPs. If a platform stops serving pages, we stop. X pulls without a bearer use the public profile timeline and keep only that handle's posts (replies included). Put `x_bearer` in `config.local.json` for official API v2. Own-account ZIP is still the complete attic.

## v1 ingest

| Platform | How | Complete history |
|----------|-----|------------------|
| **X / x.com** | Official archive ZIP (`Your archive.html` + `data/*.js`) | Yes, **own account only** |
| X handle (live) | Official API v2 if `x_bearer` is set; otherwise public profile timeline (own posts only, next pull is incremental) | Until the public timeline stops. ZIP is still the complete own-account attic |
| Reddit | Public JSON / official export if present | Recent public; full = export |
| YouTube | Data API if a key is in local config | Uploads list, not "all comments ever" without quota |
| Instagram / Facebook / TikTok / LinkedIn | Official "download your data" ZIP when we add a parser | Own export only |
| Scrape / login-steal / ToS bypass | **Refuse** | — |

## Run

```
python serve.py
```

Opens http://127.0.0.1:8768/ (or the **Post Ledger** desktop shortcut).

Drop an X archive ZIP on the page or into `inbox\`. Parsed rows land in `data\ledger.sqlite`. Home lists accounts you have stored; opening an account shows only that handle. A later pull for the same id inserts only posts not already in the database.

**Detailed breakdown** builds a temp profile page (avatar, topic/kind split, who replies were to, overall summary) and deletes that page when you close it.

Do not commit `inbox\`, `data\`, or API keys. Keys stay in `config.local.json` (gitignored).

## Categories (first-pass, rules)

`original` `reply` `repost` `quote` `has_media` `has_link` `has_mention` `question` `short` `long`

Topics (separate): `ai` `games` `politics` `finance` `media` `culture` `tech` `other`

Ollama can refine later. Not Core. Not a second Grok.
