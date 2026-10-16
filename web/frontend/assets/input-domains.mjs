// Defaults mirror build_input_intervals_from_range_bound in the engine.
export function defaultInterval(type, bound = 200) {
  if (type === "int") return { min: -bound, max: bound };
  if (type === "char") return { min: 0, max: Math.min(bound, 65535) };
  return null;
}

export function intervalConfig(parameters, values, { bound = 200, explicit = false } = {}) {
  const intervals = {};
  let changed = false;
  for (const { name, type } of parameters) {
    const defaults = defaultInterval(type, bound);
    if (!defaults) continue;
    const value = values[name] || defaults;
    const min = value.min === "" ? NaN : Number(value.min);
    const max = value.max === "" ? NaN : Number(value.max);
    const low = type === "char" ? 0 : -2147483648;
    const high = type === "char" ? 65535 : 2147483647;
    if (!Number.isInteger(min) || !Number.isInteger(max) || min > max || min < low || max > high) {
      throw new Error(`${name}: enter integer bounds with ${low} ≤ minimum ≤ maximum ≤ ${high}.`);
    }
    intervals[name] = { min, max };
    changed ||= min !== defaults.min || max !== defaults.max;
  }
  if (!changed && !explicit) return { range_bound: bound };
  return { input_intervals: intervals };
}

export function domainSummary(config = {}) {
  if (config.mode === "original") return "CSC: standard Java input domains.";
  if (config.input_intervals != null) {
    const text = Object.entries(config.input_intervals)
      .map(([name, value]) => `${name} ∈ [${value.min}, ${value.max}]`).join("; ");
    return `${config.restrict_input_domain ? "Legacy restricted run" : "Boundary expansion intervals"}: ${text || "no int or char inputs"}.`;
  }
  const bound = config.range_bound ?? 200;
  return `Default Boundary expansion rules: int [${-bound}, ${bound}]; char [0, ${Math.min(bound, 65535)}]; other types retain their standard domains.`;
}
