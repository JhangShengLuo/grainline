import type { Properties, PropertySpec, TrackingPlan } from "./types";

function typeOk(spec: PropertySpec, value: unknown): boolean {
  switch (spec.type) {
    case "string":
      return typeof value === "string";
    case "integer":
      return typeof value === "number" && Number.isInteger(value);
    case "number":
      return typeof value === "number" && Number.isFinite(value);
    case "boolean":
      return typeof value === "boolean";
  }
}

export function declaredProperties(plan: TrackingPlan, eventName: string): Record<string, PropertySpec> {
  return { ...plan.common_properties, ...plan.events[eventName]?.properties };
}

/** 送出前在瀏覽器端和 L1 比對（伺服器 app/ingest.py 也會做同樣的檢查）。 */
export function validateEvent(plan: TrackingPlan, eventName: string, properties: Properties): string[] {
  if (!(eventName in plan.events)) {
    return [`事件 ${eventName} 沒有在 L1 宣告：會進 stg_unknown_events，不會流到報表`];
  }
  const declared = declaredProperties(plan, eventName);
  const warnings = Object.keys(properties)
    .filter((name) => !(name in declared))
    .sort()
    .map((name) => `property ${name} 沒有在 L1 宣告：下游拿不到`);
  for (const [name, spec] of Object.entries(declared)) {
    const value = properties[name];
    if (value === undefined || value === null) {
      if (spec.required !== false) warnings.push(`缺少 property ${name}（${spec.type}）：下游會是 NULL`);
    } else if (!typeOk(spec, value)) {
      warnings.push(`${name} 應為 ${spec.type}，收到 ${JSON.stringify(value)}`);
    } else if (spec.enum && !spec.enum.includes(value as string)) {
      warnings.push(`${name} = ${JSON.stringify(value)} 不在 L1 enum [${spec.enum.join(", ")}] 裡`);
    } else if (typeof value === "number") {
      if (spec.min !== undefined && value < spec.min) warnings.push(`${name} = ${value} 小於 L1 的下限 ${spec.min}`);
      if (spec.max !== undefined && value > spec.max) warnings.push(`${name} = ${value} 大於 L1 的上限 ${spec.max}`);
    }
  }
  return warnings;
}
