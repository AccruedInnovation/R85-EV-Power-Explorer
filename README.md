# RDW R85 EV Power Explorer

A standalone browser for exploring **UN Regulation No. 85 maximum 30-minute power** data for electric-drive vehicles found in the Dutch RDW open-data system.

The dashboard is intended to make an obscure but useful EV power rating easier to inspect, compare, filter, and audit.

> **Dashboard:** `rdw_r85_dashboard_v2.html`  
> **Data snapshot:** 2026-09-12  
> **Collector:** `collect_rdw_r85.py v1.3`

## Why this exists

EV specifications usually emphasize **peak power**: the highest short-duration output a vehicle can produce under suitable conditions. That number is useful, but it does not describe how much power the electric drive can sustain for a much longer period.

UN Regulation No. 85 defines a separate electric-drive rating: **maximum 30-minute power**. It provides a standardized, regulated measure of power over a 30-minute test.

The distinction can be large. A high-performance EV may advertise several hundred kilowatts of peak output while carrying a much lower 30-minute rating in European approval or registration data.

This project was inspired by the InsideEVs article:

**“Your EV Has Two Horsepower Ratings. Automakers Only Advertise One”**  
https://insideevs.com/news/806789/peak-versus-sustained-power-evs/

That article highlighted how different advertised peak output can be from the regulated 30-minute figure and prompted an attempt to collect the latter at useful scale.

## What is UN R85 maximum 30-minute power?

UN Regulation No. 85 covers measurement of engine net power and electric-drive performance for relevant vehicle classes. For electric drives, it includes a **maximum 30-minute power** measurement.

Regulation text:

https://unece.org/sites/default/files/2025-03/R85r1am7e.pdf

The 30-minute rating should not be read as:

- the vehicle's advertised peak power;
- the exact power available after driving hard for 30 minutes;
- a guarantee of indefinite continuous output;
- a direct measure of acceleration;
- a complete prediction of track, towing, Autobahn, or hill-climb performance.

It is a **standardized regulatory measurement under defined test conditions**.

Real-world sustained output can also depend on battery state of charge, cell temperature, motor and inverter temperature, cooling capacity, vehicle speed, control limits, ambient conditions, and other factors.

Even with those limits, the R85 figure is valuable because it gives a common reference point that peak-power marketing figures do not provide.

## Why the 30-minute number matters

Peak power and longer-duration power answer different questions.

Peak output is relevant to short acceleration events. The R85 30-minute rating gives more information about the electric drive's ability to operate at high output for a longer period under a standardized test.

That makes the number interesting when comparing:

- thermal design;
- motor and inverter sizing;
- cooling capacity;
- performance-oriented EVs;
- vehicles with similar advertised peak output but different sustained capability;
- different versions of the same vehicle family;
- regulatory ratings against manufacturer marketing claims.

A low R85-to-peak ratio does **not** by itself mean that a vehicle has poor performance. Likewise, a high ratio does not prove superior real-world performance. The value is one additional piece of technical evidence.

The current RDW dataset generally does **not** provide a reliable advertised whole-vehicle peak-power field, so this dashboard focuses on the R85 side of the comparison.

## Data provenance

The dashboard is derived from public **RDW Open Data** from the Netherlands.

RDW is the Dutch vehicle and driving-licence authority. The source datasets are published through RDW's Socrata open-data service.

The collector uses bulk or server-side grouped queries and caches the results locally. It does not crawl individual licence plates.

### 1. R85 power and energy-source data

**RDW dataset:** TGK Energiebron Uitvoering  
**Dataset ID:** `gr7t-qfnb`

https://opendata.rdw.nl/Typegoedkeuring/Open-Data-RDW-TGK-Energiebron-Uitvoering/gr7t-qfnb

This is the primary technical source.

Important fields include:

- `typegoedkeuringsnummer` — type-approval number;
- `codevarianttgk` — variant;
- `codeuitvoeringtgk` — version;
- `volgnummerrevisieuitvoering` — approval/version revision;
- `volgnummeraandrijving` — drivetrain sequence;
- `volgnummerenergiebron` — energy-source sequence;
- `maximumvermogen30minogr` — lower bound of maximum 30-minute power;
- `maximumvermogen30minbgr` — upper bound of maximum 30-minute power;
- electric-range fields where available.

The collection process selects electric energy-source records with a populated 30-minute-power value.

