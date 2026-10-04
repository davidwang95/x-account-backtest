# Archive and price intake

Use a connected provider or an authorized existing export first. The invoking Codex or Claude agent interprets posts; these adapters do not require a separate AI account or AI API key. Provider scripts are optional paths for environments that already have data access.

## Authored X archive

`retrieve_posts.py` consumes a frozen `study.json`. `startDate` and `endDate` are inclusive New York dates. Calendar-month search windows use actual New York UTC offsets, including daylight-saving changes. The script checks any supplied `startUTC` and `endExclusiveUTC` against those dates.

```text
python scripts/retrieve_posts.py --study STUDY/study.json --output-dir STUDY
python scripts/retrieve_posts.py --study STUDY/study.json --output-dir STUDY --status
```

The direct API adapter reads `SOCIALDATA_API_KEY` from the environment and authenticates using a bearer header. It resolves the profile through `GET /twitter/user/{username}` and verifies the numeric author ID on every returned post. Search uses `GET /twitter/search` with `query`, `type=Latest` and an optional cursor. Date and account operators belong inside the query. When cursors end or repeat, the adapter continues below the lowest delivered ID using `max_id`. A stalled cursor/ID is a partial archive, never successful exhaustion.

The adapter preserves exact authored `full_text`, including replies and the author's own quote-post commentary. It excludes native reposts, wrong authors, truncated text and out-of-window rows. It does not substitute the quoted account's text, linked articles, images or video dialogue.

Files in the study directory:

- `posts.json`: chronological normalized posts with `id`, `account`, `authorId`, `publishedAt`, `text`, `url`, `isReply` and `isQuote`.
- `archive-coverage.json`: account ID, requested UTC bounds, month states, delivered/authored counts and explicit coverage limits.
- `archive-month-counts.csv`: monthly counts for report charts.
- `archive.sqlite3` and `archive-source/`: local resume checkpoints and compressed source responses. Keep these private to the study; do not ship them in the skill.

Run the same command to resume. `--page-cap` is a cumulative cap per month; raise it to continue a capped month. `--max-pages` limits search pages in one invocation. `--max-delivered` is a stopping threshold across invocations; an unpredictable final page may exceed it. A local `STOP` file pauses intake. Provider failures are stored as static codes without response bodies or credentials.

Search exhaustion establishes the end of the provider's delivered search results. It cannot establish that deleted, private, filtered or otherwise unavailable historical posts were recovered. An empty archive must be reported as empty recoverable coverage rather than evidence that an account made no calls.

### Import instead of API access

```text
python scripts/retrieve_posts.py --study STUDY/study.json --output-dir STUDY --import authorized-posts.json --account-id VERIFIED_NUMERIC_ID
```

The file is a normalized post list, or an object with a `posts` list. Every row must have the verified `authorId`, a unique numeric-string `id`, exact authored `text` and a timezone-aware `publishedAt` within the study. The account ID may instead be supplied in `study.account.id`. Verify that identity independently before importing. Imports are labeled `imported`; they are never labeled API search exhaustion or a complete historical census. Use a separate study directory when changing account, dates or intake mode.

## US equity and ETF prices

```text
python scripts/fetch_prices.py --study STUDY/study.json --calls STUDY/calls.json --series-dir STUDY/prices
```

The API adapter reads `MASSIVE_API_KEY` from the environment. It requests `GET /v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}` with `adjusted=true`, ascending order and pagination. Successful responses must confirm the exact ticker and split adjustment. The adapter saves SPY separately for benchmark and calendar verification. It refuses unsafe pagination hosts and removes authentication query parameters before retaining pagination URLs.

Eligible calls need `issuerMappingValidated=true` plus documentary dated issuer proof. A literal ticker or a model's plausible company name is insufficient. Accepted proof forms:

- `issuerReference`: `asOf` equals the publication New York date, `status=ok`, an HTTPS source URL, retained-response SHA-256 and metadata identifying the ticker, US stock market, security type and FIGI or a documented fallback identity.
- `issuerMappingEvidence`: an HTTPS source URL, the publication New York `asOfDate`, `issuerName`, `securityId` matching the call, `market=stocks`, `locale=us` and a supported equity/ETF type.

