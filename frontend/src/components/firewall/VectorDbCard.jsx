import { Database, ShieldCheck } from "lucide-react";
import { SectionCard } from "./primitives/SectionCard";
import { DatabaseConnectionPanel } from "../DatabaseConnectionPanel";
import { ZeroShieldGuardModelTestPanel } from "../BedrockTestPanel";

export function VectorDbCard({ index }) {
  return (
    <SectionCard
      id="vector-db"
      title="Vector Database Connection"
      description="Configure vector store provider and test connectivity"
      icon={Database}
      index={index}
      flushBody
    >
      <DatabaseConnectionPanel embedded />
    </SectionCard>
  );
}

export function ZeroShieldTestCard({ index }) {
  return (
    <SectionCard
      id="zeroshield-test"
      title="ZeroShield Guard Model Test"
      description="Health check and scan test against the guard model"
      icon={ShieldCheck}
      index={index}
      flushBody
    >
      <ZeroShieldGuardModelTestPanel embedded />
    </SectionCard>
  );
}
