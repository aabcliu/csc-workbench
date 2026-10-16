export class CCTViewModelError extends Error {}


function requireRecord(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new CCTViewModelError(`${label} must be an object`);
  }
  return value;
}


function requireArray(value, label) {
  if (!Array.isArray(value)) {
    throw new CCTViewModelError(`${label} must be an array`);
  }
  return value;
}


export function buildCCTViewModel(snapshot) {
  requireRecord(snapshot, "snapshot");
  if (snapshot.format !== "cct.snapshot" || snapshot.version !== 1) {
    throw new CCTViewModelError("unsupported CCT Snapshot format or version");
  }

  const nodeRecords = new Map();
  for (const node of requireArray(snapshot.nodes, "snapshot.nodes")) {
    requireRecord(node, "snapshot node");
    if (typeof node.id !== "string" || !node.id) {
      throw new CCTViewModelError("every snapshot node must have a non-empty id");
    }
    if (nodeRecords.has(node.id)) {
      throw new CCTViewModelError(`duplicate node id: ${node.id}`);
    }
    if (node.kind !== "condition" && node.kind !== "leaf") {
      throw new CCTViewModelError(`unsupported node kind at ${node.id}`);
    }
    nodeRecords.set(node.id, node);
  }
  if (!nodeRecords.has(snapshot.rootNodeId)) {
    throw new CCTViewModelError(`root node does not exist: ${snapshot.rootNodeId}`);
  }

  const outgoing = new Map();
  const edgeRecords = new Map();
  for (const edge of requireArray(snapshot.edges, "snapshot.edges")) {
    requireRecord(edge, "snapshot edge");
    if (typeof edge.id !== "string" || !edge.id) {
      throw new CCTViewModelError("every snapshot edge must have a non-empty id");
    }
    if (edgeRecords.has(edge.id)) {
      throw new CCTViewModelError(`duplicate edge id: ${edge.id}`);
    }
    if (!nodeRecords.has(edge.sourceNodeId)) {
      throw new CCTViewModelError(`edge ${edge.id} has an unknown source`);
    }
    if (typeof edge.outcome !== "boolean") {
      throw new CCTViewModelError(`edge ${edge.id} must have a Boolean outcome`);
    }
    if (edge.targetNodeId !== null && !nodeRecords.has(edge.targetNodeId)) {
      throw new CCTViewModelError(`edge ${edge.id} has an unknown target`);
    }
    const expectedAllocation = edge.targetNodeId === null
      ? "unallocated"
      : "materialized";
    if (edge.allocation !== expectedAllocation) {
      throw new CCTViewModelError(`edge ${edge.id} has inconsistent allocation`);
    }
    edgeRecords.set(edge.id, edge);
    const siblings = outgoing.get(edge.sourceNodeId) || [];
    siblings.push(edge);
    outgoing.set(edge.sourceNodeId, siblings);
  }

  const nodeById = new Map();
  const edgeById = new Map();
  const visiting = new Set();
  const visited = new Set();

  function materialize(nodeId, parentId = null, incomingEdgeId = null) {
    if (visiting.has(nodeId)) {
      throw new CCTViewModelError(`cycle detected at ${nodeId}`);
    }
    if (visited.has(nodeId)) {
      throw new CCTViewModelError(`node ${nodeId} has more than one parent`);
    }
    visiting.add(nodeId);
    visited.add(nodeId);

    const record = nodeRecords.get(nodeId);
    const viewNode = {
      ...record,
      parentId,
      incomingEdgeId,
      children: [],
    };
    nodeById.set(viewNode.id, viewNode);

    const childEdges = [...(outgoing.get(nodeId) || [])]
      .sort((first, second) => Number(first.outcome) - Number(second.outcome));
    if (record.kind === "condition") {
      if (
        childEdges.length !== 2
        || childEdges[0].outcome !== false
        || childEdges[1].outcome !== true
      ) {
        throw new CCTViewModelError(
          `condition node ${nodeId} must have one False and one True edge`,
        );
      }
    } else if (childEdges.length) {
      throw new CCTViewModelError(`leaf node ${nodeId} must not have outgoing edges`);
    }

    for (const edge of childEdges) {
      let child;
      if (edge.targetNodeId === null) {
        child = {
          id: `virtual:${edge.id}`,
          kind: "unallocated",
          virtual: true,
          parentId: nodeId,
          incomingEdgeId: edge.id,
          children: [],
        };
        nodeById.set(child.id, child);
      } else {
        child = materialize(edge.targetNodeId, nodeId, edge.id);
      }
      viewNode.children.push(child);
      edgeById.set(edge.id, {
        ...edge,
        viewTargetId: child.id,
      });
    }

    visiting.delete(nodeId);
    return viewNode;
  }

  const root = materialize(snapshot.rootNodeId);
  const unreachable = [...nodeRecords.keys()].filter((nodeId) => !visited.has(nodeId));
  if (unreachable.length) {
    throw new CCTViewModelError(
      `snapshot contains unreachable nodes: ${unreachable.sort().join(", ")}`,
    );
  }

  return {
    snapshot,
    root,
    nodeById,
    edgeById,
  };
}


export function buildSelectionPath(model, selection) {
  const nodeIds = new Set();
  const edgeIds = new Set();
  const targetId = selection.edgeId
    ? model.edgeById.get(selection.edgeId)?.viewTargetId
    : selection.nodeId;
  let current = targetId ? model.nodeById.get(targetId) : null;
  while (current) {
    nodeIds.add(current.id);
    if (current.incomingEdgeId) edgeIds.add(current.incomingEdgeId);
    current = current.parentId ? model.nodeById.get(current.parentId) : null;
  }
  return { nodeIds, edgeIds };
}


