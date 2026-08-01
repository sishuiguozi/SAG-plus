export interface SourceGraph2dPerformanceProfile {
  useForceLayout: boolean;
  simulationTicks: number;
  collisionIterations: number;
}

export function sourceGraph2dPerformanceProfile(
  nodeCount: number,
): SourceGraph2dPerformanceProfile {
  if (nodeCount > 160) {
    return {
      useForceLayout: false,
      simulationTicks: 0,
      collisionIterations: 1,
    };
  }
  if (nodeCount > 96) {
    return {
      useForceLayout: true,
      simulationTicks: 200,
      collisionIterations: 2,
    };
  }
  return {
    useForceLayout: true,
    simulationTicks: 300,
    collisionIterations: 3,
  };
}

export interface OrbitalGraphPerformanceProfile {
  antialias: boolean;
  pixelRatioCap: number;
  plateGeometryDetail: number;
  edgeCurveSegments: number;
  minimumFrameIntervalMs: number;
  backgroundPointCount: number;
}

export function orbitalGraphPerformanceProfile({
  eventCount,
  entityCount,
  relationCount,
}: {
  eventCount: number;
  entityCount: number;
  relationCount: number;
}): OrbitalGraphPerformanceProfile {
  const nodeCount = eventCount + entityCount;
  const dense = nodeCount > 160 || relationCount > 320;
  if (dense) {
    return {
      antialias: false,
      pixelRatioCap: 1.25,
      plateGeometryDetail: eventCount > 120 ? 3 : 4,
      edgeCurveSegments: relationCount > 600 ? 10 : 14,
      minimumFrameIntervalMs: 1000 / 30,
      backgroundPointCount: 160,
    };
  }
  if (nodeCount > 80 || relationCount > 160) {
    return {
      antialias: true,
      pixelRatioCap: 1.5,
      plateGeometryDetail: 4,
      edgeCurveSegments: 20,
      minimumFrameIntervalMs: 1000 / 45,
      backgroundPointCount: 220,
    };
  }
  return {
    antialias: true,
    pixelRatioCap: 2,
    plateGeometryDetail: 4,
    edgeCurveSegments: 26,
    minimumFrameIntervalMs: 0,
    backgroundPointCount: 280,
  };
}
