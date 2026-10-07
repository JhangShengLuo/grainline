import { useEffect, useRef, useState } from "react";

export interface Series {
  name: string;
  /** 第幾個類別色（1 起算），依實體固定，不依排序 */
  slot: 1 | 2;
  values: (number | null)[];
}

interface Props {
  title: string;
  labels: string[];
  series: Series[];
  format: (value: number) => string;
  height?: number;
}

const PAD = { top: 12, right: 72, bottom: 28, left: 56 };

function niceTicks(max: number, count = 4): number[] {
  if (max <= 0) return [0];
  const raw = max / count;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw)!;
  return Array.from({ length: Math.ceil(max / step) + 1 }, (_, i) => i * step);
}

/** 時間序列折線圖：2px 線、十字準線與 tooltip、≥2 條線時有圖例並直接標示 */
export function LineChart({ title, labels, series, format, height = 240 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);

  const all = series.flatMap((s) => s.values).filter((v): v is number => v !== null);
  const ticks = niceTicks(Math.max(0, ...all));
  const yMax = ticks[ticks.length - 1] || 1;
  const innerW = Math.max(10, width - PAD.left - PAD.right);
  const innerH = height - PAD.top - PAD.bottom;
  const x = (i: number) => PAD.left + (labels.length <= 1 ? innerW / 2 : (i / (labels.length - 1)) * innerW);
  const y = (v: number) => PAD.top + innerH - (v / yMax) * innerH;

  const path = (values: (number | null)[]) =>
    values
      .map((v, i) => (v === null ? null : `${i === 0 || values[i - 1] === null ? "M" : "L"}${x(i)},${y(v)}`))
      .filter(Boolean)
      .join(" ");

  // 直接標示在每條線的最後一點；兩個標籤太近時上下錯開，不會疊在一起
  const lastIndex = series.map((s) => s.values.reduce<number>((acc, v, i) => (v === null ? acc : i), -1));
  const labelY: (number | null)[] = series.map((s, i) =>
    series.length >= 2 && lastIndex[i] >= 0 ? y(s.values[lastIndex[i]]!) : null,
  );
  const MIN_GAP = 14;
  const order = labelY.map((v, i) => [v, i] as const).filter(([v]) => v !== null).sort((p, q) => p[0]! - q[0]!);
  for (let k = 1; k < order.length; k++) {
    const [prevIdx, idx] = [order[k - 1][1], order[k][1]];
    if (labelY[idx]! - labelY[prevIdx]! < MIN_GAP) labelY[idx] = labelY[prevIdx]! + MIN_GAP;
  }

  const xLabelIdx = [...new Set([0, Math.floor((labels.length - 1) / 2), labels.length - 1])].filter((i) => i >= 0);

  function onMove(event: React.PointerEvent<SVGRectElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    const ratio = (event.clientX - rect.left) / rect.width;
    setHover(Math.max(0, Math.min(labels.length - 1, Math.round(ratio * (labels.length - 1)))));
  }

  return (
    <figure className="chart">
      <figcaption>
        <span className="chart-title">{title}</span>
        {series.length >= 2 && (
          <span className="legend">
            {series.map((s) => (
              <span key={s.name}>
                <i className={`swatch series-${s.slot}`} aria-hidden="true" />
                {s.name}
              </span>
            ))}
          </span>
        )}
      </figcaption>
      <div ref={ref} className="chart-box">
        <svg width={width} height={height} role="img" aria-label={title}>
          {ticks.map((t) => (
            <g key={t}>
              <line className="grid" x1={PAD.left} x2={PAD.left + innerW} y1={y(t)} y2={y(t)} />
              <text className="axis" x={PAD.left - 8} y={y(t)} dy="0.32em" textAnchor="end">
                {format(t)}
              </text>
            </g>
          ))}
          {xLabelIdx.map((i) => (
            <text key={i} className="axis" x={x(i)} y={height - 8} textAnchor={i === 0 ? "start" : i === labels.length - 1 ? "end" : "middle"}>
              {labels[i]?.length === 10 ? labels[i].slice(5) : labels[i]}
            </text>
          ))}
          {series.map((s, i) => (
            <g key={s.name}>
              <path className={`line series-${s.slot}`} d={path(s.values)} />
              {labelY[i] !== null && (
                <text className="direct-label" x={x(lastIndex[i]) + 6} y={labelY[i]!} dy="0.32em">
                  {s.name}
                </text>
              )}
            </g>
          ))}
          {hover !== null && (
            <g>
              <line className="crosshair" x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={PAD.top + innerH} />
              {series.map((s) =>
                s.values[hover] === null ? null : (
                  <circle key={s.name} className={`marker series-${s.slot}`} cx={x(hover)} cy={y(s.values[hover]!)} r={4} />
                ),
              )}
            </g>
          )}
          <rect
            x={PAD.left}
            y={PAD.top}
            width={innerW}
            height={innerH}
            fill="transparent"
            onPointerMove={onMove}
            onPointerLeave={() => setHover(null)}
          />
        </svg>
        {hover !== null && (
          <div
            className="tooltip"
            style={{ left: Math.min(x(hover) + 12, width - 180), top: PAD.top }}
            role="status"
          >
            <div className="tooltip-title">{labels[hover]}</div>
            {series.map((s) => (
              <div key={s.name} className="tooltip-row">
                <i className={`swatch series-${s.slot}`} aria-hidden="true" />
                <span>{s.name}</span>
                <b>{s.values[hover] === null ? "—" : format(s.values[hover]!)}</b>
              </div>
            ))}
          </div>
        )}
      </div>
    </figure>
  );
}