function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}


function prettyJson(value) {
  return escapeHtml(JSON.stringify(value, null, 2));
}


function nodePresentation(node, mode = "verification") {
  if (node.kind === "condition") {
    return {
      className: "condition",
      label: "Condition",
      title: node.condition?.expression || "Condition",
      secondary: `Line ${node.condition?.line ?? "?"} · occurrence ${node.condition?.occurrence ?? "?"}`,
    };
  }
  if (node.kind === "unallocated") {
    return {
      className: "unallocated",
      label: "Unallocated",
      title: "Awaiting branch",
      secondary: "No target node",
    };
  }
  if (mode === "localization") {
    if (node.leafType === "infeasible") {
      return {
        className: "infeasible",
        label: "Infeasible",
        title: "No feasible input",
        secondary: "Terminal branch",
      };
    }
    if (node.leafType === "range_excluded") {
      return {
        className: "range-excluded",
        label: "Range excluded",
        title: "Outside input interval",
        secondary: "Terminal branch",
      };
    }
    const failed = Object.values(node.tbfvFailuresByTestCase || {}).some(
      (items) => Array.isArray(items) && items.length > 0,
    );
    return failed
      ? {
          className: "loc-failed",
          label: "Failed test",
          title: leafTitle(node),
          secondary: "Anchor of localization",
        }
      : {
          className: "loc-passed",
          label: "Passing test",
          title: leafTitle(node),
          secondary: "Passing run",
        };
  }
  const failures = node.tbfvFailuresByTestCase || {};
  if (Object.values(failures).some((items) => items.length > 0)) {
    return {
      className: "failure",
      label: "Failure recorded",
      title: leafTitle(node),
      secondary: "TBFV evidence present",
    };
  }
  if (node.leafType === "infeasible") {
    return {
      className: "infeasible",
      label: "Infeasible",
      title: "No feasible input",
      secondary: "Terminal branch",
    };
  }
  if (node.leafType === "range_excluded") {
    return {
      className: "range-excluded",
      label: "Range excluded",
      title: "Outside input interval",
      secondary: "Terminal branch",
    };
  }
  return {
    className: "covered",
    label: "Covered",
    title: leafTitle(node),
    secondary: "No failure recorded",
  };
}


function leafTitle(node) {
  const tests = node.testCaseIds || [];
  if (tests.length === 1) return tests[0];
  return testCountLabel(tests.length);
}


function testCountLabel(count) {
  return `${count} test case${count === 1 ? "" : "s"}`;
}


function computeRiskTiers(overlay) {
  const scores = new Set();
  for (const group of [overlay?.nodes, overlay?.edges]) {
    for (const entry of Object.values(group || {})) {
      if (Number.isFinite(entry?.score)) scores.add(entry.score);
    }
  }
  const sorted = [...scores].sort((a, b) => a - b);
  if (!sorted.length) return null;
  const quantile = (p) => sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))];
  return { t1: quantile(0.25), t2: quantile(0.5), t3: quantile(0.75) };
}


function riskTier(entry, tiers) {
  if (!entry || !Number.isFinite(entry.score) || !tiers) return null;
  if (entry.score >= tiers.t3) return 4;
  if (entry.score >= tiers.t2) return 3;
  if (entry.score > tiers.t1) return 2;
  return 1;
}

export function formatInputsKeyValue(inputs) {
  if (inputs === undefined || inputs === null) {
    return '<span class="cct-input-empty">not recorded</span>';
  }
  if (typeof inputs === "object" && !Array.isArray(inputs)) {
    const entries = Object.entries(inputs);
    if (!entries.length) {
      return '<span class="cct-input-empty">empty</span>';
    }
    const chips = entries.map(([key, val]) => {
      let valDisplay;
      let valClass = "cct-val-other";
      if (typeof val === "number") {
        valDisplay = String(val);
        valClass = "cct-val-num";
      } else if (typeof val === "boolean") {
        valDisplay = String(val);
        valClass = "cct-val-bool";
      } else if (typeof val === "string") {
        valDisplay = `"${val}"`;
        valClass = "cct-val-str";
      } else {
        valDisplay = JSON.stringify(val);
      }
      return `<div class="cct-input-pair"><span class="cct-input-key">${escapeHtml(key)}</span><span class="cct-input-sep">=</span><span class="cct-input-val ${valClass}">${escapeHtml(valDisplay)}</span></div>`;
    }).join("");
    return `<div class="cct-input-pairs">${chips}</div>`;
  }
  return `<code class="cct-input-val">${escapeHtml(String(inputs))}</code>`;
}

