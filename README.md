# CSC Workbench

**Explore Java executions, check behavioral specifications, and investigate
suspicious source lines in one local browser application.**

CSC Workbench connects test generation, specification checking, and fault
localization. Start with a Java class and an FSF specification, then follow a
suspicious source line back to the input, failed check, and execution path that
support it through an interactive Condition Case Tree (CCT).

All analysis runs locally. The release includes a guided AddLoop example and
the complete experimental dataset for trying the workflow.

[Quick start](#quick-start) · [Usage](#usage) ·
[Experimental dataset](#try-the-experimental-dataset) ·
[Service commands](#manage-the-local-service) ·
[Troubleshooting](#troubleshooting) · [Documentation](#documentation-and-source)

## Quick start

You need a running **Docker Engine with Docker Compose v2** and a browser with
JavaScript enabled. On Linux, Docker Engine with the Compose plugin is
sufficient. Docker Desktop provides these on macOS and Windows; use Linux
containers on Windows. Compose must support `up --wait`.

Python, Java, Maven, Z3, and Graphviz are included in the container. Start Docker
before running either deployment command below.

### 1. Use a prebuilt package (recommended)

Download the ZIP for your machine from the release assets:

| Your machine | Package |
| --- | --- |
| Intel or AMD x86-64, including Intel Macs | `csc-workbench-0.1.0-linux-amd64.zip` |
| Apple Silicon or ARM64 | `csc-workbench-0.1.0-linux-arm64.zip` |

The prebuilt ZIP includes the startup files and the bundled container image.

Extract the ZIP and open a terminal in its `csc-workbench` directory. Start the
workbench with one command:

**macOS / Linux**

```bash
./start.sh
```

**Windows PowerShell**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

The launcher loads the bundled image, starts the service, and waits for the
dependency health check. When it prints:

```text
CSC Workbench is ready: http://127.0.0.1:8765
```

open **http://127.0.0.1:8765** and continue with [Usage](#usage). Once Docker
and the package are installed, this method needs no network access.

### 2. Build and deploy from source

Download `csc-workbench-0.1.0-source.zip` or clone the tool repository. Open a
terminal in the source directory and run:

**macOS / Linux**

```bash
./start.sh build
```

**Windows PowerShell**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 build
```

This builds the Java bridge and installs the pinned Python dependencies inside
Docker, then starts the same application. The first build needs internet access
for base images, OS packages, Maven, and Python packages. Later builds reuse
Docker's cache. Repeat `build` after changing the source; use the command from
method 1 to restart an existing compatible image.

## Usage

Start with the included example, compare programs from the experimental
dataset, then try your own Java and FSF inputs.

### Run the included AddLoop example

AddLoop should return `x + y` for inputs in `[-100, 100]` and `-1` for invalid
inputs. Its positive loop contains an intentional fault: for `(x, y) = (100, 99)`,
the implementation returns `200` while the specification requires `199`.

1. Choose **Add program** → **Load example**. AddLoop is selected by default;
   its Java source and FSF specification appear in the editable input fields.
2. Choose **Use this program** to create a workspace.
3. Choose **Generate Tests**. Keep **CSC + Boundary**, **Batch**, the default
   integer intervals `[-200, 200]`, and **Max iterations = 30**. Enter
   `x=100,y=99` in **Bootstrap** to include the fault-revealing input, then
   confirm **Generate Tests**.
4. After generation finishes, choose **Run Verification**, select the generated
   test result, and confirm. The workbench checks the recorded paths against
   the FSF scenarios and marks failures on the CCT.
5. Choose **Locate Faults**, keep **Ochiai**, and confirm to rank source lines
   using verification labels and recorded coverage. See
   [Follow a candidate to its evidence](#follow-a-candidate-to-its-evidence)
   for the source-level explanation.

The validated example workflow produces these analysis conclusions:

| Stage | Expected conclusion | What to inspect |
| --- | --- | --- |
| Test generation | `completed` / `complete` | Generated inputs and CCT paths |
| Verification | `fail` / `complete` | The failed `fsf_2` check requiring `199` |
| Fault localization | `available` / `complete` | Ranked source lines and supporting failures |

**A verification verdict of `fail` is the expected result for this faulty
example.** It means the analysis found a specification violation. Job execution
status and analysis conclusions are displayed separately. Smaller exploration
budgets can produce a `partial` generation result.

### Try the experimental dataset

Both deployment packages include [the EX_CSC dataset](benchmarks/EX_CSC/README.md)
in `benchmarks/EX_CSC/`. It contains the paper's complete input cohort: 42
original Java programs, six single-line mutants per program (252 in total), and
one FSF specification per subject. Start with this comparison:

| Program to upload | FSF to upload | Expected observation |
| --- | --- | --- |
| [Original AddLoop](benchmarks/EX_CSC/AddLoop/AddLoop.java) | [AddLoop_FSF.txt](benchmarks/EX_CSC/AddLoop/FSF/AddLoop_FSF.txt) | Verification passes |
| [AddLoop_M1](benchmarks/EX_CSC/AddLoop/AddLoop_M1.java) | The same `AddLoop_FSF.txt` | Verification fails; investigate the boundary check at line 3 |

For each row, choose **Add program** → **Upload files**, select the Java and FSF
files, and create a separate workspace. Keep **CSC + Boundary**, **Batch**,
integer intervals `[-200, 200]`, **Max iterations = 30**, and **Workers = 4**.
Set **Bootstrap** to `x=-100,y=1`, generate tests, and run verification.
For this input, the original returns `-99` and M1 returns `-1`. On M1's failed
verification, run **Locate Faults** with **Ochiai** and inspect its evidence.
The dataset's original AddLoop differs from the intentionally faulty built-in
walkthrough example.

Next, try [BoundedPrimeClassifier](benchmarks/EX_CSC/BoundedPrimeClassifier/)
and its variants with their shared FSF. Consult the
[dataset guide](benchmarks/EX_CSC/README.md) and
[subject inventory](benchmarks/EX_CSC/subjects.json) for all paired inputs,
recommended settings, mutation metadata, and source provenance. Mutants retain
the original public class name; the workbench handles this when importing them.

Some mutants are equivalent under their specifications or need exploration
beyond the selected interval. Results can also be `unsupported`, `unknown`, or
`partial`; inspect their reason codes. The dataset does not promise that every
mutant will produce a failed verification.

### Analyze your own program

Choose **Add program** to paste or upload a Java class and its FSF specification.
In **Paste code**, edit the Java input and add the specification as T/D pairs,
or use **Import .txt**. In **Upload files**, select a `.java` file and an FSF
text file ending in `.txt`. To upload the bundled `AddLoop.fsf` this way, copy
it to `AddLoop_FSF.txt` first; its contents do not change. **Load example**
fills both inputs directly.

Use a static target method with `int`/`boolean` inputs and an `int` result.
See [Supported inputs and results](#supported-inputs-and-results) for the
validated program scope and interpretation of analysis conclusions.

An FSF specification contains named scenarios. `T` defines when a scenario
applies; `D` defines the required result, using `return_value` for the return
value. For example:

```ini
[positive_sum]
T: x >= -100 && x <= 100 && y > 0 && y <= 100
D: return_value == x + y
```

See the [FSF input guide](docs/fsf-format.md) and the paired
[AddLoop inputs](examples/AddLoop.fsf). Boundary expansion supports `int` and
`char` intervals, with defaults `int: [-200, 200]` and `char: [0, 200]`. These
intervals control additional exploration; bootstrap inputs and ordinary CSC
tests can lie outside them.

## Manage the local service

Run these commands from the same package directory. Workspaces and analysis
results remain in `runtime/` when you stop the service.

**macOS / Linux**

```bash
./start.sh stop
./start.sh status
./start.sh logs
```

**Windows PowerShell**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 stop
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 status
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 logs
```

To use a different local port:

```bash
CSC_PORT=8766 ./start.sh
```

```powershell
$env:CSC_PORT = '8766'
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

Then open `http://127.0.0.1:8766`. The service listens on the local loopback
interface.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Docker is unavailable | Start Docker and check that `docker info` succeeds and `docker compose version` reports Compose v2. |
| Compose reports an unknown `--wait` option | Update Docker Compose v2, then retry. |
| macOS / Linux reports `Permission denied` for `start.sh` | Run `bash ./start.sh` (or `bash ./start.sh build` for method 2). |
| No prebuilt image is available | Use a prebuilt ZIP, or explicitly run method 2. The default launcher loads an image or reuses a compatible installed image. |
| Image architecture does not match | Download the ZIP matching your Docker environment's architecture. |
| Port `8765` is already in use | Choose another port with `CSC_PORT`, as shown above. |
| Startup health check fails | Read the logs printed by the launcher, or run the `logs` command. Startup returns an unsuccessful exit code. |
| Verification reports `fail` for AddLoop | Continue to localization; the example deliberately contains a fault. |
| An analysis reports `partial`, `unknown`, or `unsupported` | Inspect its reason codes and evidence before interpreting the result; check the input scope and exploration budget. |

## Analysis workflow

The workbench provides:

- **Test generation:** Condition Sequence Coverage (CSC), with optional Boundary
  expansion over configurable finite intervals.
- **Specification checking:** refined testing-based formal verification (TBFV)
  of recorded paths against FSF scenarios.
- **Fault investigation:** source-line rankings with Ochiai, DStar (e=2), or Op2,
  linked to failed checks, inputs, traces, and source context.
- **Saved analyses:** portable sessions that restore the results and their
  supporting records for inspection without rerunning the analysis.

### Follow a candidate to its evidence

Select a ranked source line to inspect its supporting failed checks. Follow the
check for `(100, 99)` to its FSF scenario, recorded path, and source context. In
[AddLoop.java](examples/AddLoop.java), inspect the positive-loop guard:

```java
while (n > 0 || sum == 199) {
```

After 99 iterations, `n > 0` is false but `sum == 199` remains true. The guard
allows one extra iteration and produces `200`. The ranking identifies lines to
inspect; the linked records help explain the failure. Equal scores can leave
several lines tied at the same rank.

### Save and reopen the analysis

Open **Import history** to find saved runs. Use **Export portable session** on
the localization record to download a `.csc-session.zip` archive containing its
supporting generation and verification records. Use **Import archive** to
restore it in another local installation. Restored results are read-only;
**Start New Run** performs a fresh analysis.

## Supported inputs and results

The validated workflow targets deterministic computational methods with a
static target method, `int`/`boolean` inputs, an `int` result, scalar state, and
`if`/`while` control flow. The container compiles and executes the supplied Java
program.

The symbolic model does not cover heap aliasing, array or field updates,
unrecorded method effects, exceptions, reflection, concurrency, or Java IEEE-754
semantics. Switch and enhanced-for branches appear as coverage exclusions.
Unsupported, unknown, and incomplete results remain visible. A `complete`
exploration result refers to discovered obligations under the selected
configuration; source-line rankings prioritize inspection.

Run only trusted Java inputs, as they execute inside the local container with
access to its analysis data.

## Check the deployment

### Verify downloads

The prebuilt ZIP contains both the startup files and the image at
`images/csc-workbench.tar`. GitHub's automatically generated **Source code** ZIP
contains source only; choose one of the named prebuilt packages for prebuilt
deployment.

`amd64` is the name of the x86-64 architecture used by both Intel and AMD CPUs.
`linux` describes the container's operating system; both packages can run on
macOS or Windows through Docker's Linux container environment.

The release includes `SHA256SUMS`. To verify downloaded files, place them with
that file in one directory and run `shasum -a 256 -c SHA256SUMS` on macOS or
`sha256sum -c SHA256SUMS` on Linux. Missing attachments are reported as missing;
download all listed attachments to check the entire release. PowerShell users
can compare `Get-FileHash .\csc-workbench-0.1.0-linux-amd64.zip -Algorithm SHA256`
with the matching entry. Each prebuilt ZIP also includes `images/SHA256SUMS`
for its bundled image.

Standalone image `.tar` assets can also be used with the source package. Place
the matching image at `images/csc-workbench.tar`, or set `CSC_IMAGE_ARCHIVE` to
its path. The launcher checks the image architecture before starting.

### Run the automated workflow check

For an automated check of the included workflow, run this after startup from the
package directory:

```bash
docker compose --project-name csc-workbench -f deploy/compose.yaml exec -T csc-workbench \
  python /opt/csc/deploy/smoke_check.py
```

The check uses the container's Python, creates one AddLoop workspace, and runs
generation, verification, and localization. It verifies that the intentional
fault is detected and that ranking evidence is available. A successful check
prints a JSON report with `"status": "passed"`. Its records remain in `runtime/`.
If you set `CSC_PROJECT_NAME`, use that name in place of `csc-workbench` after
`--project-name`.

Release deployment checks covered native ARM64 Linux containers on macOS Docker
Desktop and AMD64 containers through Docker's emulation. Prebuilt deployment
and source deployment were exercised with the included example workflow. The
PowerShell launcher was checked in PowerShell 7.4; Windows host deployment has
not been tested directly.

## Documentation and source

| Resource | Contents |
| --- | --- |
| [FSF input guide](docs/fsf-format.md) | Scenario syntax and supported expressions |
| [Localization reference](docs/sbfl-localization-v1.md) | Scoring formulas, test labels, ties, and CCT overlays |
| [Example Java](examples/AddLoop.java) and [FSF](examples/AddLoop.fsf) | Complete walkthrough inputs |
| [Experimental dataset](benchmarks/EX_CSC/README.md) | 42 originals, 252 mutants, 42 FSFs, and recommended comparisons |
| [Result schemas](schemas/) | Versioned analysis outcome, metric, and CCT contracts |
| [Third-party notices](THIRD_PARTY_NOTICES.md) | Dependency licenses |

```text
engine/       Analysis engine and Java instrumentation
web/          FastAPI backend and browser frontend
examples/     AddLoop Java and FSF inputs
benchmarks/   Experimental programs, paired FSFs, and mutation metadata
schemas/      Versioned result contracts
deploy/       Container files, dependency lock, and smoke check
licenses/     Third-party license texts
start.sh      macOS / Linux launcher
start.ps1     Windows PowerShell launcher
```

Use method 2 to build and deploy source changes. Maintainers can create image
archives with `./deploy/export-image.sh linux/arm64 /path/to/arm64.tar` or
`./deploy/export-image.sh linux/amd64 /path/to/amd64.tar`. These build and save the
`csc-workbench:0.1.0` image used by the launcher. Cross-platform builds require
Docker's emulation support.

## License

Original CSC Workbench code is licensed under [MIT](LICENSE). Adapted benchmark
programs and bundled or installed dependencies retain their own licenses; see the
[third-party notices](THIRD_PARTY_NOTICES.md).
