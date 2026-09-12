#!/usr/bin/env python3
"""Collect UNECE R85 maximum 30-minute electric-power data from RDW Open Data.

v1.3 adds:
- RDW make/brand enrichment from a single server-side grouped query against
  Gekentekende_voertuigen rather than downloading the plate-level registry;
- useful approval-level registration context: Dutch row count, first/latest
  admission dates, vehicle type, EU vehicle category, mass, seats, dimensions,
  and maximum design speed;
- explicit source-quality flags for implausible 30-minute-power values while
  preserving RDW values verbatim;
- a review CSV and SQLite table containing flagged source rows;
- optional Socrata app-token support through --app-token or SOCRATA_APP_TOKEN.

The make/brand field comes from RDW `merk`. It is a public-facing make/brand,
not necessarily the legal manufacturer entity.

Collection policy:
- large, sequential SODA requests only;
- 2 s minimum gap between network requests;
- cached pages are reused;
- retries honor Retry-After and use backoff;
- large companion datasets are paged in 5,000,000-row chunks;
- the registration enrichment is aggregated by RDW before download;
- no per-plate crawling;
- source values are never silently corrected.

Existing v1.2 raw energy, drivetrain, and manufacturer-commercial-name caches
are reused. On a v1.2 output directory, v1.3 normally needs only the new
registration-enrichment query.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://opendata.rdw.nl/resource"
USER_AGENT = "RDW-R85-research-collector/1.3 (low-rate bulk research client)"
COURTESY_DELAY_SECONDS = 2.0
MAX_RETRIES = 5
TIMEOUT_SECONDS = 600
PAGE_SIZE = 5_000_000
MAX_COMPANION_PAGES = 20

ENERGY_ID = "gr7t-qfnb"
DRIVETRAIN_ID = "4by9-ammk"
NAMES_ID = "x5v3-sewk"
REGISTRATION_ID = "m9d7-ebf2"
DEFAULT_POWER_REVIEW_THRESHOLD_KW = 1000.0

ENERGY_FIELDS = [
    "typegoedkeuringsnummer",
    "codevarianttgk",
    "codeuitvoeringtgk",
    "volgnummerrevisieuitvoering",
    "volgnummeraandrijving",
    "volgnummerenergiebron",
    "codeenergiebron",
    "maximumnettovermogenogr",
    "maximumnettovermogenbgr",
    "maximumvermogen30minogr",
    "maximumvermogen30minbgr",
    "actieradiusvolledigelekwltpogr",
    "actieradiusvolledigelekwltpbgr",
    "actieradiusexternoplaadwltpogr",
    "actieradiusexternoplaadwltpbgr",
]

DRIVETRAIN_FIELDS = [
    "typegoedkeuringsnummer",
    "codevarianttgk",
    "codeuitvoeringtgk",
    "volgnummerrevisieuitvoering",
    "volgnummeraandrijving",
    "elektromotorindicator",
    "hybridemotorindicator",
    "externoplaadbaarindicator",
    "enkelelektrischschakelingind",
    "codebrandstoftypemotor",
    "motorcode",
]

# RDW spells this field codevariantgk (without the second 't') in this dataset.
NAMES_FIELDS = [
    "typegoedkeuringsnummer",
    "codevariantgk",
    "codeuitvoeringtgk",
    "volgnummerrevisieuitvoering",
    "volgnummerhandelsbenamingfabr",
    "typeaanduidingfabrikant",
    "handelsbenamingfabrikant",
]

# This query is intentionally aggregated at RDW before transfer.  It provides
# a readable make/brand plus useful context without downloading millions of
# individual licence-plate rows.
REGISTRATION_SELECT = ",".join(
    [
        "typegoedkeuringsnummer",
        "merk",
        "voertuigsoort",
        "europese_voertuigcategorie",
        "count(*) AS registration_count",
        "min(datum_eerste_toelating_dt) AS first_admission_date",
        "max(datum_eerste_toelating_dt) AS latest_admission_date",
        "min(massa_rijklaar) AS ready_mass_min_kg",
        "max(massa_rijklaar) AS ready_mass_max_kg",
        "min(maximale_constructiesnelheid) AS max_design_speed_min_kmh",
        "max(maximale_constructiesnelheid) AS max_design_speed_max_kmh",
        "min(aantal_zitplaatsen) AS seats_min",
        "max(aantal_zitplaatsen) AS seats_max",
        "min(lengte) AS length_min_mm",
        "max(lengte) AS length_max_mm",
        "min(breedte) AS width_min_mm",
        "max(breedte) AS width_max_mm",
        "min(hoogte_voertuig) AS height_min_mm",
        "max(hoogte_voertuig) AS height_max_mm",
    ]
)
REGISTRATION_GROUP = ",".join(
    [
        "typegoedkeuringsnummer",
        "merk",
        "voertuigsoort",
        "europese_voertuigcategorie",
    ]
)
REGISTRATION_ORDER = "typegoedkeuringsnummer,merk,voertuigsoort,europese_voertuigcategorie"

_app_token: str | None = None

_last_request_started = 0.0


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def query_url(
    dataset_id: str,
    select: str,
    where: str | None = None,
    *,
    group: str | None = None,
    order: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> str:
    params: dict[str, str] = {"$select": select}
    if where:
        params["$where"] = where
    if group:
        params["$group"] = group
    if order:
        params["$order"] = order
    if limit is not None:
        params["$limit"] = str(limit)
    if offset is not None:
        params["$offset"] = str(offset)
    encoded = urllib.parse.urlencode(params, safe=",()=' * ")
    return f"{BASE}/{dataset_id}.csv?{encoded}"


def dataset_url(
    dataset_id: str,
    fields: list[str],
    where: str | None = None,
    *,
    limit: int | None = None,
    offset: int | None = None,
) -> str:
    return query_url(
        dataset_id,
        ",".join(fields),
        where,
        limit=limit,
        offset=offset,
    )


def pace_request() -> None:
    global _last_request_started
    now = time.monotonic()
    if _last_request_started:
        wait = COURTESY_DELAY_SECONDS - (now - _last_request_started)
        if wait > 0:
            time.sleep(wait)
    _last_request_started = time.monotonic()


def download_once(url: str, destination: Path) -> dict:
    pace_request()
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/csv",
        "Accept-Encoding": "identity",
    }
    if _app_token:
        headers["X-App-Token"] = _app_token
    req = urllib.request.Request(
        url,
        headers=headers,
        method="GET",
    )
    started = time.time()
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
        status = getattr(response, "status", 200)
        if status != 200:
            raise RuntimeError(f"HTTP {status} for {url}")

        encoded_tmp = destination.with_suffix(destination.suffix + ".download")
        normalized_tmp = destination.with_suffix(destination.suffix + ".part")
        content_encoding = (response.headers.get("Content-Encoding") or "").lower()

        try:
            with encoded_tmp.open("wb") as out:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)

            with encoded_tmp.open("rb") as f:
                magic = f.read(2)
            is_gzip = content_encoding == "gzip" or magic == b"\x1f\x8b"

            if is_gzip:
                with gzip.open(encoded_tmp, "rb") as src, normalized_tmp.open("wb") as dst:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        dst.write(chunk)
                normalized_tmp.replace(destination)
            else:
                encoded_tmp.replace(destination)

            return {
                "http_status": status,
                "elapsed_seconds": round(time.time() - started, 3),
                "content_type": response.headers.get("Content-Type"),
                "content_encoding": content_encoding or None,
                "normalized_from_gzip": is_gzip,
                "last_modified": response.headers.get("Last-Modified"),
                "etag": response.headers.get("ETag"),
            }
        finally:
            encoded_tmp.unlink(missing_ok=True)
            normalized_tmp.unlink(missing_ok=True)


def normalize_cached_csv(path: Path) -> bool:
    if not path.exists():
        return False
    with path.open("rb") as probe:
        if probe.read(2) != b"\x1f\x8b":
            return False
    tmp = path.with_suffix(path.suffix + ".normalize")
    try:
        with gzip.open(path, "rb") as src, tmp.open("wb") as dst:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                dst.write(chunk)
        tmp.replace(path)
        return True
    finally:
        tmp.unlink(missing_ok=True)


def polite_download(url: str, destination: Path, refresh: bool = False) -> dict:
    if destination.exists() and not refresh:
        normalized = normalize_cached_csv(destination)
        return {
            "cached": True,
            "downloaded_at": None,
            "normalized_cached_gzip": normalized,
            "bytes": destination.stat().st_size,
            "sha256": sha256_file(destination),
            "url": url,
        }

    destination.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(MAX_RETRIES):
        try:
            # Exactly one download on a successful attempt.
            meta = download_once(url, destination)
            meta.update(
                {
                    "cached": False,
                    "downloaded_at": now_iso(),
                    "bytes": destination.stat().st_size,
                    "sha256": sha256_file(destination),
                    "url": url,
                }
            )
            return meta
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == MAX_RETRIES - 1:
                raise
            retry_after = e.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else 2 ** (attempt + 1)
            except ValueError:
                delay = 2 ** (attempt + 1)
            time.sleep(min(delay, 60))
        except (urllib.error.URLError, TimeoutError):
            if attempt == MAX_RETRIES - 1:
                raise
            time.sleep(min(2 ** (attempt + 1), 60))
    raise RuntimeError("unreachable")


def open_csv_text(path: Path):
    with path.open("rb") as probe:
        magic = probe.read(2)
    opener = gzip.open if magic == b"\x1f\x8b" else open
    return opener(path, "rt", encoding="utf-8-sig", newline="")


def read_csv(path: Path) -> list[dict[str, str]]:
    with open_csv_text(path) as f:
        return list(csv.DictReader(f))


def iter_csv_files(paths: list[Path]):
    for path in paths:
        with open_csv_text(path) as f:
            yield from csv.DictReader(f)


def csv_row_count(path: Path) -> int:
    with open_csv_text(path) as f:
        return sum(1 for _ in csv.DictReader(f))


def page_path(base: Path, page_index: int) -> Path:
    if page_index == 0:
        return base
    return base.with_name(f"{base.stem}.part-{page_index:04d}{base.suffix}")


def clear_pages(base: Path) -> None:
    base.unlink(missing_ok=True)
    for p in base.parent.glob(f"{base.stem}.part-*{base.suffix}"):
        p.unlink(missing_ok=True)


def download_paged_dataset(
    dataset_id: str,
    fields: list[str],
    base_path: Path,
    *,
    refresh: bool,
) -> tuple[list[Path], dict]:
    """Fetch a whole large companion table using a few very large pages.

    Existing v1.x base files are page zero and are reused. We keep continuation
    pages separate so a failed run never requires re-downloading earlier pages.
    """
    if refresh:
        clear_pages(base_path)

    paths: list[Path] = []
    page_meta: list[dict] = []
    total_rows = 0

    for page_index in range(MAX_COMPANION_PAGES):
        offset = page_index * PAGE_SIZE
        dest = page_path(base_path, page_index)
        url = dataset_url(dataset_id, fields, limit=PAGE_SIZE, offset=offset)
        print(f"    page {page_index + 1}: offset {offset:,}")
        meta = polite_download(url, dest, refresh=False)
        rows = csv_row_count(dest)
        meta.update({"page_index": page_index, "offset": offset, "rows": rows})
        page_meta.append(meta)
        paths.append(dest)
        total_rows += rows
        print(f"      {rows:,} rows ({'cache' if meta['cached'] else 'downloaded'})")

        if rows < PAGE_SIZE:
            return paths, {
                "complete": True,
                "page_size": PAGE_SIZE,
                "total_rows": total_rows,
                "pages": page_meta,
            }

    raise RuntimeError(
        f"{dataset_id} still returned a full {PAGE_SIZE:,}-row page after "
        f"{MAX_COMPANION_PAGES} pages; refusing to silently truncate it."
    )



def download_paged_query(
    dataset_id: str,
    select: str,
    base_path: Path,
    *,
    where: str | None = None,
    group: str | None = None,
    order: str | None = None,
    refresh: bool,
) -> tuple[list[Path], dict]:
    """Fetch a grouped/filtered query using a few very large cached pages."""
    if refresh:
        clear_pages(base_path)

    paths: list[Path] = []
    page_meta: list[dict] = []
    total_rows = 0

    for page_index in range(MAX_COMPANION_PAGES):
        offset = page_index * PAGE_SIZE
        dest = page_path(base_path, page_index)
        url = query_url(
            dataset_id,
            select,
            where,
            group=group,
            order=order,
            limit=PAGE_SIZE,
            offset=offset,
        )
        print(f"    page {page_index + 1}: offset {offset:,}")
        meta = polite_download(url, dest, refresh=False)
        rows = csv_row_count(dest)
        meta.update({"page_index": page_index, "offset": offset, "rows": rows})
        page_meta.append(meta)
        paths.append(dest)
        total_rows += rows
        print(f"      {rows:,} rows ({'cache' if meta['cached'] else 'downloaded'})")

        if rows < PAGE_SIZE:
            return paths, {
                "complete": True,
                "page_size": PAGE_SIZE,
                "total_rows": total_rows,
                "pages": page_meta,
            }

    raise RuntimeError(
        f"{dataset_id} grouped query still returned a full {PAGE_SIZE:,}-row page "
        f"after {MAX_COMPANION_PAGES} pages; refusing to silently truncate it."
    )


def norm(v: str | None) -> str:
    return (v or "").strip()


def num(v: str | None):
    s = norm(v)
    if not s:
        return None
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None



def intnum(v: str | None) -> int:
    n = num(v)
    return int(n) if n is not None else 0


def unique_text(rows: list[dict], field: str) -> list[str]:
    return sorted({norm(r.get(field)) for r in rows if norm(r.get(field))})


def min_numeric(rows: list[dict], field: str):
    vals = [num(r.get(field)) for r in rows]
    vals = [v for v in vals if v is not None]
    return min(vals) if vals else None


def max_numeric(rows: list[dict], field: str):
    vals = [num(r.get(field)) for r in rows]
    vals = [v for v in vals if v is not None]
    return max(vals) if vals else None


def min_text(rows: list[dict], field: str) -> str:
    vals = [norm(r.get(field)) for r in rows if norm(r.get(field))]
    return min(vals) if vals else ""


def max_text(rows: list[dict], field: str) -> str:
    vals = [norm(r.get(field)) for r in rows if norm(r.get(field))]
    return max(vals) if vals else ""


def fmt_number(v) -> str:
    if v is None:
        return ""
    if float(v).is_integer():
        return str(int(v))
    return f"{v:g}"


def weighted_primary(rows: list[dict], field: str, weight_field: str = "registration_count") -> str:
    scores = defaultdict(int)
    for r in rows:
        value = norm(r.get(field))
        if value:
            scores[value] += intnum(r.get(weight_field))
    if not scores:
        return ""
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def power_quality(lo, hi, threshold_kw: float) -> tuple[str, str]:
    reasons: list[str] = []
    vals = [v for v in (lo, hi) if v is not None]
    if lo is None or hi is None:
        reasons.append("one 30-minute-power bound is missing in the RDW source")
    if any(v < 0 for v in vals):
        reasons.append("negative 30-minute-power value in the RDW source")
    if any(v > threshold_kw for v in vals):
        reasons.append(
            f"source value exceeds review threshold ({threshold_kw:g} kW); "
            "possible source/unit/scaling issue; value preserved unchanged"
        )
    if lo is not None and hi is not None and lo > hi:
        reasons.append("lower bound exceeds upper bound in the RDW source")
    return ("review", "; ".join(reasons)) if reasons else ("ok", "")


def truth(v: str | None):
    s = norm(v).upper()
    if s in {"J", "JA", "Y", "YES", "TRUE", "1", "T"}:
        return True
    if s in {"N", "NEE", "NO", "FALSE", "0", "F"}:
        return False
    return None


def key4(row: dict, names: bool = False):
    return (
        norm(row.get("typegoedkeuringsnummer")),
        norm(row.get("codevariantgk" if names else "codevarianttgk")),
        norm(row.get("codeuitvoeringtgk")),
        norm(row.get("volgnummerrevisieuitvoering")),
    )


def drivetrain_key(row: dict):
    return key4(row) + (norm(row.get("volgnummeraandrijving")),)


def classify_drive(rows: list[dict]) -> tuple[str, str]:
    if not rows:
        return "unknown", ""
    e = any(truth(r.get("elektromotorindicator")) is True for r in rows)
    hybrid = any(truth(r.get("hybridemotorindicator")) is True for r in rows)
    plug = any(truth(r.get("externoplaadbaarindicator")) is True for r in rows)
    only_e = any(truth(r.get("enkelelektrischschakelingind")) is True for r in rows)

    if e and not hybrid and (only_e or not plug):
        cls = "BEV"
    elif e and hybrid and plug:
        cls = "PHEV"
    elif e and hybrid:
        cls = "HEV"
    elif e:
        cls = "electric/unknown"
    else:
        cls = "unknown"

    evidence = "; ".join(
        sorted(
            {
                ",".join(
                    [
                        f"E={norm(r.get('elektromotorindicator'))}",
                        f"H={norm(r.get('hybridemotorindicator'))}",
                        f"P={norm(r.get('externoplaadbaarindicator'))}",
                        f"EO={norm(r.get('enkelelektrischschakelingind'))}",
                    ]
                )
                for r in rows
            }
        )
    )
    return cls, evidence


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def create_sqlite(
    path: Path,
    homologation: list[dict],
    model: list[dict],
    review_rows: list[dict],
) -> None:
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    try:
        for table, rows in (
            ("homologation", homologation),
            ("model_view", model),
            ("review_rows", review_rows),
        ):
            if not rows:
                continue
            cols = list(rows[0].keys())
            qcols = ", ".join(f'"{c}" TEXT' for c in cols)
            con.execute(f'CREATE TABLE "{table}" ({qcols})')
            placeholders = ",".join("?" for _ in cols)
            colsql = ",".join('"' + c + '"' for c in cols)
            con.executemany(
                f'INSERT INTO "{table}" ({colsql}) VALUES ({placeholders})',
                [["" if r.get(c) is None else str(r.get(c)) for c in cols] for r in rows],
            )

        if homologation:
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_hom_tvv "
                "ON homologation(type_approval_number, variant, version, revision)"
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_hom_brand "
                "ON homologation(brand_make)"
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_hom_name "
                "ON homologation(commercial_name)"
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_hom_category "
                "ON homologation(eu_vehicle_category)"
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_hom_quality "
                "ON homologation(power_30min_quality_flag)"
            )
        con.commit()
    finally:
        con.close()


def main() -> int:
    global _app_token

    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="rdw-r85", help="output directory")
    ap.add_argument("--refresh", action="store_true", help="redownload all cached RDW source pages")
    ap.add_argument(
        "--app-token",
        default=os.environ.get("SOCRATA_APP_TOKEN", ""),
        help="optional Socrata app token; may also be set as SOCRATA_APP_TOKEN",
    )
    ap.add_argument(
        "--power-review-threshold-kw",
        type=float,
        default=DEFAULT_POWER_REVIEW_THRESHOLD_KW,
        help="flag, but do not alter, RDW 30-minute-power values above this threshold",
    )
    args = ap.parse_args()
    _app_token = args.app_token.strip() or None

    out = Path(args.output).resolve()
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)

    manifest = {
        "created_at": now_iso(),
        "collector": "collect_rdw_r85.py v1.3",
        "policy": {
            "sequential": True,
            "minimum_seconds_between_requests": COURTESY_DELAY_SECONDS,
            "page_size_rows": PAGE_SIZE,
            "cache_on_rerun": True,
            "retry_after_honored": True,
            "silent_truncation_forbidden": True,
            "registration_enrichment_server_side_aggregated": True,
            "per_plate_requests": False,
            "app_token_supplied": bool(_app_token),
            "power_review_threshold_kw": args.power_review_threshold_kw,
        },
        "sources": {},
        "warnings": [],
    }

    # Energy table is heavily filtered server-side and is well below PAGE_SIZE.
    energy_path = raw / "rdw_tgk_energy_ev_r85.csv"
    energy_url = dataset_url(
        ENERGY_ID,
        ENERGY_FIELDS,
        "codeenergiebron='E' AND (maximumvermogen30minogr IS NOT NULL OR maximumvermogen30minbgr IS NOT NULL)",
        limit=PAGE_SIZE,
    )
    print("Collecting filtered R85 energy rows...")
    manifest["sources"]["energy"] = polite_download(energy_url, energy_path, args.refresh)
    energy = read_csv(energy_path)
    manifest["sources"]["energy"]["rows"] = len(energy)
    if len(energy) >= PAGE_SIZE:
        raise RuntimeError("Filtered energy result reached PAGE_SIZE; refusing possible truncation.")

    # Build only the join keys we need before scanning the large companion tables.
    needed_k4 = {key4(e) for e in energy}
    needed_k3 = {k[:3] for k in needed_k4}
    needed_dk = {drivetrain_key(e) for e in energy}
    needed_approvals = {norm(e.get("typegoedkeuringsnummer")) for e in energy}

    print("Collecting drivetrain companion table in cached bulk pages...")
    drive_paths, drive_meta = download_paged_dataset(
        DRIVETRAIN_ID,
        DRIVETRAIN_FIELDS,
        raw / "rdw_tgk_drivetrain.csv",
        refresh=args.refresh,
    )
    manifest["sources"]["drivetrain"] = drive_meta

    print("Collecting manufacturer-name companion table in cached bulk pages...")
    name_paths, name_meta = download_paged_dataset(
        NAMES_ID,
        NAMES_FIELDS,
        raw / "rdw_tgk_names.csv",
        refresh=args.refresh,
    )
    manifest["sources"]["names"] = name_meta

    print("Collecting make/brand and vehicle context as one server-side grouped query...")
    reg_paths, reg_meta = download_paged_query(
        REGISTRATION_ID,
        REGISTRATION_SELECT,
        raw / "rdw_registration_approval_enrichment.csv",
        where="typegoedkeuringsnummer IS NOT NULL AND merk IS NOT NULL",
        group=REGISTRATION_GROUP,
        order=REGISTRATION_ORDER,
        refresh=args.refresh,
    )
    manifest["sources"]["registration_enrichment"] = reg_meta

    # Stream the huge companion files and retain only rows that can join to R85 data.
    drive_idx = defaultdict(list)
    drive_k4_seen = set()
    print("Indexing matching drivetrain rows locally...")
    for r in iter_csv_files(drive_paths):
        k4 = key4(r)
        if k4 in needed_k4:
            drive_k4_seen.add(k4)
        dk = drivetrain_key(r)
        if dk in needed_dk:
            drive_idx[dk].append(r)

    name_idx = defaultdict(list)
    name_k3_seen = set()
    print("Indexing matching manufacturer-name rows locally...")
    for r in iter_csv_files(name_paths):
        k4n = key4(r, names=True)
        if k4n[:3] in needed_k3:
            name_k3_seen.add(k4n[:3])
        if k4n in needed_k4:
            name_idx[k4n].append(r)

    reg_idx = defaultdict(list)
    print("Indexing matching make/brand registration aggregates locally...")
    for r in iter_csv_files(reg_paths):
        approval = norm(r.get("typegoedkeuringsnummer"))
        if approval in needed_approvals:
            reg_idx[approval].append(r)

    name_join_rows = sum(bool(name_idx.get(key4(e))) for e in energy)
    drive_join_rows = sum(bool(drive_idx.get(drivetrain_key(e))) for e in energy)
    registration_join_rows = sum(
        bool(reg_idx.get(norm(e.get("typegoedkeuringsnummer")))) for e in energy
    )
    name_k3_only_rows = sum(
        not bool(name_idx.get(key4(e))) and key4(e)[:3] in name_k3_seen for e in energy
    )
    drive_k4_only_rows = sum(
        not bool(drive_idx.get(drivetrain_key(e))) and key4(e) in drive_k4_seen for e in energy
    )

    manifest["join_diagnostics"] = {
        "energy_rows": len(energy),
        "manufacturer_name_exact_join_rows": name_join_rows,
        "manufacturer_name_exact_join_pct": round(100 * name_join_rows / len(energy), 3) if energy else 0,
        "manufacturer_name_exact_miss_but_same_approval_variant_version_rows": name_k3_only_rows,
        "drivetrain_exact_join_rows": drive_join_rows,
        "drivetrain_exact_join_pct": round(100 * drive_join_rows / len(energy), 3) if energy else 0,
        "drivetrain_exact_miss_but_same_four_key_rows": drive_k4_only_rows,
        "registration_brand_approval_join_rows": registration_join_rows,
        "registration_brand_approval_join_pct": round(
            100 * registration_join_rows / len(energy), 3
        ) if energy else 0,
        "registration_brand_join_key": "type_approval_number",
    }

    if energy and name_join_rows / len(energy) < 0.90:
        manifest["warnings"].append(
            "Manufacturer-name exact join coverage is below 90%; inspect join_diagnostics before treating blank labels as source-null."
        )
    if energy and drive_join_rows / len(energy) < 0.90:
        manifest["warnings"].append(
            "Drivetrain exact join coverage is below 90%; inspect join_diagnostics before treating unknown powertrain classes as source-null."
        )
    if energy and registration_join_rows / len(energy) < 0.50:
        manifest["warnings"].append(
            "Fewer than 50% of R85 rows match a current Dutch registered-vehicle approval. "
            "Brand/category blanks may simply mean the approval has no matching row in the current registration snapshot."
        )

    homologation = []
    for e in energy:
        k4 = key4(e)
        dk = drivetrain_key(e)
        approval = norm(e.get("typegoedkeuringsnummer"))

        drows = drive_idx.get(dk, [])
        powertrain_class, drive_evidence = classify_drive(drows)
        nrows = name_idx.get(k4, [])
        aliases = sorted(
            {
                norm(r.get("handelsbenamingfabrikant"))
                for r in nrows
                if norm(r.get("handelsbenamingfabrikant"))
            }
        )
        mtypes = sorted(
            {
                norm(r.get("typeaanduidingfabrikant"))
                for r in nrows
                if norm(r.get("typeaanduidingfabrikant"))
            }
        )

        rrows = reg_idx.get(approval, [])
        brand_make = weighted_primary(rrows, "merk")
        brand_aliases = unique_text(rrows, "merk")
        vehicle_type = weighted_primary(rrows, "voertuigsoort")
        eu_category = weighted_primary(rrows, "europese_voertuigcategorie")

        p30lo = num(e.get("maximumvermogen30minogr"))
        p30hi = num(e.get("maximumvermogen30minbgr"))
        exact = (
            p30lo
            if p30lo is not None and p30hi is not None and p30lo == p30hi
            else None
        )
        quality_flag, quality_note = power_quality(
            p30lo,
            p30hi,
            args.power_review_threshold_kw,
        )

        motor_codes = unique_text(drows, "motorcode")
        motor_fuel_type_codes = unique_text(drows, "codebrandstoftypemotor")

        homologation.append(
            {
                "brand_make": brand_make,
                "brand_make_aliases": " | ".join(brand_aliases),
                "brand_join_status": "matched" if rrows else "missing",
                "type_approval_number": approval,
                "variant": norm(e.get("codevarianttgk")),
                "version": norm(e.get("codeuitvoeringtgk")),
                "revision": norm(e.get("volgnummerrevisieuitvoering")),
                "drivetrain_sequence": norm(e.get("volgnummeraandrijving")),
                "energy_source_sequence": norm(e.get("volgnummerenergiebron")),
                "powertrain_class": powertrain_class,
                "registered_vehicle_type": vehicle_type,
                "eu_vehicle_category": eu_category,
                "manufacturer_type": " | ".join(mtypes),
                "commercial_name": " | ".join(aliases),
                "name_join_status": "matched" if nrows else "missing",
                "drivetrain_join_status": "matched" if drows else "missing",
                "rdw_registration_count_approval": str(
                    sum(intnum(r.get("registration_count")) for r in rrows)
                ) if rrows else "",
                "rdw_first_admission_date": min_text(rrows, "first_admission_date"),
                "rdw_latest_admission_date": max_text(rrows, "latest_admission_date"),
                "ready_mass_min_kg": fmt_number(min_numeric(rrows, "ready_mass_min_kg")),
                "ready_mass_max_kg": fmt_number(max_numeric(rrows, "ready_mass_max_kg")),
                "max_design_speed_min_kmh": fmt_number(
                    min_numeric(rrows, "max_design_speed_min_kmh")
                ),
                "max_design_speed_max_kmh": fmt_number(
                    max_numeric(rrows, "max_design_speed_max_kmh")
                ),
                "seats_min": fmt_number(min_numeric(rrows, "seats_min")),
                "seats_max": fmt_number(max_numeric(rrows, "seats_max")),
                "length_min_mm": fmt_number(min_numeric(rrows, "length_min_mm")),
                "length_max_mm": fmt_number(max_numeric(rrows, "length_max_mm")),
                "width_min_mm": fmt_number(min_numeric(rrows, "width_min_mm")),
                "width_max_mm": fmt_number(max_numeric(rrows, "width_max_mm")),
                "height_min_mm": fmt_number(min_numeric(rrows, "height_min_mm")),
                "height_max_mm": fmt_number(max_numeric(rrows, "height_max_mm")),
                # Keep RDW's field under a neutral name. It is sparsely populated
                # on electric energy-source rows and must not be assumed to be EV
                # advertised peak system power.
                "rdw_max_net_power_min_kw": e.get("maximumnettovermogenogr", ""),
                "rdw_max_net_power_max_kw": e.get("maximumnettovermogenbgr", ""),
                # These are copied verbatim from RDW.  Quality flags below identify
                # values that deserve review; this collector never rescales them.
                "power_30min_min_kw": e.get("maximumvermogen30minogr", ""),
                "power_30min_max_kw": e.get("maximumvermogen30minbgr", ""),
                "power_30min_exact_kw": "" if exact is None else f"{exact:g}",
                "power_30min_quality_flag": quality_flag,
                "power_30min_quality_note": quality_note,
                "power_30min_zero_source_flag": (
                    "yes" if p30lo == 0 or p30hi == 0 else ""
                ),
                "wltp_bev_range_min_km": e.get("actieradiusvolledigelekwltpogr", ""),
                "wltp_bev_range_max_km": e.get("actieradiusvolledigelekwltpbgr", ""),
                "wltp_phev_range_min_km": e.get("actieradiusexternoplaadwltpogr", ""),
                "wltp_phev_range_max_km": e.get("actieradiusexternoplaadwltpbgr", ""),
                "motor_codes": " | ".join(motor_codes),
                "motor_fuel_type_codes": " | ".join(motor_fuel_type_codes),
                "drivetrain_indicator_evidence": drive_evidence,
                "rdw_energy_dataset": ENERGY_ID,
                "rdw_registration_dataset": REGISTRATION_ID if rrows else "",
            }
        )

    homologation.sort(
        key=lambda r: (
            r["brand_make"],
            r["commercial_name"],
            r["manufacturer_type"],
            r["type_approval_number"],
            r["variant"],
            r["version"],
            r["revision"],
            r["drivetrain_sequence"],
        )
    )

    hfields = list(homologation[0].keys()) if homologation else []
    write_csv(out / "rdw_r85_homologation.csv", homologation, hfields)

    review_rows = [r for r in homologation if r["power_30min_quality_flag"] != "ok"]
    if review_rows:
        write_csv(
            out / "rdw_r85_review_rows.csv",
            review_rows,
            list(review_rows[0].keys()),
        )
    else:
        write_csv(out / "rdw_r85_review_rows.csv", [], hfields)

    groups = defaultdict(list)
    group_fields = [
        "brand_make",
        "powertrain_class",
        "registered_vehicle_type",
        "eu_vehicle_category",
        "manufacturer_type",
        "commercial_name",
        "name_join_status",
        "drivetrain_join_status",
        "brand_join_status",
        "rdw_max_net_power_min_kw",
        "rdw_max_net_power_max_kw",
        "power_30min_min_kw",
        "power_30min_max_kw",
        "power_30min_exact_kw",
        "power_30min_quality_flag",
        "power_30min_quality_note",
        "power_30min_zero_source_flag",
        "wltp_bev_range_min_km",
        "wltp_bev_range_max_km",
        "wltp_phev_range_min_km",
        "wltp_phev_range_max_km",
        "motor_codes",
        "motor_fuel_type_codes",
    ]
    for r in homologation:
        groups[tuple(r[f] for f in group_fields)].append(r)

    model = []
    for gkey, rows in groups.items():
        base = dict(zip(group_fields, gkey))
        approvals = sorted({r["type_approval_number"] for r in rows})

        # Registration count is approval-level, so count each approval only once.
        registration_count_by_approval: dict[str, int] = {}
        for r in rows:
            approval = r["type_approval_number"]
            registration_count_by_approval[approval] = max(
                registration_count_by_approval.get(approval, 0),
                intnum(r.get("rdw_registration_count_approval")),
            )

        base.update(
            {
                "brand_make_aliases": " | ".join(
                    sorted(
                        {
                            alias
                            for r in rows
                            for alias in r["brand_make_aliases"].split(" | ")
                            if alias
                        }
                    )
                ),
                "homologation_row_count": len(rows),
                "rdw_registration_count": sum(registration_count_by_approval.values()),
                "rdw_first_admission_date": min(
                    (r["rdw_first_admission_date"] for r in rows if r["rdw_first_admission_date"]),
                    default="",
                ),
                "rdw_latest_admission_date": max(
                    (r["rdw_latest_admission_date"] for r in rows if r["rdw_latest_admission_date"]),
                    default="",
                ),
                "ready_mass_min_kg": fmt_number(
                    min(
                        (num(r["ready_mass_min_kg"]) for r in rows if num(r["ready_mass_min_kg"]) is not None),
                        default=None,
                    )
                ),
                "ready_mass_max_kg": fmt_number(
                    max(
                        (num(r["ready_mass_max_kg"]) for r in rows if num(r["ready_mass_max_kg"]) is not None),
                        default=None,
                    )
                ),
                "max_design_speed_min_kmh": fmt_number(
                    min(
                        (num(r["max_design_speed_min_kmh"]) for r in rows if num(r["max_design_speed_min_kmh"]) is not None),
                        default=None,
                    )
                ),
                "max_design_speed_max_kmh": fmt_number(
                    max(
                        (num(r["max_design_speed_max_kmh"]) for r in rows if num(r["max_design_speed_max_kmh"]) is not None),
                        default=None,
                    )
                ),
                "seats_min": fmt_number(
                    min((num(r["seats_min"]) for r in rows if num(r["seats_min"]) is not None), default=None)
                ),
                "seats_max": fmt_number(
                    max((num(r["seats_max"]) for r in rows if num(r["seats_max"]) is not None), default=None)
                ),
                "length_min_mm": fmt_number(
                    min((num(r["length_min_mm"]) for r in rows if num(r["length_min_mm"]) is not None), default=None)
                ),
                "length_max_mm": fmt_number(
                    max((num(r["length_max_mm"]) for r in rows if num(r["length_max_mm"]) is not None), default=None)
                ),
                "width_min_mm": fmt_number(
                    min((num(r["width_min_mm"]) for r in rows if num(r["width_min_mm"]) is not None), default=None)
                ),
                "width_max_mm": fmt_number(
                    max((num(r["width_max_mm"]) for r in rows if num(r["width_max_mm"]) is not None), default=None)
                ),
                "height_min_mm": fmt_number(
                    min((num(r["height_min_mm"]) for r in rows if num(r["height_min_mm"]) is not None), default=None)
                ),
                "height_max_mm": fmt_number(
                    max((num(r["height_max_mm"]) for r in rows if num(r["height_max_mm"]) is not None), default=None)
                ),
                "type_approval_numbers": " | ".join(approvals),
                "variants": " | ".join(sorted({r["variant"] for r in rows})),
                "versions": " | ".join(sorted({r["version"] for r in rows})),
                "revisions": " | ".join(sorted({r["revision"] for r in rows})),
            }
        )
        model.append(base)

    model.sort(
        key=lambda r: (
            r["brand_make"],
            r["commercial_name"],
            r["manufacturer_type"],
            r["power_30min_min_kw"],
        )
    )
    mfields = list(model[0].keys()) if model else []
    write_csv(out / "rdw_r85_model_view.csv", model, mfields)
    create_sqlite(out / "rdw_r85.sqlite", homologation, model, review_rows)

    manifest["row_counts"] = {
        "energy_source_rows": len(energy),
        "drivetrain_source_rows": drive_meta["total_rows"],
        "name_source_rows": name_meta["total_rows"],
        "registration_enrichment_source_rows": reg_meta["total_rows"],
        "homologation_rows": len(homologation),
        "model_view_rows": len(model),
        "bev_homologation_rows": sum(r["powertrain_class"] == "BEV" for r in homologation),
        "phev_homologation_rows": sum(r["powertrain_class"] == "PHEV" for r in homologation),
        "exact_30min_rows": sum(bool(r["power_30min_exact_kw"]) for r in homologation),
        "ranged_30min_rows": sum(
            bool(
                r["power_30min_min_kw"]
                and r["power_30min_max_kw"]
                and r["power_30min_min_kw"] != r["power_30min_max_kw"]
            )
            for r in homologation
        ),
        "named_homologation_rows": sum(bool(r["commercial_name"] or r["manufacturer_type"]) for r in homologation),
        "drivetrain_evidence_rows": sum(bool(r["drivetrain_indicator_evidence"]) for r in homologation),
        "brand_enriched_homologation_rows": sum(bool(r["brand_make"]) for r in homologation),
        "vehicle_category_enriched_rows": sum(bool(r["eu_vehicle_category"]) for r in homologation),
        "power_review_rows": len(review_rows),
        "zero_power_source_rows": sum(bool(r["power_30min_zero_source_flag"]) for r in homologation),
    }

    # Source-null diagnostics make expected blanks explicit.
    fields_to_check = [
        "brand_make",
        "registered_vehicle_type",
        "eu_vehicle_category",
        "manufacturer_type",
        "commercial_name",
        "rdw_max_net_power_min_kw",
        "rdw_max_net_power_max_kw",
        "power_30min_min_kw",
        "power_30min_max_kw",
        "power_30min_exact_kw",
        "wltp_bev_range_min_km",
        "wltp_bev_range_max_km",
        "wltp_phev_range_min_km",
        "wltp_phev_range_max_km",
        "drivetrain_indicator_evidence",
    ]
    manifest["blank_counts"] = {
        field: sum(not bool(r[field]) for r in homologation) for field in fields_to_check
    }

    if review_rows:
        manifest["warnings"].append(
            f"{len(review_rows)} homologation rows have source-level 30-minute-power values "
            "that meet the review rules. Values were preserved verbatim; see rdw_r85_review_rows.csv."
        )

    for p in [
        out / "rdw_r85_homologation.csv",
        out / "rdw_r85_model_view.csv",
        out / "rdw_r85_review_rows.csv",
        out / "rdw_r85.sqlite",
    ]:
        manifest.setdefault("outputs", {})[p.name] = {
            "bytes": p.stat().st_size,
            "sha256": sha256_file(p),
        }

    with (out / "source_manifest.json").open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print("\nJoin diagnostics:")
    print(json.dumps(manifest["join_diagnostics"], indent=2))
    print("\nRow counts:")
    print(json.dumps(manifest["row_counts"], indent=2))
    if manifest["warnings"]:
        print("\nWarnings:")
        for warning in manifest["warnings"]:
            print(f"  - {warning}")
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