export class CCTTreeView {
  constructor(elements) {
    if (!globalThis.d3) {
      throw new Error("D3 must be loaded before CCTTreeView");
    }
    this.d3 = globalThis.d3;
    this.panel = elements.panel;
    this.svgElement = elements.svg;
    this.worldElement = elements.world;
    this.linksElement = elements.links;
    this.labelsElement = elements.labels;
    this.nodesElement = elements.nodes;
    this.inspectorElement = elements.inspector;
    this.titleElement = elements.title;
    this.subtitleElement = elements.subtitle;
    this.layoutSelect = elements.layoutSelect;
    this.fitButton = elements.fitButton;
    this.zoomInButton = elements.zoomInButton;
    this.zoomOutButton = elements.zoomOutButton;
    this.rootButton = elements.rootButton;
    this.nextFailureButton = elements.nextFailureButton;
    this.failureBadge = elements.failureBadge;

    this.orientation = "TB";
    this.siblingGap = 198;
    this.levelGap = 122;
    this.collapsed = new Set();
    this.selectedNodeId = null;
    this.selectedEdgeId = null;
    this.selectedTestId = null;
    this.edgeOverlays = {};
    this.localizationOverlay = null;
    this.riskTiers = null;
    this.presentationMode = "verification";
    this.workspaceSource = elements.workspaceSource || null;
    this.onOpenSource = elements.onOpenSource || null;
    this.model = null;
    this.latestBounds = null;

    this.svg = this.d3.select(this.svgElement);
    this.world = this.d3.select(this.worldElement);
    this.linksLayer = this.d3.select(this.linksElement);
    this.labelsLayer = this.d3.select(this.labelsElement);
    this.nodesLayer = this.d3.select(this.nodesElement);
    this.zoom = this.d3.zoom()
      .scaleExtent([0.28, 2.2])
      .on("zoom", (event) => this.world.attr("transform", event.transform));
    this.svg.call(this.zoom).on("dblclick.zoom", null);

    this.layoutSelect.addEventListener("change", () => {
      this.orientation = this.layoutSelect.value;
      this.render(true);
    });
    this.fitButton.addEventListener("click", () => this.fitView());
    this.zoomInButton.addEventListener("click", () => {
      this.svg.transition().duration(160).call(this.zoom.scaleBy, 1.25);
    });
    this.zoomOutButton.addEventListener("click", () => {
      this.svg.transition().duration(160).call(this.zoom.scaleBy, 0.8);
    });
    if (this.rootButton) {
      this.rootButton.addEventListener("click", () => this.jumpToRoot());
    }
    if (this.nextFailureButton) {
      this.nextFailureButton.addEventListener("click", () => this.jumpToNextFailure());
    }
    this.resizeObserver = new ResizeObserver(() => {
      if (this.model && !this.panel.classList.contains("hidden")) {
        this.render(true);
      }
    });
    this.resizeObserver.observe(this.svgElement);
  }

  setSnapshot(snapshot, edgeOverlays = {}) {
    this.model = buildCCTViewModel(snapshot);
    this.edgeOverlays = edgeOverlays;
    this.collapsed.clear();
    this.selectedNodeId = this.model.root.id;
    this.selectedEdgeId = null;
    this.selectedTestId = null;
    this.titleElement.textContent = `CCT · ${snapshot.className}`;
    const unallocatedCount = [...this.model.edgeById.values()]
      .filter((edge) => edge.allocation === "unallocated").length;
    this.subtitleElement.textContent = this.isLocalizationMode
      ? "Fault localization view — nodes and edges colored by SBFL risk"
      : [
          `${snapshot.nodes.length} semantic nodes`,
          `${unallocatedCount} unallocated branch${unallocatedCount === 1 ? "" : "es"}`,
          `${this.model.edgeById.size} edges`,
        ].join(" · ");
    this.updateFailureControls();
    this.render(true);
  }

  clear(message = "No CCT snapshot is available for this task.") {
    this.model = null;
    this.latestBounds = null;
    this.linksLayer.selectAll("*").remove();
    this.labelsLayer.selectAll("*").remove();
    this.nodesLayer.selectAll("*").remove();
    this.setPresentationMode("verification");
    this.titleElement.textContent = "CCT result";
    this.subtitleElement.textContent = "Interactive tree view";
    this.inspectorElement.innerHTML = `<p class="cct-empty-state">${escapeHtml(message)}</p>`;
    this.updateFailureControls();
  }

  setEdgeOverlays(edgeOverlays = {}) {
    this.edgeOverlays = edgeOverlays;
    this.renderInspector();
  }

  setWorkspaceSource(source) {
    this.workspaceSource = source || null;
    this.renderInspector();
  }

  setLocalizationOverlay(overlay = null) {
    this.localizationOverlay = overlay;
    this.riskTiers = computeRiskTiers(overlay);
    this.updateRiskLegend();
    if (this.model) this.render(false);
  }

  setPresentationMode(mode = "verification") {
    this.presentationMode = mode === "localization" ? "localization" : "verification";
    this.panel?.classList.toggle("is-localization", this.presentationMode === "localization");
    if (this.model) {
      this.renderInspector();
      this.render(false);
    }
  }

  get isLocalizationMode() {
    return this.presentationMode === "localization";
  }

  riskTier(entry) {
    return riskTier(entry, this.riskTiers);
  }

  updateRiskLegend() {
    const legend = this.panel?.querySelector(".cct-risk-legend");
    if (!legend) return;
    legend.classList.toggle("hidden", !this.riskTiers);
  }

  childrenAccessor(node) {
    if (this.collapsed.has(node.id)) return null;
    return node.children?.length ? node.children : null;
  }

  dimensions(node) {
    if (node.data.kind === "condition") return { width: 180, height: 64 };
    if (node.data.kind === "unallocated") return { width: 132, height: 48 };
    return { width: 158, height: 58 };
  }

  positionNodes(hierarchy) {
    const tree = this.d3.tree().nodeSize([this.siblingGap, this.levelGap]);
    tree(hierarchy);
    hierarchy.each((node) => {
      if (this.orientation === "TB") {
        node.px = node.x;
        node.py = node.y;
      } else {
        node.px = node.y;
        node.py = node.x;
      }
    });
  }