### 2. Drivetrain classification

**RDW dataset:** TGK Aandrijving Uitvoering  
**Dataset ID:** `4by9-ammk`

https://opendata.rdw.nl/Typegoedkeuring/Open-Data-RDW-TGK-Aandrijving-Uitvoering/4by9-ammk

This table supplies drivetrain indicators used to classify records as BEV, PHEV, HEV, or another/unknown class where possible.

It also supplies useful drivetrain evidence such as motor codes.

### 3. Manufacturer commercial names and type designations

**RDW dataset:** TGK Handelsbenaming Fabrikant  
**Dataset ID:** `x5v3-sewk`

https://opendata.rdw.nl/Typegoedkeuring/Open-Data-RDW-TGK-Handelsbenaming-Fabrikant/x5v3-sewk

This source adds:

- manufacturer-assigned commercial name;
- manufacturer type designation.

The field shown as **Manufacturer type** in the dashboard is a type designation. It is **not** the legal manufacturer name.

### 4. Brand/make and Dutch registration context

**RDW dataset:** Gekentekende voertuigen  
**Dataset ID:** `m9d7-ebf2`

https://opendata.rdw.nl/en/en/Vehicles/Open-Data-RDW-Gekentekende_voertuigen/m9d7-ebf2

This source adds registration-side context through a server-side grouped query, including:

- `merk` — public-facing make/brand;
- vehicle type;
- EU vehicle category;
- Dutch registration count;
- first and latest admission dates;
- ready-to-drive mass range;
- seat-count range;
- vehicle dimensions;
- maximum design-speed range.

RDW lists this dataset as **CC0 / public domain**.

The dashboard labels `merk` as **Brand / make**. It should not be assumed to identify the legal corporate manufacturer in every case.

## Snapshot statistics

The supplied dashboard snapshot contains:

| Measure | Count |
| --- | ---: |
| Raw R85 energy-source rows | 189,673 |
| Homologation rows | 189,673 |
| Grouped model-view rows | 13,812 |
| BEV homologation rows | 56,197 |
| PHEV homologation rows | 20,070 |
| Rows with an exact 30-minute value | 188,905 |
| Rows with a 30-minute range | 763 |
| Commercial-name matches | 189,630 |
| Drivetrain matches | 189,671 |
| Brand/make enriched rows | 163,287 |
| Source-quality review rows | 90 |
| Rows containing a zero R85 source value | 40,621 |

Join coverage in this snapshot:

- **Commercial name:** 99.977%
- **Drivetrain:** 99.999%
- **Brand/make registration enrichment:** 86.089%

The lower brand/make coverage is expected because brand enrichment depends on finding a corresponding record in the current Dutch registered-vehicle dataset. A type approval can exist in the type-approval data without a matching currently registered Dutch vehicle.

## Dashboard views

### Model view

The default view groups technically identical or equivalent homologation rows into a more manageable model-oriented table.

It is the best place to start when browsing brands and commercial names.

The grouping is conservative. Underlying type-approval identifiers remain available in the record details.

### Homologation view

This exposes the full **189,673-row** technical dataset.

Use it when you need to inspect:

- approval number;
- variant;
- version;
- revision;
- drivetrain sequence;
- energy-source sequence;
- individual source bounds;
- exact join evidence.

This is the better view for auditing the source or investigating why two apparently similar vehicles have different ratings.

### Review anomalies

This contains records that triggered a source-quality rule.

The dashboard does **not** silently fix, scale, discard, or replace suspicious RDW values.

For example, the RDW source contains some 30-minute-power values such as:

- 6,800 kW;
- 6,200 kW;
- other values well above plausible passenger-EV outputs.

Those numbers are already present in the RDW source data. They were not created by the collection or join process.

The collector therefore:

1. preserves the source value;
2. marks the row for review;
3. records why it was flagged;
4. leaves any correction to a later evidence-based process.

The default high-value review threshold for this snapshot is **1,000 kW**.

This threshold is a data-quality screening rule, not a statement that a vehicle above 1,000 kW is impossible.

## Zero values

The source also contains a large number of records with a zero 30-minute-power value.

These remain in the dataset because a zero can have several possible causes and the collector does not have enough evidence to replace it automatically.

The dashboard provides a **Non-zero only** filter for analysis where zero records would distort the result.

Do not assume every zero means that the vehicle literally has zero sustained propulsion power.