These records must originate from source review. The price adapter checks their structure and timing; it does not replace the agent's issuer adjudication. Historical reference data should distinguish ticker reuse, share classes, ADRs and security changes. Retain supporting reference responses in the study audit bundle.

For a dated reference lookup, use an existing Massive connector or authenticated `GET https://api.massive.com/v3/reference/tickers/SYMBOL?date=YYYY-MM-DD`, where the date is the call's New York publication date. Supply the existing key in the authorization header rather than retaining it in a source URL. Save the reference response locally, its SHA-256 and the returned US market/security metadata. Verify the named company and exact security/share class from the authored text before setting `issuerMappingValidated=true`. A response containing a matching ticker alone does not resolve semantic ambiguity.

Explicit listing and delisting dates restrict the requested interval. Different reviewed security identities under the same ticker are rejected from a single shared series. The adapter never automatically substitutes an acquirer, another share class or a renamed ticker. A verified rename chain requires a separately reviewed same-security price import; preserve its dated continuity proof. Without that proof, the original ticker's missing history remains a gap.

Outputs are `prices/{SYMBOL}.json` caches, `prices/source/` retained aggregate responses and `price-manifest.json` beside the study file. Caches record actual first/last observations, response hashes, partial states and empty histories. Re-running resumes successful pages after a transient failure; `--refresh` starts a new retrieval for that series.

`--max-requests` limits logical requests; bounded network retries may make additional HTTP attempts. `--min-interval` defaults to a conservative 12.1 seconds. Reduce it only when the provider entitlement permits. Missing access or entitlement remains an explicit failure; do not buy access or substitute synthetic results.

### Price limits and separate asset classes

The cutoff is the frozen last completed price date, independent of the post-archive end date. Live daily bars are omitted until 20:00 New York time. Provider entitlements can truncate history or delay the latest observation. The scorer must independently audit SPY sessions through the declared cutoff; adjust the cutoff transparently or recover missing bars before publishing statistics.

Daily aggregate opens are research proxies and can reflect qualifying extended-hours trades. Split adjustment does not include dividends. Missing required entry/exit bars remain unscored: no forward fills, delayed entries, terminal-zero assumptions or invented delisting returns.

Crypto, currencies, commodities and macro views stay in the call ledger unless a separate, identified instrument and appropriate price/calendar contract are supplied. Do not pool a 24-hour crypto window with US-stock trading sessions. The bundled direct price adapter supports US equities and ETFs; other market adapters must preserve separate outcomes and disclosures.

### Optional public crypto adapter

```text
python scripts/fetch_crypto_prices.py --study STUDY/study.json --calls STUDY/calls.json --series-dir STUDY/crypto-prices
```

This separate adapter requires no key. It verifies an identified base asset against Coinbase's exact USD product, then retrieves UTC daily candles in bounded date chunks. Broad or ambiguous crypto comments are excluded. Responses, missing dates, partial progress and product proof remain in the crypto cache and `crypto-price-manifest.json`. No USDT conversion, interpolation or replacement candle is allowed. The report uses `crypto_scoring.py` and separate calendar-day results.

[Coinbase's candle contract](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles) and [product identity fields](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-single-product) define this adapter.

## Offline verification

```text
python -m unittest discover -s scripts -p test_provider_adapters.py -v
```

Fixtures test New York month boundaries, authored attribution, retweets/truncation, failure/resume behavior, caps, import rollback, checkpoint identity, unsafe destinations, dated mapping proof, split adjustment, duplicate bars and empty/live histories. They contain synthetic data and make no network requests.

## Official endpoint references

- [SocialData user profiles](https://docs.socialdata.tools/reference/get-user-profile/)
- [SocialData search and pagination](https://docs.socialdata.tools/reference/get-search-results/)
- [SocialData rate limits](https://docs.socialdata.tools/getting-started/rate-limits/)
- [Massive bearer authentication](https://massive.com/docs/rest)
- [Massive custom daily bars](https://massive.com/docs/rest/stocks/aggregates/custom-bars)
- [Massive historical ticker reference](https://massive.com/docs/rest/stocks/tickers/ticker-overview)
