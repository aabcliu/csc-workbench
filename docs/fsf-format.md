# FSF input format

An FSF file contains one or more named scenarios. Each scenario has a trigger
condition (`T`) and an expected result condition (`D`). Use parameter names from
the target Java method; use `return_value` for its returned scalar result.

```ini
[fsf_1]
T: x >= -100 && x <= 100 && y >= -100 && y <= 100
D: return_value == x + y

[fsf_2]
T: x < -100 || x > 100 || y < -100 || y > 100
D: return_value == -1
```

Scalar comparisons, arithmetic, and boolean combinations such as `&&`, `||`, and
`!` are supported within the engine's restricted expression model. A scenario
checks its expected result only when its trigger holds for a generated test.
Use the paired `examples/AddLoop.java` and `examples/AddLoop.fsf` as a starting
point. Unsupported formulas appear in the verification results.