  render(fitAfter = false) {
    if (!this.model) return;
    const hierarchy = this.d3.hierarchy(
      this.model.root,
      (node) => this.childrenAccessor(node),
    );
    this.positionNodes(hierarchy);
    const activePath = buildSelectionPath(this.model, {
      nodeId: this.selectedNodeId,
      edgeId: this.selectedEdgeId,
    });
    const linkGenerator = this.orientation === "TB"
      ? this.d3.linkVertical().x((point) => point.px).y((point) => point.py)
      : this.d3.linkHorizontal().x((point) => point.px).y((point) => point.py);
    const transition = this.d3.transition().duration(
      window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 220,
    );

    const links = this.linksLayer.selectAll("g.cct-link")
      .data(hierarchy.links(), (link) => link.target.data.incomingEdgeId)
      .join(
        (enter) => {
          const group = enter.append("g").attr("class", "cct-link");
          group.append("path").attr("class", "cct-edge");
          group.append("path").attr("class", "cct-edge-hit");
          return group;
        },
        (update) => update,
        (exit) => exit.remove(),
      )
      .on("click", (event, link) => {
        event.stopPropagation();
        this.selectEdge(link.target.data.incomingEdgeId);
      });

    links.select("path.cct-edge")
      .classed("is-active", (link) => activePath.edgeIds.has(link.target.data.incomingEdgeId))
      .classed("is-selected", (link) => this.selectedEdgeId === link.target.data.incomingEdgeId)
      .each((link, index, elements) => {
        const tier = this.riskTier(this.localizationOverlay?.edges?.[link.target.data.incomingEdgeId]);
        const edgePath = this.d3.select(elements[index]);
        for (let level = 1; level <= 4; level += 1) {
          edgePath.classed(`risk-edge-${level}`, tier === level);
        }
      })
      .transition(transition)
      .attr("d", linkGenerator);
    links.select("path.cct-edge-hit")
      .transition(transition)
      .attr("d", linkGenerator);

    this.renderEdgeLabels(hierarchy.links());
    this.renderNodes(hierarchy.descendants(), transition);
    this.updateBounds(hierarchy.descendants());
    this.renderInspector();
    if (fitAfter) requestAnimationFrame(() => this.fitView());
  }

  renderEdgeLabels(links) {
    this.labelsLayer.selectAll("foreignObject")
      .data(links, (link) => link.target.data.incomingEdgeId)
      .join("foreignObject")
      .attr("class", "cct-edge-label-fo")
      .attr("width", 44)
      .attr("height", 44)
      .attr("x", (link) => (
        (link.source.px + link.target.px) / 2 - 22
        + (this.orientation === "TB" ? 0 : 10)
      ))
      .attr("y", (link) => (
        (link.source.py + link.target.py) / 2 - 22
        - (this.orientation === "TB" ? 7 : 0)
      ))
      .each((link, index, elements) => {
        const edge = this.model.edgeById.get(link.target.data.incomingEdgeId);
        const container = this.d3.select(elements[index]);
        container.selectAll("*").remove();
        const wrap = container.append("xhtml:div")
          .attr("class", "cct-edge-button-wrap");
        wrap.append("xhtml:button")
          .attr("type", "button")
          .attr("class", "cct-edge-button")
          .attr(
            "aria-pressed",
            this.selectedEdgeId === link.target.data.incomingEdgeId ? "true" : "false",
          )
          .attr(
            "aria-label",
            `Select ${edge.outcome ? "True" : "False"} branch ${edge.id}`,
          )
          .text(edge.outcome ? "T" : "F")
          .on("click", (event) => {
            event.stopPropagation();
            this.selectEdge(link.target.data.incomingEdgeId);
          });
      });
  }

  renderNodes(nodes, transition) {
    const selection = this.nodesLayer.selectAll("g.cct-node")
      .data(nodes, (node) => node.data.id)
      .join(
        (enter) => {
          const group = enter.append("g").attr("class", "cct-node");
          group.append("foreignObject").attr("class", "cct-node-fo");
          return group;
        },
        (update) => update,
        (exit) => exit.remove(),
      )
      .attr("class", (node) => (
        `cct-node${node.data.kind === "unallocated" ? " cct-unallocated" : ""}`
      ));

    selection.select("foreignObject").each((node, index, elements) => {
      const size = this.dimensions(node);
      const presentation = nodePresentation(node.data, this.presentationMode);
      const container = this.d3.select(elements[index])
        .attr("x", -size.width / 2)
        .attr("y", -size.height / 2)
        .attr("width", size.width)
        .attr("height", size.height + (node.data.kind === "condition" ? 14 : 0));
      container.selectAll("*").remove();
      const box = container.append("xhtml:div")
        .attr(
          "class",
          `cct-node-box is-${presentation.className}${this.selectedNodeId === node.data.id && !this.selectedEdgeId ? " is-selected" : ""}`,
        );
      const risk = this.localizationOverlay?.nodes?.[node.data.id];
      const tier = this.riskTier(risk);
      if (tier) {
        box.classed(`is-risk-${tier}`, true);
      }
      const main = box.append("xhtml:button")
        .attr("type", "button")
        .attr("class", "cct-node-main")
        .attr(
          "aria-pressed",
          this.selectedNodeId === node.data.id && !this.selectedEdgeId ? "true" : "false",
        )
        .attr("aria-label", `${presentation.label}: ${presentation.title}`)
        .on("click", () => this.selectNode(node.data.id));
      main.append("xhtml:span")
        .attr("class", "cct-node-kind")
        .text(presentation.secondary);
      main.append("xhtml:span")
        .attr("class", "cct-node-title")
        .text(presentation.title);

      if (node.data.kind === "condition" && node.data.children?.length) {
        box.append("xhtml:button")
          .attr("type", "button")
          .attr("class", "cct-collapse")
          .attr(
            "aria-label",
            this.collapsed.has(node.data.id) ? "Expand branch" : "Collapse branch",
          )
          .text(this.collapsed.has(node.data.id) ? "+" : "−")
          .on("click", (event) => {
            event.stopPropagation();
            this.toggleCollapse(node.data.id);
          });
      }
    });

    selection.transition(transition)
      .attr("transform", (node) => `translate(${node.px},${node.py})`);
  }

