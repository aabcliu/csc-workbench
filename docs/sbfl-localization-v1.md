# SBFL Localization v1

`sbfl.localization.v1.json` is the public fault-localization artifact produced
after Refined TBFV. It ranks source lines from per-test execution coverage and
the recorded verification verdicts.

## Supported methods

- `ochiai`: `a_ef / sqrt((a_ef + a_nf) * (a_ef + a_ep))`
- `dstar`: `a_ef^2 / (a_ep + a_nf)`; a zero denominator with `a_ef > 0` uses
  the finite sentinel `1,000,000`
- `op2`: `a_ef - a_ep / (total_passed + 1)`

Here `a_ef` and `a_ep` count failing and passing tests that execute the line;
`a_nf` and `a_np` count failing and passing tests that do not execute it. A
test is failing when any applicable FSF check fails. It is passing when at
least one check passes and none fail. Tests containing no pass or fail result
are listed under `excludedTests` and do not contribute to the spectrum.

Scores are rounded to eight decimal places. Ranking is descending by score,
with source line number as the deterministic tie order. Equal scores share a
competition rank and report `tieGroupSize`.

## CCT projection

`cctOverlay` is separate from `cct.snapshot.v1`. A condition node receives the
score for its source line; outgoing branch edges inherit that source-line score
for display. The overlay always retains the selected method, raw score, rank,
and passing/failing coverage counts. UI color intensity is rank-relative and
must not be used to compare raw scores across different formulas.

The CCT remains a visualization and provenance surface. It does not change the
source-line program entities or the SBFL formulas.
