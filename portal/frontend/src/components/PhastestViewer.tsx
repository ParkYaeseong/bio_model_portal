type LegendItem = {
  name: string;
  swatchColor: string;
};

type Feature = {
  type: string;
  name: string;
  start: number;
  stop: number;
  strand: number;
  legend: string;
};

export type CgviewData = {
  cgview: {
    sequence: { length: number };
    features: Feature[];
    legend: { items: LegendItem[] };
  };
};

const TAU = Math.PI * 2;

const clampFeatures = (features: Feature[]) => features.slice(0, 250);

function polarToCartesian(cx: number, cy: number, radius: number, angle: number) {
  return {
    x: cx + radius * Math.cos(angle),
    y: cy + radius * Math.sin(angle),
  };
}

function describeArc(cx: number, cy: number, radius: number, startAngle: number, endAngle: number) {
  const start = polarToCartesian(cx, cy, radius, endAngle);
  const end = polarToCartesian(cx, cy, radius, startAngle);
  const largeArcFlag = endAngle - startAngle <= Math.PI ? "0" : "1";
  return `M ${start.x} ${start.y} A ${radius} ${radius} 0 ${largeArcFlag} 0 ${end.x} ${end.y}`;
}

type Props = {
  data: CgviewData;
};

export function PhastestViewer({ data }: Props) {
  const sequenceLength = data?.cgview?.sequence?.length ?? 0;
  const features = clampFeatures(data?.cgview?.features ?? []);
  const legend = data?.cgview?.legend?.items ?? [];
  const colorMap = new Map<string, string>();
  legend.forEach((item) => colorMap.set(item.name, item.swatchColor));

  const cx = 150;
  const cy = 150;
  const baseRadius = 110;

  const arcs = features.map((feature, index) => {
    const startAngle = (feature.start / Math.max(sequenceLength, 1)) * TAU;
    const endAngle = (feature.stop / Math.max(sequenceLength, 1)) * TAU;
    const radius = feature.strand >= 0 ? baseRadius : baseRadius - 20;
    const path = describeArc(cx, cy, radius, startAngle, endAngle);
    const color = colorMap.get(feature.legend) || "#0f172a";
    return (
      <path
        key={`${feature.name}-${index}`}
        d={path}
        stroke={color}
        strokeWidth={feature.strand >= 0 ? 8 : 5}
        strokeLinecap="round"
        fill="none"
        opacity={0.85}
      />
    );
  });

  return (
    <div className="rounded-2xl border border-slate-100 bg-white p-4">
      <div className="flex flex-col gap-4 lg:flex-row">
        <div className="flex-1">
          <svg viewBox="0 0 300 300" className="h-72 w-full">
            <circle cx={cx} cy={cy} r={baseRadius + 15} stroke="#cbd5f5" strokeWidth={2} fill="none" />
            <circle cx={cx} cy={cy} r={baseRadius - 25} stroke="#cbd5f5" strokeWidth={2} fill="none" />
            {arcs}
            <text x={cx} y={cy} textAnchor="middle" dominantBaseline="middle" className="fill-slate-500 text-sm">
              {sequenceLength.toLocaleString()} bp
            </text>
          </svg>
        </div>
        <div className="flex w-full flex-col gap-3 lg:max-w-xs">
          <p className="text-sm font-semibold text-slate-700">Legend</p>
          <div className="flex flex-wrap gap-2">
            {legend.map((item) => (
              <span
                key={item.name}
                className="flex items-center gap-2 rounded-full border border-slate-200 px-3 py-1 text-xs text-slate-600"
              >
                <span className="h-2 w-6 rounded-full" style={{ backgroundColor: item.swatchColor }} />
                {item.name}
              </span>
            ))}
          </div>
        </div>
      </div>
      <div className="mt-4 max-h-48 overflow-y-auto rounded-2xl border border-slate-100 bg-slate-50 p-3 text-xs text-slate-700">
        <table className="w-full border-collapse">
          <thead>
            <tr className="text-left text-[11px] uppercase tracking-wide text-slate-500">
              <th className="py-1 pr-2">Region</th>
              <th className="py-1 pr-2">Name</th>
              <th className="py-1 pr-2">Start</th>
              <th className="py-1 pr-2">Stop</th>
            </tr>
          </thead>
          <tbody>
            {features.map((feature, index) => (
              <tr key={`${feature.legend}-${index}`} className="border-b border-slate-100 last:border-b-0">
                <td className="py-1 pr-2">{feature.legend}</td>
                <td className="py-1 pr-2">{feature.name}</td>
                <td className="py-1 pr-2">{feature.start.toLocaleString()}</td>
                <td className="py-1 pr-2">{feature.stop.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