  updateBounds(nodes) {
    this.latestRenderedNodes = nodes;
    const xExtent = this.d3.extent(nodes, (node) => node.px);
    const yExtent = this.d3.extent(nodes, (node) => node.py);
    this.latestBounds = {
      minX: xExtent[0] - 120,
      maxX: xExtent[1] + 120,
      minY: yExtent[0] - 90,
      maxY: yExtent[1] + 90,
    };
  }

  ensureVisible(nodeId) {
    if (!nodeId || !this.model) return false;
    let node = this.model.nodeById.get(nodeId);
    let uncollapsedAny = false;
    while (node?.parentId) {
      if (this.collapsed.has(node.parentId)) {
        this.collapsed.delete(node.parentId);
        uncollapsedAny = true;
      }
      node = this.model.nodeById.get(node.parentId);
    }
    return uncollapsedAny;
  }

  focusNode(nodeId) {
    if (!nodeId || !this.model || !this.svg) return;
    const uncollapsed = this.ensureVisible(nodeId);
    if (uncollapsed) {
      this.render(false);
    }
    const renderedNode = this.latestRenderedNodes?.find((n) => n.data.id === nodeId);
    if (!renderedNode) return;
    const width = this.svgElement.clientWidth || this.svg.node().getBoundingClientRect().width;
    const height = this.svgElement.clientHeight || this.svg.node().getBoundingClientRect().height;
    const isRoot = this.model?.root?.id === nodeId;
    const currentScale = this.d3.zoomTransform(this.svg.node()).k || 1;
    const scale = isRoot ? 1.0 : Math.max(0.65, Math.min(1.2, currentScale));
    const targetY = (this.orientation === "TB" && isRoot) ? 80 : height / 2;
    const targetX = (this.orientation === "LR" && isRoot) ? 100 : width / 2;
    const transform = this.d3.zoomIdentity
      .translate(targetX - renderedNode.px * scale, targetY - renderedNode.py * scale)
      .scale(scale);
    this.svg.transition()
      .duration(window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 350)
      .call(this.zoom.transform, transform);
  }

  jumpToRoot() {
    if (!this.model?.root?.id) return;
    this.selectNode(this.model.root.id);
    this.focusNode(this.model.root.id);
  }

  getFailureNodesInPreOrder() {
    if (!this.model?.root?.id) return [];
    const results = [];
    const traverse = (nodeId) => {
      const node = this.model.nodeById.get(nodeId);
      if (!node) return;
      if (node.kind === "leaf") {
        const failures = node.tbfvFailuresByTestCase || {};
        if (Object.values(failures).some((items) => Array.isArray(items) && items.length > 0)) {
          results.push(node);
        }
      }
      for (const child of node.children || []) {
        traverse(child.id);
      }
    };
    traverse(this.model.root.id);
    return results;
  }

  jumpToNextFailure() {
    const failureNodes = this.getFailureNodesInPreOrder();
    if (!failureNodes.length) return null;
    let nextIndex = 0;
    if (this.selectedNodeId) {
      const currentIndex = failureNodes.findIndex((n) => n.id === this.selectedNodeId);
      if (currentIndex >= 0) {
        nextIndex = (currentIndex + 1) % failureNodes.length;
      }
    }
    const targetNode = failureNodes[nextIndex];
    this.selectNode(targetNode.id);
    this.focusNode(targetNode.id);
    return { targetNode, index: nextIndex, total: failureNodes.length };
  }

  updateFailureControls() {
    if (!this.nextFailureButton) return;
    const failureNodes = this.getFailureNodesInPreOrder();
    const count = failureNodes.length;
    this.nextFailureButton.classList.remove("hidden");
    if (count > 0) {
      this.nextFailureButton.disabled = false;
      this.nextFailureButton.title = `Jump to next failure (${count} failure${count === 1 ? "" : "s"} in CCT)`;
      if (this.failureBadge) {
        this.failureBadge.textContent = count;
        this.failureBadge.className = "failure-count-badge has-failures";
      }
    } else {
      this.nextFailureButton.disabled = true;
      this.nextFailureButton.title = "No verification failures in current CCT";
      if (this.failureBadge) {
        this.failureBadge.textContent = "0";
        this.failureBadge.className = "failure-count-badge is-zero";
      }
    }
  }

