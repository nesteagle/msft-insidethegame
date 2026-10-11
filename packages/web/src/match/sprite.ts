/* Pixel-art footballers for the pitch. Each sprite is an 8×10 grid drawn as one path per colour, centred on the
 * player's position. Skin and hair come from a hash of the player id, so a player looks the same all match. */

const PX = 0.4; // pitch metres per sprite pixel
const W = 8;
const H = 10;

// One row per line, top to bottom. h hair, s skin, j shirt, k shorts, o socks, b boots, g gloves (keeper only).
const ROWS = [
  "..hhhh..",
  ".hssssh.",
  "..ssss..",
  ".jjjjjj.",
  "gjjjjjjg",
  "sjjjjjjs",
  ".jjjjjj.",
  "..kkkk..",
  "..o..o..",
  "..b..b..",
];

const SKIN = ["#f1c7a0", "#e0ac82", "#c68a5c", "#9b6640", "#6e4529"];
const HAIR = ["#2b1d14", "#4a2f1b", "#7a4b23", "#c9a25a", "#111111", "#8c3b1f"];

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
}

/** Path data for every cell of `ROWS` whose code is in `codes`, grown by `grow` on each side. */
function cells(codes: string, grow = 0): string {
  let d = "";
  ROWS.forEach((row, y) => {
    [...row].forEach((c, x) => {
      if (!codes.includes(c)) return;
      const px = (x - W / 2) * PX - grow;
      const py = (y - H / 2) * PX - grow;
      const s = PX + grow * 2;
      d += `M${px.toFixed(2)} ${py.toFixed(2)}h${s.toFixed(2)}v${s.toFixed(2)}h${(-s).toFixed(2)}z`;
    });
  });
  return d;
}

const OUTLINE = cells("hsjkobg", 0.14);
const PARTS = [
  { cls: "hair", codes: "h" },
  { cls: "skin", codes: "s" },
  { cls: "shirt", codes: "j" },
  { cls: "shorts", codes: "k" },
  { cls: "socks", codes: "o" },
  { cls: "boots", codes: "b" },
  { cls: "gloves", codes: "g" },
].map((p) => ({ ...p, d: cells(p.codes) }));

/** Shirt centre, where the squad number sits. */
export const SHIRT_Y = (4.5 - H / 2) * PX + PX / 2;

export function playerSprite(pid: string, make: (tag: "path" | "ellipse", attrs: Record<string, string | number>) => SVGElement): SVGElement[] {
  const h = hash(pid);
  const skin = SKIN[h % SKIN.length];
  const hair = HAIR[(h >>> 8) % HAIR.length];
  const shadow = make("ellipse", { class: "shadow", cx: 0, cy: (H / 2) * PX, rx: 1.5, ry: 0.45 });
  const outline = make("path", { class: "outline", d: OUTLINE });
  const parts = PARTS.map((p) => {
    const el = make("path", { class: p.cls, d: p.d });
    if (p.cls === "hair") el.setAttribute("fill", hair);
    // Outfield hands are bare; the stylesheet gloves the keeper's, and CSS beats this presentation attribute.
    if (p.cls === "skin" || p.cls === "gloves") el.setAttribute("fill", skin);
    return el;
  });
  return [shadow, outline, ...parts];
}

/* The ball: a white disc with a central pentagon and five clipped patches around the rim. */
const BALL_R = 0.95;
function pentagon(cx: number, cy: number, r: number, rot: number): string {
  const pts = Array.from({ length: 5 }, (_, i) => {
    const a = rot + (i * 2 * Math.PI) / 5;
    return `${(cx + r * Math.cos(a)).toFixed(3)} ${(cy + r * Math.sin(a)).toFixed(3)}`;
  });
  return `M${pts.join("L")}z`;
}
export const BALL = {
  r: BALL_R,
  patches: [
    pentagon(0, 0, BALL_R * 0.36, -Math.PI / 2),
    ...Array.from({ length: 5 }, (_, i) => {
      const a = -Math.PI / 2 + Math.PI / 5 + (i * 2 * Math.PI) / 5;
      return pentagon(Math.cos(a) * BALL_R * 0.98, Math.sin(a) * BALL_R * 0.98, BALL_R * 0.36, a + Math.PI / 5);
    }),
  ].join(""),
};
