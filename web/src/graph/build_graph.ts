export type GraphNode = {
  id: string;
  entity_id: string;
  version: number;
  t_ms: number;
  risk: number;
  unknown: boolean;
};
export type GraphLink = { source: string; target: string; kind: "lineage" | "cause"; action?: string };
import { EntityState, Interaction } from "../types/TEMPORARY";
const node_id = (entity_id: string, version: number) => `${entity_id}:${version}`;

export function build_graph(states: EntityState[], interactions: Interaction[]) {
  const kept = states.filter((state) => state.version === 0 || state.caused_by !== null);

  const by_ent = new Map<string, EntityState[]>();
  for (const state of kept) {
    const list = by_ent.get(state.entity_id) ?? [];
    list.push(state);
    by_ent.set(state.entity_id, list);
  }

  for (const list of by_ent.values()) {
    list.sort((a, b) => a.t_ms - b.t_ms || a.version - b.version);
  }

  const events = new Map(interactions.map((edge) => [edge.event_id, edge]));
  const nodes: GraphNode[] = [];
  const links: GraphLink[] = [];

  for (const [ent_id, list] of by_ent.entries()) {
    list.forEach((state, index) => {
      const id = node_id(ent_id, state.version);
      nodes.push({
        id,
        entity_id: ent_id,
        version: state.version,
        t_ms: state.t_ms,
        risk: Math.max(0, ...Object.values(state.probabilities)),
        unknown: state.unknown,
      });

      if (index > 0) links.push({ source: node_id(ent_id, list[index - 1].version), target: id, kind: "lineage" });

      const edge = state.caused_by ? events.get(state.caused_by) : undefined;
      if (!edge || edge.a_id === edge.b_id) return;
      const other_id = edge.a_id === ent_id ? edge.b_id : edge.a_id;
      const other = latest_before(by_ent.get(other_id) ?? [], state.t_ms);
      if (other)
        links.push({ source: node_id(other.entity_id, other.version), target: id, kind: "cause", action: edge.action });
    });
  }
  return { nodes, links };
}

function latest_before(list: EntityState[], t_ms: number): EntityState | undefined {
  let found: EntityState | undefined = undefined;
  for (const state of list) {
    if (state.t_ms < t_ms) found = state;
    else break;
  }
  return found;
}