  selectNode(nodeId, testId = null) {
    this.ensureVisible(nodeId);
    this.selectedNodeId = nodeId;
    this.selectedEdgeId = null;
    const node = this.model?.nodeById?.get(nodeId);
    this.selectedTestId = node?.testCaseIds?.[0] || null;
    if (node?.kind === "leaf" && node.tbfvFailuresByTestCase) {
      const failingTestId = Object.keys(node.tbfvFailuresByTestCase).find(
        (tid) => Array.isArray(node.tbfvFailuresByTestCase[tid]) && node.tbfvFailuresByTestCase[tid].length > 0
      );
      if (failingTestId) {
        this.selectedTestId = failingTestId;
      }
    }
    if (testId && node?.testCaseIds?.includes(testId)) this.selectedTestId = testId;
    this.render(false);
  }

  selectEdge(edgeId) {
    this.selectedEdgeId = edgeId;
    this.selectedNodeId = null;
    this.selectedTestId = null;
    this.render(false);
  }

  toggleCollapse(nodeId) {
    if (this.collapsed.has(nodeId)) this.collapsed.delete(nodeId);
    else this.collapsed.add(nodeId);
    const selectedTargetId = this.selectedEdgeId
      ? this.model.edgeById.get(this.selectedEdgeId)?.viewTargetId
      : this.selectedNodeId;
    if (!this.isVisible(selectedTargetId)) {
      this.selectedNodeId = nodeId;
      this.selectedEdgeId = null;
      this.selectedTestId = null;
    }
    this.render(true);
  }

  isVisible(nodeId) {
    if (!nodeId) return false;
    let node = this.model.nodeById.get(nodeId);
    while (node?.parentId) {
      if (this.collapsed.has(node.parentId)) return false;
      node = this.model.nodeById.get(node.parentId);
    }
    return true;
  }

  fitView() {
    if (!this.latestBounds) return;
    const width = this.svgElement.clientWidth;
    const height = this.svgElement.clientHeight;
    if (!width || !height) return;
    const contentWidth = Math.max(
      1,
      this.latestBounds.maxX - this.latestBounds.minX,
    );
    const contentHeight = Math.max(
      1,
      this.latestBounds.maxY - this.latestBounds.minY,
    );
    const scale = Math.max(
      0.35,
      Math.min(1.15, width / contentWidth, height / contentHeight),
    );
    const centerX = (this.latestBounds.minX + this.latestBounds.maxX) / 2;
    const centerY = (this.latestBounds.minY + this.latestBounds.maxY) / 2;
    const transform = this.d3.zoomIdentity
      .translate(width / 2 - centerX * scale, height / 2 - centerY * scale)
      .scale(scale);
    this.svg.transition()
      .duration(window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 260)
      .call(this.zoom.transform, transform);
  }

  renderInspector() {
    if (!this.model) return;
    if (this.selectedEdgeId) {
      this.renderEdgeInspector(this.model.edgeById.get(this.selectedEdgeId));
      return;
    }
    const node = this.model.nodeById.get(this.selectedNodeId);
    if (!node) {
      this.inspectorElement.innerHTML = '<p class="cct-empty-state">Select a node or edge.</p>';
      return;
    }
    const presentation = nodePresentation(node, this.presentationMode);
    let titleHtml = escapeHtml(node.id);
    if (node.kind === "leaf") {
      const activeTestId = this.selectedTestId || node.testCaseIds?.[0];
      if (activeTestId) {
        titleHtml += ` <span class="cct-test-id-pill" title="Test case: ${escapeHtml(activeTestId)}">${escapeHtml(activeTestId)}</span>`;
      }
    }
    let html = `<h4>${titleHtml}</h4>`;
    html += `<p class="cct-inspector-summary">${escapeHtml(presentation.title)}</p>`;
    html += `<span class="cct-selection-status is-${presentation.className}">${escapeHtml(presentation.label)}</span>`;

    if (node.kind === "condition") {
      html += '<dl class="cct-detail-fields">';
      html += `<dt>Line</dt><dd>${escapeHtml(node.condition?.line ?? "")}</dd>`;
      html += `<dt>Expression</dt><dd>${escapeHtml(node.condition?.expression ?? "")}</dd>`;
      html += `<dt>Constraint</dt><dd>${escapeHtml(node.condition?.inputConstraint ?? "")}</dd>`;
      html += `<dt>Occurrence</dt><dd>${escapeHtml(node.condition?.occurrence ?? "")}</dd>`;
      html += `<dt>Expanded</dt><dd>${node.expanded ? "true" : "false"}</dd>`;
      html += `<dt>UI state</dt><dd>${this.collapsed.has(node.id) ? "collapsed" : "expanded"}</dd>`;
      html += "</dl>";
      const risk = this.localizationOverlay?.nodes?.[node.id];
      if (risk) {
        html += '<h5>Spectrum-based suspiciousness</h5>';
        html += '<dl class="cct-detail-fields">';
        html += `<dt>Method</dt><dd>${escapeHtml(risk.method)}</dd>`;
        html += `<dt>Rank</dt><dd>${escapeHtml(risk.rank)}</dd>`;
        html += `<dt>Score</dt><dd>${escapeHtml(risk.score)}</dd>`;
        html += `<dt>Coverage</dt><dd>${escapeHtml(risk.failedCovering)} failed / ${escapeHtml(risk.passedCovering)} passed</dd>`;
        html += "</dl>";
      }
      if (this.workspaceSource?.java_source && node.condition?.line != null) {
        html += this.renderSourceSnippet(node.condition.line);
      }
    } else if (node.kind === "unallocated") {
      const edge = this.model.edgeById.get(node.incomingEdgeId);
      html += '<p class="cct-empty-state">This display-only placeholder represents an unallocated branch.</p>';
      html += '<dl class="cct-detail-fields">';
      html += `<dt>Edge</dt><dd>${escapeHtml(edge?.id || "")}</dd>`;
      html += "<dt>Target</dt><dd>null</dd>";
      html += "</dl>";
    } else {
      html += this.renderLeafDetails(node);
    }
    this.inspectorElement.innerHTML = html;
    this.bindTestButtons(node);

    const viewFullBtn = this.inspectorElement.querySelector(".cct-snippet-view-full");
    if (viewFullBtn) {
      viewFullBtn.addEventListener("click", () => {
        const line = Number(viewFullBtn.dataset.line);
        if (this.onOpenSource) {
          this.onOpenSource({ tab: "java", line });
        }
        document.dispatchEvent(new CustomEvent("cct:view-source", { detail: { tab: "java", line } }));
      });
    }
  }

