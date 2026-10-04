#!/usr/bin/env python3
"""Retrieve recoverable authored X text, or import an authorized normalized archive.

Only provider credentials are used; interpretation runs in the invoking agent.
Source responses and resumable checkpoints are local study artifacts, not skill assets.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
UTC = timezone.utc
SEARCH_DOC = "https://docs.socialdata.tools/reference/get-search-results/"


class ProviderError(Exception):
    """Static error codes deliberately omit URLs, headers and response bodies."""
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url, key, params=None, *, host="api.socialdata.tools", attempts=4, sleep=time.sleep):
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname != host or parts.username or parts.password
            or parts.port not in (None, 443) or parts.fragment):
        raise ProviderError("unsafe_destination")
    if not isinstance(key, str) or not key or any(c in key for c in "\r\n"):
        raise ProviderError("missing_provider_access")
    if params:
        url += ("&" if parts.query else "?") + urlencode(params)
    opener = build_opener(NoRedirect())
    for attempt in range(attempts):
        try:
            request = Request(url, headers={"Authorization": "Bearer " + key, "Accept": "application/json", "User-Agent": "DaveWang-XBacktest/1.0"})
            with opener.open(request, timeout=60) as response:
                value = json.loads(response.read(40_000_001))
            if not isinstance(value, dict):
                raise ProviderError("invalid_response")
            return value
        except HTTPError as error:
            code = {401: "unauthorized", 402: "insufficient_credits", 403: "entitlement_denied", 404: "not_found", 429: "rate_limited"}.get(error.code, "provider_response")
            if error.code not in {429, 500, 502, 503, 504} or attempt == attempts - 1:
                raise ProviderError(code) from None
        except (URLError, TimeoutError, OSError):
            if attempt == attempts - 1:
                raise ProviderError("network") from None
        except (ValueError, UnicodeError):
            raise ProviderError("invalid_response") from None
        sleep(min(2 ** attempt, 8))
    raise ProviderError("network")


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def read_study(path):
    study = json.loads(Path(path).read_text(encoding="utf-8"))
    account = study.get("account", {})
    handle = account.get("handle", "").lstrip("@") if isinstance(account, dict) else str(account).lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", handle):
        raise ValueError("Study requires one valid X handle.")
    start, last = date.fromisoformat(study["startDate"]), date.fromisoformat(study["endDate"])
    if start > last or study.get("timezone", "America/New_York") != "America/New_York":
        raise ValueError("Study needs ordered New York dates.")
    begin = datetime.combine(start, datetime.min.time(), NY).astimezone(UTC)
    stop = datetime.combine(last + timedelta(days=1), datetime.min.time(), NY).astimezone(UTC)
    for field, expected in (("startUTC", begin), ("endExclusiveUTC", stop)):
        if field in study and datetime.fromisoformat(study[field].replace("Z", "+00:00")) != expected:
            raise ValueError("Study UTC bounds disagree with the requested New York dates.")
    return study, handle, begin, stop


def month_ranges(begin, stop):
    current = begin.astimezone(NY)
    end = stop.astimezone(NY)
    while current < end:
        following = datetime(current.year + (current.month == 12), current.month % 12 + 1, 1, tzinfo=NY)
        finish = min(following, end)
        yield current.strftime("%Y-%m"), current.astimezone(UTC), finish.astimezone(UTC)
        current = finish


def normalize_post(row, handle, author_id, begin, stop, *, imported=False):
    if not isinstance(row, dict):
        return None, "malformed"
    if imported:
        post_id, text, stamp, owner = row.get("id"), row.get("text"), row.get("publishedAt"), row.get("authorId")
        retweet = row.get("isRetweet") is True or row.get("retweeted_status") is not None
        reply, quote = row.get("isReply") is True, row.get("isQuote") is True
    else:
        user = row.get("user", {})
        owner = user.get("id_str") if isinstance(user, dict) else None
        post_id, text, stamp = row.get("id_str"), row.get("full_text"), row.get("tweet_created_at") or row.get("created_at")
        retweet = row.get("retweeted_status") is not None or bool(row.get("retweeted_status_id_str"))
        reply, quote = bool(row.get("in_reply_to_status_id_str")), row.get("is_quote_status") is True
    if owner != author_id:
        return None, "wrong_author_id"
    if retweet:
        return None, "native_retweet"
    if not isinstance(post_id, str) or not post_id.isdigit() or not isinstance(text, str) or not text.strip() or row.get("truncated") is True:
        return None, "malformed_or_truncated"
    try:
        published = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if published.tzinfo is None:
            raise ValueError
        published = published.astimezone(UTC)
    except (AttributeError, ValueError, TypeError):
        return None, "invalid_timestamp"
    if not begin <= published < stop:
        return None, "out_of_window"
    return {"id": post_id, "account": handle, "authorId": author_id, "publishedAt": published.isoformat(), "text": text,
            "url": f"https://x.com/{handle}/status/{post_id}", "isReply": reply, "isQuote": quote}, None


class Archive:
    def __init__(self, output_dir, handle, begin, stop, *, mode="api", author_id=None):
        self.root = Path(output_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.handle, self.begin, self.stop = handle, begin, stop
        self.connection = sqlite3.connect(self.root / "archive.sqlite3")
        self.connection.executescript("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE IF NOT EXISTS months(month TEXT PRIMARY KEY,payload TEXT); CREATE TABLE IF NOT EXISTS posts(id TEXT PRIMARY KEY,month TEXT,payload TEXT); CREATE TABLE IF NOT EXISTS pages(id TEXT PRIMARY KEY,payload TEXT);")
        identity = {"handle": handle.lower(), "begin": begin.isoformat(), "stop": stop.isoformat(), "mode": mode}
        old = self.get_meta("identity")
        if old and old != identity:
            self.connection.close()
            raise ValueError("Checkpoint belongs to another account, period or intake mode; use a new study directory.")
        self.set_meta("identity", identity)
        prior_id = self.get_meta("authorId")
        if author_id and prior_id and author_id != prior_id:
            self.connection.close()
            raise ValueError("Account identity changed; checkpoint cannot be reused.")
        if author_id:
            self.set_meta("authorId", author_id)
        self.author_id = author_id or prior_id
        for month, start, end in month_ranges(begin, stop):
            state = {"month": month, "start": start.isoformat(), "end": end.isoformat(), "state": "queued", "cursor": None, "maxId": None, "lowestId": None, "pages": 0, "delivered": 0, "authored": 0, "skips": {}, "error": None}
            self.connection.execute("INSERT OR IGNORE INTO months VALUES(?,?)", (month, json.dumps(state)))
        self.connection.commit()

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def get_meta(self, key):
        row = self.connection.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_meta(self, key, value):
        self.connection.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, json.dumps(value)))
        self.connection.commit()

    def states(self):
        return [json.loads(row[0]) for row in self.connection.execute("SELECT payload FROM months ORDER BY month")]

    def save_state(self, state):
        self.connection.execute("UPDATE months SET payload=? WHERE month=?", (json.dumps(state), state["month"]))

    def export(self):
        posts = [json.loads(row[0]) for row in self.connection.execute("SELECT payload FROM posts")]
        posts.sort(key=lambda p: (p["publishedAt"], int(p["id"])))
        states = self.states()
        mode = self.get_meta("identity")["mode"]
        manifest = {"schemaVersion": "1.0", "account": self.handle, "accountId": self.author_id, "provider": "SocialData live X search" if mode == "api" else "Authorized normalized import", "requestedStart": self.begin.isoformat(), "requestedEndExclusive": self.stop.isoformat(),
                    "searchExhaustedEveryMonth": mode == "api" and all(s["state"] == "search_exhausted" for s in states), "completeHistoricalCensus": False,
                    "coverageStatus": "search_exhausted" if mode == "api" and all(s["state"] == "search_exhausted" for s in states) else "imported" if mode == "import" else "partial",
                    "coverageNote": "Recoverable authored text only. Deleted, private, filtered or inaccessible posts remain unknown; search exhaustion does not prove a complete historical census.",
                    "sourceDocumentation": SEARCH_DOC if mode == "api" else None, "totals": {"posts": len(posts), "pages": sum(s["pages"] for s in states), "delivered": sum(s["delivered"] for s in states)}, "months": states, "postsSha256": digest(posts)}
        write_json(self.root / "posts.json", posts)
        write_json(self.root / "archive-coverage.json", manifest)
        with (self.root / "archive-month-counts.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["month", "state", "authored", "delivered", "pages"])
            writer.writeheader()
            writer.writerows({key: s[key] for key in writer.fieldnames} for s in states)
        return manifest

    def retrieve(self, request, *, page_cap=500, max_pages=1000, max_delivered=100000):
        fetched = 0
        delivered = sum(s["delivered"] for s in self.states())
        for state in self.states():
            if state["state"] == "search_exhausted":
                continue
            start, end = datetime.fromisoformat(state["start"]), datetime.fromisoformat(state["end"])
            state["error"] = None
            seen_cursors = {state["cursor"]} if state["cursor"] else set()
            while state["pages"] < page_cap:
                if fetched >= max_pages or delivered >= max_delivered:
                    state["state"] = "budget_paused"
                    break
                if (self.root / "STOP").exists():
                    state["state"] = "cancelled"
                    break
                query = f"from:{self.handle} since_time:{int(start.timestamp())} until_time:{int(end.timestamp())} -filter:nativeretweets"
                if state["maxId"]:
                    query += " max_id:" + state["maxId"]
                params = {"query": query, "type": "Latest"}
                if state["cursor"]:
                    params["cursor"] = state["cursor"]
                try:
                    payload = request(params)
                    rows, cursor = payload.get("tweets"), payload.get("next_cursor")
                    if not isinstance(rows, list) or (cursor is not None and not isinstance(cursor, str)):
                        raise ProviderError("invalid_response")
                    page_id = f"{state['month']}-{state['pages'] + 1:05d}"
                    raw = json.dumps({"query": query, "cursorIn": state["cursor"], "retrievedAt": datetime.now(UTC).isoformat(), "payload": payload}, ensure_ascii=False).encode()
                    raw_path = self.root / "archive-source" / (page_id + ".json.gz")
                    raw_path.parent.mkdir(exist_ok=True)
                    with gzip.open(raw_path, "wb") as stream:
                        stream.write(raw)
                    ids = [int(r["id_str"]) for r in rows if isinstance(r, dict) and isinstance(r.get("id_str"), str) and r["id_str"].isdigit()]
                    prior = int(state["lowestId"]) if state["lowestId"] else None
                    lowest = min(ids + ([prior] if prior is not None else [])) if ids or prior is not None else None
                    skips, added = Counter(state["skips"]), 0
                    with self.connection:
                        for row in rows:
                            post, reason = normalize_post(row, self.handle, self.author_id, start, end)
                            if reason:
                                skips[reason] += 1
                            else:
                                inserted = self.connection.execute("INSERT OR IGNORE INTO posts VALUES(?,?,?)", (post["id"], state["month"], json.dumps(post, ensure_ascii=False))).rowcount
                                added += inserted
                                if not inserted:
                                    skips["duplicate"] += 1
                        if not rows:
                            state.update(state="search_exhausted", cursor=None)
                        elif cursor and cursor not in seen_cursors:
                            seen_cursors.add(cursor)
                            state.update(state="fetching", cursor=cursor)
                        elif lowest is not None and (prior is None or lowest < prior):
                            state.update(state="fetching", cursor=None, maxId=str(lowest - 1))
                        else:
                            state.update(state="pagination_stalled", cursor=None, error="pagination_stalled")
                        state.update(lowestId=str(lowest) if lowest is not None else None, pages=state["pages"] + 1, delivered=state["delivered"] + len(rows), authored=state["authored"] + added, skips=dict(skips))
                        self.connection.execute("INSERT INTO pages VALUES(?,?)", (page_id, json.dumps({"rawPath": str(raw_path.relative_to(self.root)), "sha256": hashlib.sha256(raw).hexdigest(), "rows": len(rows)})))
                        self.save_state(state)
                    fetched += 1
                    delivered += len(rows)
                    if state["state"] in {"search_exhausted", "pagination_stalled"}:
                        break
                except ProviderError as error:
                    state.update(state="failed", error=error.code)
                    break
            else:
                state["state"] = "page_cap_paused"
            with self.connection:
                self.save_state(state)
            self.export()
        return self.export()

    def import_posts(self, rows):
        if not isinstance(rows, list):
            raise ValueError("Imported archive must be a normalized post list.")
        states = {s["month"]: s for s in self.states()}
        seen = set()
        with self.connection:
            for row in rows:
                post, reason = normalize_post(row, self.handle, self.author_id, self.begin, self.stop, imported=True)
                if reason:
                    raise ValueError("Imported post failed validation: " + reason)
                if post["id"] in seen:
                    raise ValueError("Imported archive contains duplicate post IDs.")
                seen.add(post["id"])
                existing = self.connection.execute("SELECT payload FROM posts WHERE id=?", (post["id"],)).fetchone()
                if existing and json.loads(existing[0]) != post:
                    raise ValueError("Imported source conflicts with an existing frozen post.")
                month = datetime.fromisoformat(post["publishedAt"]).astimezone(NY).strftime("%Y-%m")
                added = self.connection.execute("INSERT OR IGNORE INTO posts VALUES(?,?,?)", (post["id"], month, json.dumps(post, ensure_ascii=False))).rowcount
                states[month]["authored"] += added
                states[month]["delivered"] += added
            for state in states.values():
                state.update(state="imported", error=None)
                self.save_state(state)
        return self.export()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--import", dest="import_file", type=Path)
    parser.add_argument("--account-id", help="Verified numeric account ID, required for normalized imports unless present in study.account.id.")
    parser.add_argument("--page-cap", type=int, default=500, help="Cumulative pages per month; raise to resume a capped month.")
    parser.add_argument("--max-pages", type=int, default=1000, help="Maximum API search pages in this invocation.")
    parser.add_argument("--max-delivered", type=int, default=100000, help="Cumulative delivered-row stopping threshold; the final unpredictable page may exceed it.")
    parser.add_argument("--status", action="store_true", help="Export existing checkpoints without network requests.")
    args = parser.parse_args()
    archive = None
    try:
        study, handle, begin, stop = read_study(args.study)
        if min(args.page_cap, args.max_pages, args.max_delivered) <= 0:
            raise ValueError("Caps must be positive.")
        owner = args.account_id or (study["account"].get("id") if isinstance(study["account"], dict) else None)
        if owner is not None and (not isinstance(owner, str) or not owner.isdigit()):
            raise ValueError("Verified account ID must be a numeric string.")
        if args.import_file and not owner:
            raise ValueError("Normalized imports require a verified --account-id or study.account.id.")
        if args.status:
            if not (args.output_dir / "archive.sqlite3").exists():
                raise ValueError("No checkpoint exists in this study directory.")
            con = sqlite3.connect(args.output_dir / "archive.sqlite3")
            identity = json.loads(con.execute("SELECT value FROM meta WHERE key='identity'").fetchone()[0])
            con.close()
            archive = Archive(args.output_dir, handle, begin, stop, mode=identity["mode"], author_id=owner)
            result = archive.export()
        elif args.import_file:
            archive = Archive(args.output_dir, handle, begin, stop, mode="import", author_id=owner)
            payload = json.loads(args.import_file.read_text(encoding="utf-8"))
            result = archive.import_posts(payload.get("posts") if isinstance(payload, dict) else payload)
        else:
            key = os.environ.get("SOCIALDATA_API_KEY", "")
            profile = request_json("https://api.socialdata.tools/twitter/user/" + handle, key)
            if not isinstance(profile.get("id_str"), str) or not profile["id_str"].isdigit() or str(profile.get("screen_name", "")).lower() != handle.lower():
                raise ProviderError("unverified_account")
            if owner and owner != profile["id_str"]:
                raise ProviderError("account_identity_mismatch")
            archive = Archive(args.output_dir, handle, begin, stop, author_id=profile["id_str"])
            archive.set_meta("profile", {k: profile.get(k) for k in ("id_str", "screen_name", "name")})
            def request(params):
                time.sleep(0.6)
                return request_json("https://api.socialdata.tools/twitter/search", key, params)
            result = archive.retrieve(request, page_cap=args.page_cap, max_pages=args.max_pages, max_delivered=args.max_delivered)
        print(json.dumps({"coverageStatus": result["coverageStatus"], "totals": result["totals"], "states": dict(Counter(s["state"] for s in result["months"]))}))
        return 0
    except (ProviderError, ValueError, OSError, KeyError, TypeError, sqlite3.Error) as error:
        print(json.dumps({"status": "failed", "code": error.code if isinstance(error, ProviderError) else "invalid_input_or_checkpoint"}), flush=True)
        return 1
    finally:
        if archive is not None:
            archive.close()


if __name__ == "__main__":
    raise SystemExit(main())
