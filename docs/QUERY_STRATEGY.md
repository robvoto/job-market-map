# Neutral Query Strategy

## Objective

The query registry exists to maximise **market discovery coverage**, especially unusual roles that one obvious title search would miss.

It is not a list of jobs Rob is assumed to want.

## Inputs to the registry

Query ideas may come from:
- Job Hunter searches and terminology;
- Reset / Edge searches;
- Plan Z searches;
- successful unusual discoveries;
- employer/product terminology found during research;
- searches that produced useful adjacent titles;
- searches that produced mostly noise, when that noise still reveals alternative terms worth testing.

Using prior search knowledge to design discovery queries does **not** make the collector policy-aware. The collector stores all cards it sees.

## Registry fields

Each query should ultimately record:
- stable query ID/name;
- query text;
- source applicability;
- location/geography parameters;
- active/inactive;
- origin/rationale;
- first/last run;
- cards observed;
- unique new jobs discovered;
- duplicate-hit rate;
- source errors/blocking.

## Coverage philosophy

Overlap is acceptable and useful. If several queries find the same vacancy, store one source job plus several query hits.

Prefer broad/high-recall discovery over clever early exclusion. Filtering belongs to consumers.

We cannot literally index every vacancy on the internet. The operational goal is to exhaust the reachable result space of a deliberately broad registry across the sources we support and continuously expand that registry when new discovery patterns appear.
