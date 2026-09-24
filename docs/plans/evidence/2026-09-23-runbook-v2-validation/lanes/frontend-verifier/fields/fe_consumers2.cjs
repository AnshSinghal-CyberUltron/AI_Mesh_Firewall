// Refined: drop files whose only matches are documentation strings (verified by hand, see fe_consumers.txt notes),
// then report F ∩ P where P = G_trace ∪ G_telemetry ∪ C_ee (the specific UI-bound payload producers),
// and F ∩ G_gateway (any key the gateway emits anywhere; broad upper bound).
const fs = require("fs");
const g = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const k = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const prev = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));
const DOC_ONLY = new Set(["components/howToUseContent.js", "utils/policyCreateBehavior.js", "components/GatewayKeyPanel.jsx", "utils/environmentUrls.js"]);
const files = prev.consumer_files.map((c) => c.file).filter((f) => !DOC_ONLY.has(f));
const F = new Set(files.flatMap((f) => g.files[f].reads));
const P = new Set([...k.G_trace, ...k.G_telemetry, ...k.C_ee]);
const G = new Set(k.G_gateway);
const FP = [...F].filter((x) => P.has(x)).sort();
const FG = [...F].filter((x) => G.has(x) || P.has(x)).sort();
// which consumer files read each FP field (for traceability)
const who = {}; for (const x of FP) who[x] = files.filter((f) => g.files[f].reads.includes(x));
// hard-coded v1 stage names used as string literals in consumer files
const STAGES = ["auth", "kill_switch", "rate_limit", "policy", "input_scan", "model_routing", "model_input", "model_output", "output_guardrail"];
const stageUse = {}; for (const s of STAGES) stageUse[s] = files.filter((f) => g.files[f].strings.includes(s));
const res = { consumer_files: files, n_consumer_files: files.length, bytes: files.reduce((a, f) => a + g.files[f].bytes, 0),
  F_size: F.size, P_size: P.size, F_and_P: FP, n_F_and_P: FP.length, F_and_Gany: FG.length, readers_per_field: who, v1_stage_name_literals: stageUse };
fs.writeFileSync(process.argv[5], JSON.stringify(res, null, 1));
console.log(`consumer files: ${files.length} (${res.bytes} bytes)`);
console.log(`P (G_trace ∪ G_telemetry ∪ C_ee) size: ${P.size}`);
console.log(`F ∩ P  (specific payload fields read by consumer files): ${FP.length}`);
console.log(`F ∩ (G_gateway ∪ P) (broad; includes generic names): ${FG.length}`);
console.log(`fields read by >=3 consumer files: ${FP.filter((x) => who[x].length >= 3).length}`);
console.log("v1 stage-name string literals in consumer files:"); for (const [s, fs_] of Object.entries(stageUse)) console.log(`  ${s.padEnd(17)} ${fs_.length} files`);
console.log("\nF ∩ P:\n  " + FP.join(", "));
