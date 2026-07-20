import { describe, expect, it } from "vitest";
import {
  isEchoLikeTool,
  simulateEchoToolOutput,
} from "./MCPGuardrailSimulator";

describe("MCPGuardrailSimulator dry-run echo simulation", () => {
  it("detects echo-like tools by name and description", () => {
    expect(isEchoLikeTool("echo")).toBe(true);
    expect(isEchoLikeTool("tool_echo")).toBe(true);
    expect(isEchoLikeTool("list_teams")).toBe(false);
    expect(isEchoLikeTool("custom", "Echoes back the input string")).toBe(true);
    expect(isEchoLikeTool("list_issues", "Search Linear issues")).toBe(false);
  });

  it("simulates echo output so response-direction rules can match", () => {
    const pem =
      "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7\n-----END PRIVATE KEY-----";
    const sim = simulateEchoToolOutput({ message: pem });
    expect(sim.response).toBe(`Echo: ${pem}`);
    expect(sim.output_data.content[0].text).toContain("BEGIN PRIVATE KEY");
    expect(sim.response).toContain("BEGIN PRIVATE KEY");
  });
});
