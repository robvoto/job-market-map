# Job Market Map Discovery

Last updated: 10 September 2026

This file captures architectural discovery for the Job Market Map POC. It is not proof that the described future-state behaviour is implemented.

## Problem being solved

Ownership is becoming blurred between Job Market Map, Job Hunter and JH-305.

If JMM and Job Hunter both store their own canonical copies of the same vacancy/JD, the same SEEK or LinkedIn job can diverge across systems. That creates permanent synchronisation rules and uncertainty about which copy is authoritative.

The target is one owner for each kind of truth.

## Proposed ownership model

### Job Market Map = canonical MARKET truth

JMM owns neutral facts/evidence about the vacancy:

- stable job identity;
- source job IDs and URLs;
- title, employer, location, salary and work type;
- Quick/Easy Apply or equivalent source-visible apply method;
- raw card evidence;
- full JD snapshots when obtained;
- JD versions and capture timestamps;
- source/query provenance;
- market lifecycle;
- conservative cross-source duplicate relationships.

JMM must not contain Rob-specific fit, application decisions or activity outcomes.

### JH-305 = canonical ACTIVITY truth

JH-305 owns chronological personal/user activity such as:

- applied;
- rejected;
- interview;
- no response;
- viewed;
- agent-presented;
- other user/job interaction events.

### Job Hunter = canonical ANALYSIS / DECISION truth

Job Hunter owns Rob-specific interpretation such as:

- fit analysis;
- requirement coverage;
- scores;
- recommendation;
- Potential / Hidden / Liked state where those are decision/workflow state rather than neutral market facts;
- Rob-specific evidence mapping.

## Proposed JD flow

```text
Job Hunter needs to analyse job 123
        |
        v
asks JMM for job 123
        |
        v
JMM already has suitable JD snapshot?
   |                         |
  YES                       NO
   |                         |
   |                 JMM obtains/enriches
   |                 neutral job evidence
   |                         |
   |                 stores JD snapshot/version
   |                         |
   +-------------> returns snapshot
                             |
                             v
                    Job Hunter analyses it
                             |
                             v
                 Job Hunter stores only
                 Rob-specific analysis
```

The important principle is that Job Hunter should not scrape a JD, keep a canonical private copy, and then send another copy back to JMM. If on-demand JD enrichment becomes part of JMM, JMM remains the owner of the neutral evidence even when Job Hunter triggered the request.

## Provenance rule

A Job Hunter analysis must reference the exact JMM JD snapshot/version it analysed.

Conceptually:

```text
job_analysis
  job_id = 123
  jmm_snapshot_id = ABC
  score = 82
```

If the employer changes the JD later, JMM may create snapshot/version 2 while the older analysis still clearly points to version 1.

This prevents a score or requirement assessment from silently appearing to apply to evidence that did not exist when the analysis was performed.

## Existing architecture conflict to resolve

The repository currently documents card-only market mapping and ADR-003 states that full JD opening belongs to a consumer after screening.

The newer design direction above proposes a different split: bulk market mapping stays card-first, but neutral JD acquisition/enrichment may become an on-demand JMM responsibility when requested by a consumer.

That is a real architecture change, not wording cleanup. It must be resolved explicitly before implementation. See `JMM-001` in `docs/BACKLOG.md`.

## Legacy Job Hunter data

Existing Job Hunter records that contain copied `full_description` or equivalent JD content should be treated as legacy/migration debt if the future-state JMM ownership model is adopted.

Do not reproduce that duplication in the new integration simply because the old schema already contains it.

The migration itself belongs on the Job Hunter side; JMM only needs to provide a stable neutral identity and snapshot contract that makes the migration possible.

## Design questions still open

1. What exact operation/API triggers on-demand JMM enrichment?
2. What counts as a sufficiently current JD snapshot versus one that should be refreshed?
3. How do we detect an unchanged JD without creating pointless versions?
4. How does `/v3` expose latest JD evidence versus an exact historical snapshot?
5. What happens when a source no longer exposes the JD but an older snapshot exists?
6. Which source-specific enrichment mechanisms are reliable enough to support without weakening the existing card-collection throughput model?

These are discovery questions, not licence to add speculative implementation.

## Working principle

**JMM owns what the job is. JH-305 owns what happened between Rob and the job. Job Hunter owns what we think about the job.**
