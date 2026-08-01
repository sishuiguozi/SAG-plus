import type { Entity, SourceGraphEvent, SourceGraphResponse } from "@/lib/types";

export type EventEntityGraphKind = "event" | "entity";

export interface EventEntityRelation {
  id: string;
  eventId: string;
  entityId: string;
}

export interface EventEntityGraphSlice {
  events: SourceGraphEvent[];
  entities: Entity[];
  relations: EventEntityRelation[];
}

export interface EventEntityCluster {
  id: string;
  key: string;
  label: string;
  members: Entity[];
}

export interface ClusteredEventEntityGraph {
  slice: EventEntityGraphSlice;
  clusters: Map<string, EventEntityCluster>;
  clusteredEntityCount: number;
}

const ENTITY_CLUSTER_PREFIX = "__entity_cluster__:";
const UNCATEGORIZED_CLUSTER_KEY = "__uncategorized__";

export function eventEntityNodeId(kind: EventEntityGraphKind, id: string) {
  return `${kind}:${id}`;
}

function entityClusterKey(entity: Entity) {
  return entity.type.trim().toLocaleLowerCase() || UNCATEGORIZED_CLUSTER_KEY;
}

function entityClusterId(key: string) {
  return `${ENTITY_CLUSTER_PREFIX}${encodeURIComponent(key)}`;
}

export function entityClusterKeyFromId(id: string) {
  if (!id.startsWith(ENTITY_CLUSTER_PREFIX)) return null;
  try {
    return decodeURIComponent(id.slice(ENTITY_CLUSTER_PREFIX.length));
  } catch {
    return null;
  }
}

/**
 * Collapse dense entity types into lightweight super-nodes. The full API
 * payload remains available locally; expanding a cluster therefore does not
 * need another request and both 2D and 3D views can share the same projection.
 */
export function clusterEventEntityGraph(
  slice: EventEntityGraphSlice,
  expandedClusterKeys: ReadonlySet<string>,
  {
    minimumEntityCount = 48,
    minimumClusterSize = 4,
    uncategorizedLabel = "Uncategorized",
  }: {
    minimumEntityCount?: number;
    minimumClusterSize?: number;
    uncategorizedLabel?: string;
  } = {},
): ClusteredEventEntityGraph {
  if (slice.entities.length < minimumEntityCount) {
    return {
      slice,
      clusters: new Map(),
      clusteredEntityCount: 0,
    };
  }

  const groups = new Map<string, Entity[]>();
  slice.entities.forEach((entity) => {
    const key = entityClusterKey(entity);
    const values = groups.get(key) ?? [];
    values.push(entity);
    groups.set(key, values);
  });

  const entityProjection = new Map<string, string>();
  const clusters = new Map<string, EventEntityCluster>();
  const entities: Entity[] = [];
  let clusteredEntityCount = 0;

  groups.forEach((members, key) => {
    const expanded =
      expandedClusterKeys.has(key) || members.length < minimumClusterSize;
    if (expanded) {
      members.forEach((entity) => {
        entities.push(entity);
        entityProjection.set(entity.id, entity.id);
      });
      return;
    }

    const id = entityClusterId(key);
    const label =
      key === UNCATEGORIZED_CLUSTER_KEY
        ? uncategorizedLabel
        : members.find((entity) => entity.type.trim())?.type.trim() ||
          uncategorizedLabel;
    const sortedMembers = [...members].sort(
      (left, right) =>
        right.heat - left.heat ||
        left.name.localeCompare(right.name) ||
        left.id.localeCompare(right.id),
    );
    const cluster: EventEntityCluster = {
      id,
      key,
      label,
      members: sortedMembers,
    };
    clusters.set(id, cluster);
    clusteredEntityCount += members.length;
    entities.push({
      id,
      name: `${label} · ${members.length}`,
      type: label,
      description: sortedMembers
        .slice(0, 8)
        .map((entity) => entity.name)
        .filter(Boolean)
        .join("、"),
      heat: members.reduce((total, entity) => total + entity.heat, 0),
    });
    members.forEach((entity) => entityProjection.set(entity.id, id));
  });

  const seen = new Set<string>();
  const relations: EventEntityRelation[] = [];
  slice.relations.forEach((relation) => {
    const entityId = entityProjection.get(relation.entityId);
    if (!entityId) return;
    const key = `${relation.eventId}:${entityId}`;
    if (seen.has(key)) return;
    seen.add(key);
    relations.push({
      id: `mention:${key}`,
      eventId: relation.eventId,
      entityId,
    });
  });

  return {
    slice: {
      events: slice.events,
      entities,
      relations,
    },
    clusters,
    clusteredEntityCount,
  };
}

/** Normalize the API graph to the real event-entity relationships rendered by both graph modes. */
export function sliceEventEntityGraph(graph: SourceGraphResponse): EventEntityGraphSlice {
  const eventIds = new Set(graph.events.map((event) => event.id));
  const entityIds = new Set(graph.entities.map((entity) => entity.id));
  const seen = new Set<string>();
  const relations: EventEntityRelation[] = [];

  graph.relations.forEach((relation) => {
    if (relation.kind !== "mentions") return;

    const eventId =
      relation.source_kind === "event" && relation.target_kind === "entity"
        ? relation.source_id
        : relation.target_kind === "event" && relation.source_kind === "entity"
          ? relation.target_id
          : null;
    const entityId =
      relation.source_kind === "event" && relation.target_kind === "entity"
        ? relation.target_id
        : relation.target_kind === "event" && relation.source_kind === "entity"
          ? relation.source_id
          : null;

    if (!eventId || !entityId || !eventIds.has(eventId) || !entityIds.has(entityId)) return;

    const key = `${eventId}:${entityId}`;
    if (seen.has(key)) return;
    seen.add(key);
    relations.push({ id: `mention:${key}`, eventId, entityId });
  });

  return {
    events: graph.events,
    entities: graph.entities,
    relations,
  };
}