  renderSourceSnippet(targetLine, blockLines = []) {
    const lineNum = Number(targetLine);
    if (Number.isNaN(lineNum) || !this.workspaceSource?.java_source) return "";
    const allLines = this.workspaceSource.java_source.split(/\r?\n/);
    const block = [...new Set(blockLines.map(Number).filter((l) => !Number.isNaN(l) && l > 0))]
      .filter((l) => l !== lineNum)
      .sort((a, b) => a - b);
    // Window: the target line ±2, extended to cover the statement block
    // (capped so large blocks cannot flood the inspector).
    let startLine = Math.max(1, lineNum - 2);
    let endLine = Math.min(allLines.length, lineNum + 2);
    if (block.length) {
      startLine = Math.max(1, Math.min(startLine, block[0]));
      endLine = Math.min(allLines.length, Math.max(endLine, block[block.length - 1]));
      if (endLine - startLine + 1 > 12) {
        endLine = Math.min(allLines.length, startLine + 11);
      }
    }

    let snippetHtml = '<div class="cct-snippet-card">';
    snippetHtml += '<div class="cct-snippet-header">';
    snippetHtml += `<span>Source Context (Line ${escapeHtml(lineNum)})</span>`;
    snippetHtml += `<button type="button" class="cct-snippet-view-full" data-line="${escapeHtml(lineNum)}" title="Open full source code at line ${escapeHtml(lineNum)}">View in Full Code ↗</button>`;
    snippetHtml += '</div>';
    snippetHtml += '<div class="cct-snippet-code">';
    for (let l = startLine; l <= endLine; l++) {
      const isCurrent = l === lineNum;
      const isBlock = !isCurrent && block.includes(l);
      const lineContent = allLines[l - 1] ?? "";
      snippetHtml += `<div class="cct-snippet-line${isCurrent ? " is-current" : isBlock ? " is-block" : ""}">`;
      snippetHtml += `<span class="cct-snippet-marker">${isCurrent ? "▶" : isBlock ? "•" : "&nbsp;"}</span>`;
      snippetHtml += `<span class="cct-snippet-num">${l}</span>`;
      snippetHtml += `<span class="cct-snippet-text">${escapeHtml(lineContent)}</span>`;
      snippetHtml += '</div>';
    }
    snippetHtml += '</div></div>';
    return snippetHtml;
  }

  renderLeafDetails(node) {
    const testCaseIds = node.testCaseIds || [];
    const testId = this.selectedTestId || testCaseIds[0];
    const inputs = node.inputsByTestCase?.[testId];
    const failures = node.tbfvFailuresByTestCase?.[testId] || [];
    const failed = failures.length > 0;

    let switcherHtml = "";
    if (testCaseIds.length > 1) {
      switcherHtml = '<div class="cct-test-switcher"><span class="cct-test-switcher-label">Test cases:</span><div class="cct-test-list">';
      switcherHtml += testCaseIds.map((tid) => (
        `<button type="button" class="cct-test-button" data-cct-test-id="${escapeHtml(tid)}" aria-pressed="${testId === tid}">${escapeHtml(tid)}</button>`
      )).join("");
      switcherHtml += "</div></div>";
    }

    if (this.isLocalizationMode) {
      let html = switcherHtml;
      html += '<dl class="cct-detail-fields">';
      html += `<dt>Leaf type</dt><dd>${escapeHtml(node.leafType)}</dd>`;
      html += `<dt>Input</dt><dd>${formatInputsKeyValue(inputs)}</dd>`;
      html += `<dt>Verification</dt><dd>${failed ? "failed" : "passed"}</dd>`;
      html += "</dl>";
      html += failed
        ? '<p class="cct-empty-state">Failed run — this check is part of the failing spectrum that drives the SBFL ranking.</p>'
        : '<p class="cct-empty-state">Passing run — part of the healthy baseline of the spectrum.</p>';
      return html;
    }

    const remainder = node.truncatedRemaindersByTestCase?.[testId]
      ?? node.truncatedRemainder;

    let html = switcherHtml;
    html += '<dl class="cct-detail-fields">';
    html += `<dt>Leaf type</dt><dd>${escapeHtml(node.leafType)}</dd>`;
    if (testCaseIds.length > 1) {
      html += `<dt>Tests</dt><dd>${testCaseIds.length}</dd>`;
    }
    html += `<dt>Input</dt><dd>${formatInputsKeyValue(inputs)}</dd>`;
    html += `<dt>Remainder</dt><dd>${remainder ? `${remainder.length} condition${remainder.length === 1 ? "" : "s"}` : "none"}</dd>`;
    html += `<dt>Evidence</dt><dd>${failures.length ? `${failures.length} verification failure${failures.length === 1 ? "" : "s"}` : "none recorded"}</dd>`;
    html += `<dt>Expanded</dt><dd>${node.expanded ? "true" : "false"}</dd>`;
    html += "</dl>";

    if (failures.length) {
      html += '<h5>Verification evidence</h5>';
      html += failures.map((failure) => {
        const hasTD = Boolean(failure.T || failure.D);
        const specSummaryHtml = hasTD
          ? (
            `<div class="cct-failure-specs">` +
            `<div class="cct-spec-row"><span class="cct-spec-pill is-trigger" title="Precondition (T)">T</span><span class="cct-spec-text"><code>${escapeHtml(failure.T || "true")}</code></span></div>` +
            `<div class="cct-spec-row"><span class="cct-spec-pill is-danger" title="Danger Property (D)">D</span><span class="cct-spec-text"><code>${escapeHtml(failure.D || "false")}</code></span></div>` +
            `</div>`
          )
          : `<div class="cct-failure-td-desc">${escapeHtml(failure.reason || "Specification constraint violated")}</div>`;

        return (
          `<details class="cct-failure-card">` +
          `<summary class="cct-failure-summary">` +
          `<div class="cct-failure-heading">` +
          `<span class="cct-failure-tag">Violation</span>` +
          `<strong class="cct-failure-rule-id">${escapeHtml(failure.fsfId || "FSF Rule")}</strong>` +
          `</div>` +
          specSummaryHtml +
          `<span class="cct-failure-toggle-hint">Click to show/hide solver formula</span>` +
          `</summary>` +
          `<div class="cct-failure-body">` +
          (failure.reason ? `<div class="cct-failure-formula-label">Condition: <code>${escapeHtml(failure.reason)}</code></div>` : "") +
          `<div class="cct-failure-formula-label">SMT Solver Proof / Formula</div>` +
          `<pre class="cct-failure-formula"><code>${escapeHtml(failure.formula || "No formula recorded")}</code></pre>` +
          `</div>` +
          `</details>`
        );
      }).join("");
    } else {
      html += '<p class="cct-empty-state">No failure is recorded. This does not by itself mean “TBFV passed”.</p>';
    }
    return html;
  }

