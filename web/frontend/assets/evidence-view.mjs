/** Read-only, keyboard-operable navigation over recorded evidence links. */
export function provenanceText(upstream = []) {
  return upstream.map((entry) => {
    const stage = entry.stage === 'csc' ? 'Upstream exploration' : 'Upstream verification';
    if (entry.state !== 'ready') return `${stage}: unavailable — ${entry.reason || 'Conclusion not recorded.'}`;
    const result = entry.conclusion;
    const reasons = result.reasonCodes?.length ? ` (${result.reasonCodes.join(', ')})` : '';
    return `${stage}: ${result.status} / ${result.completeness}${reasons}. This is separate from the current stage’s conclusion.`;
  });
}

const GROUPS = { lines: 'Ranked source lines' };

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

export class EvidenceView {
  constructor(panel, onPath = () => {}, onIndex = () => {}) {
    this.panel = panel;
    this.onPath = onPath;
    this.onIndex = onIndex;
    this.generation = 0;
    this.detailGeneration = 0;
  }

  clear() {
    this.selectedDetail = null;
    this.generation++;
    this.detailGeneration++;
    this.controller?.abort();
    this.detailController?.abort();
    this.panel.replaceChildren();
    this.panel.classList.add('hidden');
    this.onIndex(null);
  }

  async load(job) {
    this.clear();
    if (job.kind !== 'localization' || job.status !== 'succeeded') return;
    const generation = this.generation;
    this.jobId = job.id;
    this.controller = new AbortController();
    this.panel.classList.remove('hidden');
    const title = element('h3', 'Diagnostic evidence');
    this.notice = element('p', 'Loading recorded candidate links…');
    this.notice.setAttribute('role', 'status');
    this.panel.append(title, this.notice);
    try {
      const response = await fetch(`/api/jobs/${job.id}/evidence`, { signal: this.controller.signal });
      if (!response.ok) throw new Error(response.status === 404
        ? 'No linked evidence is available for this result.'
        : 'The recorded evidence has invalid or broken links. Other analysis results remain available.');
      const data = await response.json();
      if (generation !== this.generation) return;
      if (data.jobId !== job.id) throw new Error('Evidence belongs to another job.');
      this.data = data;
      this.onIndex(data);
      this.notice.className = 'evidence-notice';
      this.notice.textContent = `${data.candidates.length} source lines · ${data.failures.length} failed ${data.failures.length === 1 ? 'check' : 'checks'}. Scores rank suspicion; they do not prove a fault.`;
      this.renderCandidates();
    } catch (error) {
      if (generation === this.generation) this.notice.textContent = error.message;
    }
  }

  renderCandidates() {
    const layout = element('div', undefined, 'evidence-layout');
    const candidates = element('div', undefined, 'evidence-candidates');
    candidates.setAttribute('aria-label', 'Ranked diagnostic candidates');
    for (const group of Object.keys(GROUPS)) {
      const rows = this.data.candidates.filter((c) => c.group === group);
      if (!rows.length) continue;
      const header = element('div', undefined, 'evidence-list-header');
      header.append(element('span', 'Rank'), element('h4', GROUPS[group]),
        element('span', { ochiai: 'Ochiai', dstar: 'DStar', op2: 'Op2' }[this.data.method] || this.data.method));
      candidates.append(header);
      for (const candidate of rows) {
        const button = element('button', undefined, 'evidence-candidate');
        button.type = 'button';
        button.setAttribute('aria-pressed', 'false');
        button.setAttribute('aria-label', `Candidate #${candidate.rank}, line ${candidate.line || '—'}: ${candidate.condition}. ${candidate.method} ${candidate.score.toFixed(4)}${candidate.tieCount > 1 ? `, tied (${candidate.tieCount})` : ''}`);
        const content = element('span', undefined, 'evidence-candidate-content');
        const meta = element('span', undefined, 'evidence-candidate-meta');
        meta.append(element('span', `Line ${candidate.line || '—'}`, 'evidence-candidate-line'));
        if (candidate.tieCount > 1) meta.append(element('span', `Tied (${candidate.tieCount})`, 'evidence-candidate-tie'));
        content.append(meta, element('code', candidate.condition, 'evidence-candidate-code'));
        button.append(element('span', `#${candidate.rank}`, 'evidence-candidate-rank'), content,
          element('span', candidate.score.toFixed(4), 'evidence-candidate-score'));
        button.addEventListener('click', () => {
          candidates.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', String(b === button)));
          this.selectCandidate(candidate);
        });
        candidates.append(button);
      }
    }
    this.detail = element('div', undefined, 'evidence-detail');
    this.detail.classList.add('is-empty');
    const empty = element('div', undefined, 'evidence-empty');
    const icon = element('span', '</>', 'evidence-empty-icon');
    icon.setAttribute('aria-hidden', 'true');
    empty.append(icon, element('h4', 'Inspect a source line'),
      element('p', 'Select a ranked line to view its supporting failed checks, source and condition path.'));
    this.detail.append(empty);
    layout.append(candidates, this.detail);
    this.panel.append(layout);
  }

