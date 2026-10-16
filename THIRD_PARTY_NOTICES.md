# Third-party notices

The MIT license in `LICENSE` applies to original CSC Workbench code. Third-party
components keep their own copyrights and licenses.

## Bundled browser dependency

- **D3 7.9.0** — ISC license; Copyright 2010–2023 Mike Bostock.
  Source: https://github.com/d3/d3/tree/v7.9.0.
  The unmodified minified distribution and full license are in
  `web/frontend/assets/vendor/d3.v7.9.0.min.js` and `d3.LICENSE`.

## Java bridge dependency

- **JavaParser Core 3.25.1** — available under Apache-2.0 or LGPL-3.0-or-later;
  this distribution uses the Apache-2.0 option.
  Copyright (C) 2007-2010 Júlio Vilmar Gesser; Copyright (C) 2011, 2013-2023 The JavaParser Team.
  Source: https://github.com/javaparser/javaparser/tree/javaparser-parent-3.25.1.
  Maven downloads the unmodified dependency when building the bridge. The bridge's
  fat JAR bundles it. Full license text: `licenses/Apache-2.0.txt`.

## Experimental benchmark programs

The release includes the 42-subject EX_CSC input cohort in `benchmarks/EX_CSC`.
Its Java and FSF inputs retain the experimental bytes and line numbers.
`subjects.json` identifies the source inspiration, adaptation, and license of
each subject. Mutants inherit the original subject's upstream license.

- **TheAlgorithms/Java** — MIT; Copyright (c) 2021 The Algorithms.
  Fourteen benchmark subjects adapt its number-theory, divisor, and bit-counting
  algorithms to bounded scalar inputs. Helpers are inlined and return values are
  changed to compact classifications. Full copyright and permission notice:
  `licenses/TheAlgorithms-MIT.txt`.
  Source: https://github.com/TheAlgorithms/Java.
- **Google Guava IntMath** — Apache-2.0; Copyright (C) 2011 The Guava Authors.
  `BoundedIntPower` and `SaturatedIntPower` adapt the `pow` and `saturatedPow`
  algorithm families. They replace upstream control flow with bounded scalar
  multiplication loops, remove helpers and exceptions, and add mode/cap branches.
  Source: https://github.com/google/guava/blob/master/guava/src/com/google/common/math/IntMath.java.
- **Apache Commons Numbers ArithmeticUtils** — Apache-2.0.
  `BoundedIntPower` also draws on its integer-power algorithm family, with the
  benchmark adaptations described above. This product includes software
  developed at The Apache Software Foundation (https://www.apache.org/).
  Source: https://github.com/apache/commons-numbers/blob/master/commons-numbers-core/src/main/java/org/apache/commons/numbers/core/ArithmeticUtils.java.
  Full license for these two adapted Java subject families:
  `licenses/Apache-2.0.txt`.

The constructed subjects, original FSFs, and benchmark mutation edits are CSC
Workbench contributions under `LICENSE`; upstream subject code retains its own
license. These kernels are adapted benchmark inputs, not upstream library APIs.

## Installed Python and system dependencies

Python dependencies are recorded in `deploy/requirements.lock`; their original
license files remain in the container's installed distributions. These include
Z3 (MIT), FastAPI (MIT), Uvicorn (BSD-3-Clause), Starlette (BSD-3-Clause), Pydantic
(MIT), and python-multipart (Apache-2.0). Transitive dependencies retain their
upstream license files as well.

The container also includes Python, OpenJDK 17, Graphviz, and Debian packages.
Their license and copyright notices are retained by the image, including those
under `/usr/share/doc/`. Maven and its builder image are used only during the
build and are not part of the runtime image.
