# Source Behaviour

## SEEK

Status: card-only extraction implemented.

Mechanics:
- query encoded in SEEK search path;
- `daterange=7` and listed-date ordering supported;
- one page currently exposes about 32 cards in a Human MCP snapshot;
- pagination uses `page=N`;
- collector validates source job link order against repeated card blocks;
- explicit terminal state `No matching search results` ends the query cleanly;
- transient empty renders are polled until a valid card structure or a bounded failure.

Captured card data currently includes title, employer, source ID, URL, posted text, employment type, location, work arrangement, visible salary, teaser, classification/subclassification, selected card tags and raw card evidence.

Individual JDs are not opened.

## LinkedIn

Status: card-only extraction and resumable offset collection implemented.

Mechanics:
- the search list is virtualised and exposes only about seven cards in one snapshot;
- arbitrary `start=N` offsets produce different card windows;
- collector advances by the number of cards parsed rather than assuming 25-card pages;
- cursor is persisted by source/query/location so long result sets can resume across invocations;
- source result count is treated as a hint because LinkedIn can change it between requests.

Captured card data currently includes title, employer, source ID, URL, location/work arrangement, visible card metadata/tags and raw card evidence.

Individual JDs are not opened.

## APSJobs

Status: registry entries exist; neutral collector not yet implemented.

Do not mark APSJobs coverage complete until its card/result parser and exhaustion rules are implemented and measured.

## Additional sources

New sources should be added when they materially increase coverage or reach roles not well represented on SEEK/LinkedIn. Every new source needs:
- card-only extraction where technically possible;
- stable identity strategy;
- explicit exhaustion/completion rule;
- parser fixture/regression tests;
- source behaviour documented here;
- no policy filtering in the collector.