  selectCandidate(candidate) {
    this.selectedDetail = null;
    this.detailGeneration++;
    this.detailController?.abort();
    this.detail.replaceChildren();
    this.detail.classList.remove('is-empty');
    const heading = element('h4', `Line ${candidate.line || '—'} · Candidate #${candidate.rank}`);
    heading.tabIndex = -1;
    this.detail.append(heading);
    this.detail.append(element('code', candidate.condition, 'evidence-detail-condition'));
    this.detail.append(element('p', `${candidate.method} spectrum: ef=${candidate.spectrum.a_ef}, ep=${candidate.spectrum.a_ep}, nf=${candidate.spectrum.a_nf}, np=${candidate.spectrum.a_np}`, 'evidence-spectrum'));
    heading.focus({ preventScroll: true });
    if (!candidate.failureIds.length) {
      this.detail.append(element('p', candidate.warning || 'No linked failure evidence.'));
      return;
    }
    const label = element('label', `${candidate.failureIds.length} supporting failed ${candidate.failureIds.length === 1 ? 'check' : 'checks'} `);
    const select = element('select');
    select.setAttribute('aria-label', 'Supporting failed check');
    const failures = new Map(this.data.failures.map((f) => [f.id, f]));
    candidate.failureIds.forEach((id) => {
      const f = failures.get(id);
      const option = element('option', `${f.testId} · ${f.fsfId}`);
      option.value = id;
      select.append(option);
    });
    this.body = element('div');
    select.addEventListener('change', () => this.loadDetail(candidate, select.value));
    label.append(select);
    this.detail.append(label, this.body);
    this.loadDetail(candidate, candidate.failureIds[0]);
  }

  async loadDetail(candidate, failureId) {
    this.selectedDetail = null;
    const generation = this.generation;
    const detailGeneration = ++this.detailGeneration;
    this.detailController?.abort();
    const controller = this.detailController = new AbortController();
    const body = this.body;
    body.replaceChildren(element('p', 'Loading source and path…'));
    try {
      const response = await fetch(`/api/jobs/${this.jobId}/evidence/${candidate.id}/${failureId}`, { signal: controller.signal });
      if (!response.ok) throw new Error('The selected evidence could not be loaded.');
      const data = await response.json();
      if (generation !== this.generation || detailGeneration !== this.detailGeneration) return;
      if (data.jobId !== this.jobId || data.candidateId !== candidate.id || data.failureId !== failureId) throw new Error('Evidence identity mismatch.');
      this.selectedDetail = data;
      this.renderDetail(body, data);
      this.onPath(data);
      this.notice.textContent = `Showing ${data.testId} / ${data.verification.fsf_id} supporting candidate #${candidate.rank}.`;
    } catch (error) {
      if (generation === this.generation && detailGeneration === this.detailGeneration) body.replaceChildren(element('p', error.message));
    }
  }

  renderDetail(body, data) {
    body.replaceChildren();
    body.append(element('h5', `Failed check · ${data.testId} · ${data.verification.fsf_id}`));
    body.append(element('p', `Generated test inputs: ${JSON.stringify(data.inputs)}`));
    body.append(element('p', `Solver counterexample (not automatically replayed): ${JSON.stringify(data.verification.counterexample)}`));
    const spec = element('pre', `Scenario: ${data.verification.T}\nRequired behavior: ${data.verification.D}\nPath constraint: ${data.verification.Ct_in}\nReason: ${data.verification.reason}`, 'evidence-code');
    spec.tabIndex = 0;
    body.append(spec);
    body.append(element('h5', `Source · ${data.source.filename}`));
    const source = element('pre', undefined, 'evidence-code evidence-source');
    source.tabIndex = 0;
    source.setAttribute('aria-label', 'Source code with candidate lines marked');
    data.source.lines.forEach((line, i) => {
      const highlight = data.source.highlightLines.includes(i + 1);
      source.append(element('span', `${highlight ? '▶' : ' '} ${String(i + 1).padStart(4)}  ${line}`, highlight ? 'evidence-highlight' : ''));
    });
    body.append(source);
    const firstMarked = data.source.highlightLines[0];
    if (firstMarked) source.scrollTop = Math.max(0, (source.children[firstMarked - 1]?.offsetTop || 0) - 60);
    body.append(element('h5', 'Condition path · linked to the CCT'));
    const path = element('ol', undefined, 'evidence-path');
    for (const step of data.path) {
      path.append(element('li', `L${step.line}: ${step.expression} → ${step.outcome ? 'True' : 'False'} [${step.nodeId}]`));
    }
    body.append(path);
    const trace = element('details');
    trace.append(element('summary', `Recorded execution trace · ${data.trace.length} events`));
    trace.append(element('pre', data.traceWarning || data.trace.map((event) => JSON.stringify(event)).join('\n'), 'evidence-code'));
    body.append(trace);
    const refs = element('details');
    refs.append(element('summary', 'Recorded evidence references'));
    refs.append(element('pre', data.references.map((r) => `${r.jobId.slice(0, 8)} · ${r.artifact} · ${r.pointer || r.nodeId}`).join('\n'), 'evidence-code'));
    body.append(refs);
  }
}