## Lower and upper bounds

RDW can represent some type-approval values as a lower and upper bound.

The dashboard therefore retains:

- `power_30min_min_kw`;
- `power_30min_max_kw`.

It populates:

- `power_30min_exact_kw`

only when the lower and upper values are equal.

The collector does **not** average a range or invent a single midpoint.

## Brand and naming caveats

Three naming concepts appear in the dataset:

### Brand / make

Derived from RDW registration field `merk`.

Examples can include names such as BMW, TESLA, AUDI, or VOLKSWAGEN.

Coverage is about 86% in this snapshot because the join requires a corresponding Dutch registration-side approval.

### Commercial name

Derived from the type-approval manufacturer commercial-name table.

This has almost complete coverage.

### Manufacturer type

Derived from RDW `typeaanduidingfabrikant`.

This is the manufacturer's **type designation**, not its corporate name. Values can therefore look like internal codes rather than familiar marques.

## Dutch registration counts

Registration counts are included only as context.

They can help answer questions such as:

- whether a type approval appears to correspond to vehicles actually registered in the Netherlands;
- which approval variants are common or rare in the Dutch fleet;
- whether an unusual technical row is tied to many or few registered vehicles.

They are **not**:

- European sales totals;
- global production figures;
- current active-fleet counts with perfect historical meaning;
- a measure of popularity outside the Netherlands.

## Vehicle classes

The source is broader than passenger BEVs.

It can contain:

- BEVs;
- PHEVs;
- HEVs;
- passenger vehicles;
- commercial vehicles;
- other relevant EU vehicle categories.

For ordinary passenger-EV research, the dashboard includes an **M1 BEVs** preset.

Filtering by EU vehicle category is important when comparing power values because the full homologation dataset is not limited to conventional passenger cars.

## Useful filters and presets

The dashboard can filter by:

- brand/make;
- commercial name through search;
- EU vehicle category;
- powertrain class;
- minimum and maximum R85 power;
- exact-value availability;
- zero/non-zero source value;
- source-quality status;
- WLTP electric range;
- mass;
- Dutch registration match;
- commercial-name join;
- drivetrain join.

Included presets:

- **M1 BEVs**
- **M1 BEV/PHEV**
- **Hide anomalies**
- **Registered only**

## Charts

The dashboard includes:

### R85 exact-power distribution

A histogram of exact 30-minute-power values in the current filtered set.

The display caps the chart scale at the 98th percentile so a small number of extreme source values do not make the rest of the distribution unreadable.

This affects only the chart display. It does not alter the underlying records.

### Top brands

Shows the most common brands in the current filtered result.

The count is a row count for the selected dashboard view, not a sales ranking.

## CSV export

**Export filtered CSV** exports the rows currently selected by the dashboard filters.

This is useful for taking a focused subset into Python, R, Excel, DuckDB, SQLite, or another analysis tool.

The export uses the underlying source values, including review flags.

## What the dataset is good for

Reasonable uses include:

- finding R85 30-minute ratings that are otherwise difficult to locate;
- comparing homologated versions of an EV;
- identifying large differences within a vehicle family;
- building a list for later comparison with advertised peak power;
- studying the distribution of 30-minute ratings by vehicle class;
- finding suspicious approval records for manual verification;
- exploring how R85 values relate to mass, range, or vehicle category;
- creating a reproducible starting point for sustained-power research.

## What the dataset is not good for without more work

Do not use the dashboard alone to claim:

- actual real-world continuous power;
- guaranteed track power after 30 minutes;
- actual towing or hill-climb performance;
- advertised peak power;
- battery-limited power at a given state of charge;
- legal manufacturer identity;
- European or worldwide sales;
- exact current fleet population outside the RDW snapshot;
- that every source value is correct.

Any important outlier should be checked against the vehicle's Certificate of Conformity, approval documents, registration documentation, manufacturer data, or another authoritative source.

## Peak power is currently a missing half of the comparison

The strongest use of the R85 number is often to compare it with a vehicle's advertised peak output.

That comparison is **not yet complete in this dataset**.

RDW contains a `maximum net power` field in the type-approval energy-source data, but it is extremely sparse for the electric records in this snapshot and should not be assumed to equal the advertised whole-vehicle system peak rating.

The collector therefore retains it under neutral names:

