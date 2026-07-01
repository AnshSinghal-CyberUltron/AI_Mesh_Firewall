/**
 * Cross-module sync bridge: Module 1 mutations → Module 2 live refresh.
 * Clears the Module 2 GET cache and emits DOM events consumed by M2 pages.
 */

import { clearModule2Cache } from "../api/module2";
import { notifyContainmentChanged } from "./containmentEvents";
import { notifyTelemetryActivity } from "./telemetryEvents";

/** After config/policy/model changes that affect telemetry KPIs and analytics. */
export function syncModule2AfterTelemetryChange(source = "module1", detail = {}) {
  clearModule2Cache();
  notifyTelemetryActivity(source, detail);
}

/** After key disable, kill-switch, or credential containment changes. */
export function syncModule2AfterContainmentChange(source = "module1") {
  clearModule2Cache();
  notifyContainmentChanged(source);
}

/** Key CRUD affects both UEBA fleet registry and containment KPIs. */
export function syncModule2AfterGatewayKeyChange(action, detail = {}) {
  clearModule2Cache();
  notifyContainmentChanged(`gateway-key-${action}`);
  notifyTelemetryActivity(`gateway-key-${action}`, detail);
}