  bindTestButtons(node) {
    this.inspectorElement.querySelectorAll("[data-cct-test-id]").forEach((button) => {
      button.addEventListener("click", () => {
        this.selectedTestId = button.dataset.cctTestId;
        this.renderInspector();
      });
    });
  }

  renderEdgeInspector(edge) {
    if (!edge) {
      this.inspectorElement.innerHTML = '<p class="cct-empty-state">Select an edge.</p>';
      return;
    }
    const branch = edge.outcome ? "True" : "False";
    let html = `<h4>${escapeHtml(edge.id)}</h4>`;
    html += `<p class="cct-inspector-summary">${branch} branch from ${escapeHtml(edge.sourceNodeId)}</p>`;
    html += `<span class="cct-selection-status is-${escapeHtml(edge.allocation)}">${escapeHtml(edge.allocation)}</span>`;
    html += '<dl class="cct-detail-fields">';
    html += `<dt>Outcome</dt><dd>${edge.outcome}</dd>`;
    html += `<dt>Source</dt><dd>${escapeHtml(edge.sourceNodeId)}</dd>`;
    html += `<dt>Target</dt><dd>${edge.targetNodeId === null ? "null" : escapeHtml(edge.targetNodeId)}</dd>`;
    html += `<dt>Allocation</dt><dd>${escapeHtml(edge.allocation)}</dd>`;
    html += "</dl>";

    const risk = this.localizationOverlay?.edges?.[edge.id];
    if (risk) {
      html += '<h5>Spectrum-based suspiciousness</h5>';
      html += '<dl class="cct-detail-fields">';
      html += `<dt>Method</dt><dd>${escapeHtml(risk.method)}</dd>`;
      html += `<dt>Rank</dt><dd>${escapeHtml(risk.rank)}</dd>`;
      html += `<dt>Score</dt><dd>${escapeHtml(risk.score)}</dd>`;
      html += `<dt>Basis</dt><dd>${risk.basis === "assignment" ? "assignment block" : "source condition"}</dd>`;
      if (risk.basis === "assignment" && Array.isArray(risk.blockLines) && risk.blockLines.length) {
        html += `<dt>Top line</dt><dd>${escapeHtml(risk.line)}</dd>`;
        html += `<dt>Block lines</dt><dd>${escapeHtml(risk.blockLines.join(", "))}</dd>`;
      }
      html += `<dt>Coverage</dt><dd>${escapeHtml(risk.failedCovering)} failed / ${escapeHtml(risk.passedCovering)} passed</dd>`;
      html += "</dl>";
      html += `<details class="cct-overlay-raw"><summary>Raw overlay entry</summary><pre class="cct-overlay-json">${prettyJson(risk)}</pre></details>`;
      if (this.workspaceSource?.java_source && risk.line != null) {
        html += this.renderSourceSnippet(risk.line, risk.blockLines || []);
      }
    } else if (this.edgeOverlays[edge.id] !== undefined) {
      html += '<h5>Edge overlay</h5>';
      html += `<pre class="cct-overlay-json">${prettyJson(this.edgeOverlays[edge.id])}</pre>`;
    } else {
      html += '<p class="cct-empty-state">Additional execution or verification information can later be attached here by edge ID.</p>';
    }
    this.inspectorElement.innerHTML = html;
  }
}