- `rdw_max_net_power_min_kw`;
- `rdw_max_net_power_max_kw`.

It does not label those values as advertised peak EV power.

A future dataset could add verified manufacturer peak-power figures as a separate source and calculate measures such as:

```text
R85 sustained ratio = R85 30-minute power / advertised peak power
```

Such a ratio could be useful, but only if both input values refer to the same vehicle configuration and are sourced correctly.

## Data-quality policy

The collection process follows several rules:

1. **Preserve source values.**
2. **Do not infer decimal corrections.**
3. **Do not average regulatory ranges.**
4. **Flag suspicious values instead of deleting them.**
5. **Keep homologation identifiers so records remain auditable.**
6. **Distinguish source data from enrichment data.**
7. **Report join coverage.**
8. **Cache source downloads and process joins locally.**
9. **Use bulk and server-side grouped queries rather than plate-by-plate crawling.**

This approach is deliberate. A strange value that can be traced to RDW is more useful than a plausible-looking value that was silently changed.

## Data collection approach

The collector was designed to place little load on RDW's public service.

It uses:

- cached bulk source files;
- very large sequential pages for the few tables that require paging;
- server-side filtering;
- server-side grouping for registration enrichment;
- local joins and analysis;
- request pacing;
- retry/backoff handling;
- `Retry-After` where supplied;
- no individual licence-plate crawl.

Once source data is cached, the dataset can be rebuilt and reanalyzed without making new RDW requests.

## Reproducibility

The generated data package includes:

- `rdw_r85_homologation.csv` — full technical view;
- `rdw_r85_model_view.csv` — grouped browsing view;
- `rdw_r85_review_rows.csv` — source-quality review subset;
- `rdw_r85.sqlite` — SQLite form of the processed data;
- `source_manifest.json` — source, count, join, warning, and output metadata;
- raw cached RDW source files/pages.

The dashboard embeds its snapshot directly in the HTML and makes no network requests when opened.

For research where provenance matters, use the SQLite/CSV files and `source_manifest.json` alongside the dashboard.

## Licensing and attribution

RDW publishes relevant open-data sources under open/public-data terms; the registered-vehicle dataset used for enrichment is listed by RDW as **CC0 / public domain**.

Check the metadata page for each source dataset if redistributing a derived dataset.

This dashboard is a derived research tool and is **not an official RDW or UNECE product**.

## Primary references

### UN Regulation No. 85

UNECE — Regulation No. 85:

https://unece.org/sites/default/files/2025-03/R85r1am7e.pdf

### RDW Open Data

TGK Energiebron Uitvoering:

https://opendata.rdw.nl/Typegoedkeuring/Open-Data-RDW-TGK-Energiebron-Uitvoering/gr7t-qfnb

TGK Aandrijving Uitvoering:

https://opendata.rdw.nl/Typegoedkeuring/Open-Data-RDW-TGK-Aandrijving-Uitvoering/4by9-ammk

TGK Handelsbenaming Fabrikant:

https://opendata.rdw.nl/Typegoedkeuring/Open-Data-RDW-TGK-Handelsbenaming-Fabrikant/x5v3-sewk

Gekentekende voertuigen:

https://opendata.rdw.nl/en/en/Vehicles/Open-Data-RDW-Gekentekende_voertuigen/m9d7-ebf2

### Inspiration

InsideEVs — **“Your EV Has Two Horsepower Ratings. Automakers Only Advertise One”**:

https://insideevs.com/news/806789/peak-versus-sustained-power-evs/

The article drew attention to the large gap that can exist between advertised EV peak power and the UN R85 30-minute rating. This project was created to make the latter easier to inspect across a much larger set of approval data.

## Suggested next steps

Useful extensions would include:

- add verified advertised peak-power data from manufacturer or CoC sources;
- calculate peak-to-R85 ratios where vehicle identity can be matched with high confidence;
- manually investigate high-value and zero-value RDW anomalies;
- add model-year or approval-extension timelines;
- improve legal-manufacturer enrichment separately from public brand/make;
- add other European type-approval or registration sources for cross-checking;
- track changes between periodic RDW snapshots;
- publish a cleaned subset whose corrections are backed by independent evidence.

---

This dataset should be treated as an **auditable research index into regulatory data**, not as a corrected master specification database. Its main value is that it exposes a useful EV rating that is normally hard to find while retaining enough source identity to check unusual records.
