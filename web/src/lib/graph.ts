// Service-map geometry and reachability — pure, unit-tested in lib.test.ts.
import type { Topology, TopologyNode } from "../types";

export interface PlacedNode extends TopologyNode {
  x: number;
  y: number;
}

export interface Layout {
  nodes: PlacedNode[];
  width: number;
  height: number;
  nodeWidth: number;
  nodeHeight: number;
}

export const NODE_W = 168;
export const NODE_H = 52;
const GAP_X = 28;
const GAP_Y = 64;
const PAD = 30; // room for edges that bow around a row

/** Layered layout: callers sit above what they call (row = max depth −
 *  depth), services the graph does not know sit in a trailing row. Within a
 *  row, nodes are ordered by the mean column of their neighbours in the row
 *  above, which keeps most edges from crossing without an iterative solver. */
export function layout(topology: Topology): Layout {
  const known = topology.nodes.filter((n) => n.known);
  const unknown = topology.nodes.filter((n) => !n.known);
  const maxDepth = Math.max(0, ...known.map((n) => n.depth));

  const rows: TopologyNode[][] = [];
  for (const node of known) {
    const row = maxDepth - node.depth;
    (rows[row] ??= []).push(node);
  }
  for (let i = 0; i < rows.length; i += 1) rows[i] ??= [];
  if (unknown.length) rows.push([...unknown]);

  const callers = new Map<string, string[]>();
  for (const edge of topology.edges) {
    callers.set(edge.to, [...(callers.get(edge.to) ?? []), edge.from]);
  }

  const column = new Map<string, number>();
  rows.forEach((row, index) => {
    row.sort((a, b) => a.id.localeCompare(b.id));
    if (index > 0) {
      const rank = (node: TopologyNode): number => {
        const above = (callers.get(node.id) ?? [])
          .map((id) => column.get(id))
          .filter((c): c is number => c !== undefined);
        return above.length ? above.reduce((s, c) => s + c, 0) / above.length : Infinity;
      };
      row.sort((a, b) => rank(a) - rank(b) || a.id.localeCompare(b.id));
    }
    row.forEach((node, i) => column.set(node.id, i));
  });

  const widest = Math.max(1, ...rows.map((r) => r.length));
  const width = PAD * 2 + widest * NODE_W + (widest - 1) * GAP_X;
  const height = PAD * 2 + rows.length * NODE_H + Math.max(0, rows.length - 1) * GAP_Y;

  const nodes: PlacedNode[] = [];
  rows.forEach((row, r) => {
    const rowWidth = row.length * NODE_W + Math.max(0, row.length - 1) * GAP_X;
    const offset = (width - rowWidth) / 2;
    row.forEach((node, c) => {
      nodes.push({ ...node, x: offset + c * (NODE_W + GAP_X), y: PAD + r * (NODE_H + GAP_Y) });
    });
  });
  return { nodes, width, height, nodeWidth: NODE_W, nodeHeight: NODE_H };
}

function reach(start: string, step: Map<string, string[]>): string[] {
  const found = new Set<string>();
  const frontier = [start];
  while (frontier.length) {
    const current = frontier.pop() as string;
    for (const next of step.get(current) ?? []) {
      if (!found.has(next)) {
        found.add(next);
        frontier.push(next);
      }
    }
  }
  return [...found].sort();
}

/** Transitive callers of `service` — what degrades if it does. */
export function blastRadius(topology: Topology, service: string): string[] {
  const callers = new Map<string, string[]>();
  for (const edge of topology.edges) {
    callers.set(edge.to, [...(callers.get(edge.to) ?? []), edge.from]);
  }
  return reach(service, callers);
}

/** Transitive dependencies of `service` — where its faults can come from. */
export function upstream(topology: Topology, service: string): string[] {
  const calls = new Map<string, string[]>();
  for (const edge of topology.edges) {
    calls.set(edge.from, [...(calls.get(edge.from) ?? []), edge.to]);
  }
  return reach(service, calls);
}
