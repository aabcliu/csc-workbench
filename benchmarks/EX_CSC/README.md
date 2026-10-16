# EX_CSC benchmark inputs

This directory contains the experimental input cohort used to evaluate CSC
Workbench: **42 original Java programs, 252 single-line mutants, and 42 FSF
specifications**. Each subject has three condition mutants and three statement
mutants. These are small scalar programs, including constructed examples and
adaptations of open-source arithmetic and number-theory kernels.

The Java and FSF files preserve the experimental inputs byte for byte, including
source line numbers. The AddLoop program here is the original control; the
separate built-in AddLoop walkthrough intentionally contains a different fault.

## Recommended first comparison

After starting the workbench, use **Add program** → **Upload files**:

1. Upload [AddLoop.java](AddLoop/AddLoop.java) together with
   [AddLoop_FSF.txt](AddLoop/FSF/AddLoop_FSF.txt), then choose **Use this program**.
2. Generate tests with **CSC + Boundary**, **Batch**, integer intervals
   `[-200, 200]`, **Max iterations = 30**, **Workers = 4**, and bootstrap
   `x=-100,y=1`. Run verification. The original should receive `pass`.
3. Create another workspace with [AddLoop_M1.java](AddLoop/AddLoop_M1.java) and
   the **same FSF file**. Repeat those settings and bootstrap. Verification
   should receive `fail`: the mutant returns `-1` for `(-100, 1)` whereas
   `fsf_2` requires `-99`.
4. On the mutant's failed verification, run **Locate Faults** with **Ochiai**.
   Inspect the boundary check at source line 3 and follow its linked evidence.
   Several source lines may share a score; the known mutation is an inspection
   reference, not a promise that its line will rank first.

Next, try the original and mutant programs in
[BoundedPrimeClassifier](BoundedPrimeClassifier/), pairing every variant with
[its original FSF](BoundedPrimeClassifier/FSF/BoundedPrimeClassifier_FSF.txt).
This adds a bounded divisor-counting loop to the workflow. The subject registry
below lists every other available program and its exact paired inputs.

## Files and pairing

```text
EX_CSC/
  README.md
  subjects.json                 All 42 subjects, paths, origins, and licenses
  mutants.jsonl                 252 mutation records with fault-line metadata
  AddLoop/
    AddLoop.java                Original control
    AddLoop_M1.java              Single-line mutant
    ...                         Six mutants per subject
    FSF/AddLoop_FSF.txt          Shared specification for all seven variants
  ...
```

Use [subjects.json](subjects.json) as the complete input inventory. Paths in both
manifests are relative to this directory. Mutant suffixes are identifiers and
need not be consecutive; the two BoundaryStress subjects include `M8`.
Mutants retain the original public Java class name. Upload each Java file in a
separate workspace; the workbench resolves the class name from its contents.
Compiling every variant together with `javac *.java` would create duplicate
classes and is not the intended workflow.

[mutants.jsonl](mutants.jsonl) records the operator, category, original and
mutated source text, one-based line numbers, and paired FSF. Its ground truth
identifies the edited line; it does not assert that every mutant is detectable
or behaviorally different. Original and mutant line numbers can differ: the
SelectionSort variants compact some unchanged swap statements onto one line.
Use each record's separate `original_location` and `mutant_location`, rather
than assuming the same line number in both files. Historical validation fields
are omitted to avoid
presenting old run status as a current result. No generated traces, compiled
classes, evaluation reports, or local analysis sessions are bundled here.

## Analysis settings and interpretation

The paper's workbench cohort used CSC + Boundary, range bound 200, 30 iterations,
batch generation with four workers, and Ochiai localization. CCT image rendering
was disabled for the evaluation, and each analysis job had a 900-second external
timeout. These settings are recorded in `subjects.json`; the browser can be used
to inspect CCTs interactively. The bootstrap above is an explicit demonstration
witness, not an additional claim about the paper's generation configuration.

Start with one subject rather than launching the full cohort. Larger subjects
can take longer. Some mutants are behaviorally equivalent under their FSF;
some faults require inputs outside the configured Boundary interval. In
addition, the symbolic model can return `unsupported`, `unknown`, or `partial`.
Interpret these conclusions using their reason codes. A `pass` does not prove
that a mutant is equivalent, and `complete` describes exploration obligations
under the selected configuration.

## Provenance and licenses

The 26 constructed subjects, their mutants, and the original FSF specifications
are distributed under the repository's [MIT license](../../LICENSE).
Fourteen subjects adapt algorithms from
[TheAlgorithms/Java](https://github.com/TheAlgorithms/Java); retain its
[MIT copyright and permission notice](../../licenses/TheAlgorithms-MIT.txt).

`BoundedIntPower` and `SaturatedIntPower`, including their mutants, adapt power
kernels from [Google Guava](https://github.com/google/guava/blob/master/guava/src/com/google/common/math/IntMath.java).
`BoundedIntPower` also draws on
[Apache Commons Numbers ArithmeticUtils](https://github.com/apache/commons-numbers/blob/master/commons-numbers-core/src/main/java/org/apache/commons/numbers/core/ArithmeticUtils.java).
These adapted Java subjects are distributed under
[Apache-2.0](../../licenses/Apache-2.0.txt). They are modified scalar benchmark
adaptations: inputs are bounded, helper calls and exception-based validation
are removed, and classification or saturation branches are added. They are
not unmodified upstream library implementations.

Per-subject source inspirations and adaptations are listed in `subjects.json`.
See [third-party notices](../../THIRD_PARTY_NOTICES.md) for retained upstream
attribution. The original FSFs and the new mutation edits are CSC Workbench
contributions; upstream code retains the license identified above.
