// Mock data generators — swap out useMock flag in RobotContext to connect real backend

export const GRID_SIZE = 120;

// Build a static occupancy grid: 0=free, 1=occupied, -1=unknown
export function buildMockGrid(): number[][] {
  const grid: number[][] = Array.from({ length: GRID_SIZE }, () =>
    Array(GRID_SIZE).fill(-1)
  );

  // Outer walls
  for (let i = 0; i < GRID_SIZE; i++) {
    grid[0][i] = 1;
    grid[GRID_SIZE - 1][i] = 1;
    grid[i][0] = 1;
    grid[i][GRID_SIZE - 1] = 1;
  }

  // Internal walls — room layout
  const walls = [
    [20, 0, 20, 60],
    [20, 60, 60, 60],
    [60, 20, 60, 60],
    [60, 20, 100, 20],
    [100, 20, 100, 80],
    [40, 60, 40, 100],
    [40, 100, 90, 100],
    [90, 80, 90, 100],
    [80, 0, 80, 20],
  ];

  walls.forEach(([r1, c1, r2, c2]) => {
    if (r1 === r2) {
      for (let c = Math.min(c1, c2); c <= Math.max(c1, c2); c++) {
        if (r1 < GRID_SIZE && c < GRID_SIZE) grid[r1][c] = 1;
      }
    } else {
      for (let r = Math.min(r1, r2); r <= Math.max(r1, r2); r++) {
        if (r < GRID_SIZE && c1 < GRID_SIZE) grid[r][c1] = 1;
      }
    }
  });

  // Scatter debris
  const rand = mulberry32(42);
  for (let i = 0; i < 60; i++) {
    const r = Math.floor(rand() * (GRID_SIZE - 2)) + 1;
    const c = Math.floor(rand() * (GRID_SIZE - 2)) + 1;
    if (grid[r][c] === -1) grid[r][c] = 1;
  }

  // Reveal initial scanned area near start
  revealArea(grid, 10, 10, 18);

  return grid;
}

export function revealArea(grid: number[][], centerR: number, centerC: number, radius: number) {
  for (let r = Math.max(0, centerR - radius); r <= Math.min(GRID_SIZE - 1, centerR + radius); r++) {
    for (let c = Math.max(0, centerC - radius); c <= Math.min(GRID_SIZE - 1, centerC + radius); c++) {
      const dist = Math.sqrt((r - centerR) ** 2 + (c - centerC) ** 2);
      if (dist <= radius && grid[r][c] === -1) {
        grid[r][c] = 0;
      }
    }
  }
}

// Deterministic PRNG
function mulberry32(seed: number) {
  return () => {
    seed |= 0; seed = seed + 0x6D2B79F5 | 0;
    let t = Math.imul(seed ^ seed >>> 15, 1 | seed);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}

// Robot patrol waypoints (grid coords)
export const PATROL_PATH = [
  { r: 10, c: 10 },
  { r: 10, c: 50 },
  { r: 30, c: 50 },
  { r: 30, c: 10 },
  { r: 55, c: 10 },
  { r: 55, c: 55 },
  { r: 30, c: 55 },
  { r: 10, c: 55 },
];

export interface SensorReading {
  temperature: number;
  humidity: number;
  battery: number;
  co2: number;
  timestamp: number;
}

export interface DetectionEvent {
  id: string;
  timestamp: number;
  confidence: number;
  gridR: number;
  gridC: number;
  thumbnailColor: string;
  location: string;
}

export interface RobotStatus {
  connected: boolean;
  mode: "EXPLORE" | "RETURN" | "IDLE" | "MANUAL";
  speed: number;
  heading: number;
  errorCount: number;
  warningCount: number;
  infoCount: number;
  uptime: number;
}

export const INITIAL_SENSORS: SensorReading = {
  temperature: 28.4,
  humidity: 67.2,
  battery: 84,
  co2: 412,
  timestamp: Date.now(),
};

export const DETECTION_LOCATIONS = [
  "Sector A-3, Room 102",
  "Corridor B, Junction 4",
  "Sector C-1, Stairwell",
  "Room 205, East Wing",
  "Basement Level 1",
  "Rooftop Access Shaft",
];

// Seeded PRNG so module-eval values are identical on server and client (avoids
// Next.js hydration mismatches; the timestamps are pinned to the hour below).
const bbRand = mulberry32(20240724);

export const MOCK_BLACKBOX_ENTRIES = Array.from({ length: 12 }, (_, i) => {
  const d = new Date();
  d.setDate(d.getDate() - Math.floor(i / 3));
  d.setHours(8 + (i % 3) * 4, 0, 0, 0);
  return {
    id: `bb-${i}`,
    date: d.toISOString().split("T")[0],
    startTime: d.toISOString(),
    duration: Math.floor(20 + bbRand() * 80),
    detections: Math.floor(bbRand() * 8),
    size: `${(0.4 + bbRand() * 2).toFixed(1)} GB`,
    thumbnailHue: 140 + i * 15,
  };
});
