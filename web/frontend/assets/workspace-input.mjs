export function javaCodeOnly(source) {
  return source
    .replace(/"""[\s\S]*?"""|\/\*[\s\S]*?\*\/|\/\/[^\n]*/g, " ")
    .replace(/"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'/g, " ");
}

export function detectJavaClassName(source) {
  const codeOnly = javaCodeOnly(source);
  const publicClass = codeOnly.match(
    /\bpublic\s+(?:(?:abstract|final|sealed|non-sealed|strictfp)\s+)*class\s+([A-Za-z_][A-Za-z0-9_]*)\b/,
  );
  const anyClass = codeOnly.match(
    /\b(?:(?:abstract|final|sealed|non-sealed|strictfp)\s+)*class\s+([A-Za-z_][A-Za-z0-9_]*)\b/,
  );
  return (publicClass || anyClass)?.[1] || null;
}

export function extractJavaInputParameters(source) {
  const codeOnly = javaCodeOnly(source);
  const methodPattern = /\b(?:public|protected|private)\s+((?:(?:static|final|synchronized|native|abstract|strictfp)\s+)*)([A-Za-z_$][\w$<>\[\].?]*)\s+([A-Za-z_$][\w$]*)\s*\(([^()]*)\)/g;
  const candidates = [];
  let match;

  while ((match = methodPattern.exec(codeOnly)) !== null) {
    const [, modifiers, returnType, methodName, parameterSource] = match;
    if (methodName === "main") continue;
    candidates.push({
      isStatic: /\bstatic\b/.test(modifiers),
      methodName,
      returnType,
      parameters: parseJavaParameters(parameterSource),
    });
  }

  const analysisMethod = candidates.find((candidate) => candidate.isStatic) || candidates[0];
  return analysisMethod?.parameters || [];
}

function parseJavaParameters(parameterSource) {
  if (!parameterSource.trim()) return [];
  return splitTopLevel(parameterSource).flatMap((parameter) => {
    const cleaned = parameter
      .replace(/@[A-Za-z_$][\w$]*(?:\s*\([^)]*\))?\s*/g, "")
      .replace(/\bfinal\s+/g, "")
      .trim();
    const nameMatch = cleaned.match(/([A-Za-z_$][\w$]*)\s*(?:\[\s*\])?\s*$/);
    if (!nameMatch) return [];
    const name = nameMatch[1];
    const type = cleaned.slice(0, nameMatch.index).trim().replace(/\.\.\.$/, "…");
    return type ? [{ name, type }] : [];
  });
}

function splitTopLevel(source) {
  const parts = [];
  let start = 0;
  let angleDepth = 0;
  let squareDepth = 0;
  for (let index = 0; index < source.length; index += 1) {
    const character = source[index];
    if (character === "<") angleDepth += 1;
    if (character === ">") angleDepth = Math.max(0, angleDepth - 1);
    if (character === "[") squareDepth += 1;
    if (character === "]") squareDepth = Math.max(0, squareDepth - 1);
    if (character === "," && angleDepth === 0 && squareDepth === 0) {
      parts.push(source.slice(start, index));
      start = index + 1;
    }
  }
  parts.push(source.slice(start));
  return parts;
}

export function serializeFSFUnits(units) {
  return units
    .map(({ t, d }, index) => `[fsf_${index + 1}]\nT: ${t.trim()}\nD: ${d.trim()}`)
    .join("\n\n");
}

export function parseFSFUnits(source) {
  const units = [];
  let current = { t: null, d: null };

  const flush = () => {
    if (current.t === null && current.d === null) return;
    if (current.t === null || current.d === null) {
      throw new Error("Each FSF unit must contain both T and D");
    }
    units.push({ t: current.t.trim() || "true", d: current.d.trim() || "true" });
    current = { t: null, d: null };
  };

  source.split(/\r?\n/).forEach((rawLine) => {
    const line = rawLine.split("#", 1)[0].trim();
    if (!line) return;
    if (line.startsWith("[") && line.endsWith("]")) {
      flush();
      return;
    }
    if (line.startsWith("T:")) {
      if (current.t !== null && current.d !== null) flush();
      if (current.t !== null) throw new Error("Duplicate T before D");
      current.t = line.slice(2).trim();
      return;
    }
    if (line.startsWith("D:")) {
      if (current.d !== null) throw new Error("Duplicate D in one FSF unit");
      current.d = line.slice(2).trim();
      return;
    }
    throw new Error(`Unsupported FSF line: ${rawLine.trim()}`);
  });
  flush();
  if (!units.length) throw new Error("The FSF file does not contain any T/D pairs");
  return units;
}
