import { motion } from "motion/react";
import { Slider } from "../../ui/Slider";

export function ThresholdSlider({
  value,
  onChange,
  min = 0,
  max = 1,
  step = 0.01,
  ticks,
  format = (v) => v.toFixed(2),
}) {
  const tickItems =
    ticks ??
    [
      { value: 0, label: "0.00" },
      { value: 0.5, label: "0.50" },
      { value: 1, label: "1.00" },
    ];

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-xs uppercase tracking-wider text-muted-foreground">Threshold</span>
        <motion.span
          key={value}
          initial={{ scale: 0.9, opacity: 0.6 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ duration: 0.15 }}
          className="font-mono text-sm font-semibold tabular-nums text-primary"
        >
          {format(value)}
        </motion.span>
      </div>
      <Slider
        value={[value]}
        min={min}
        max={max}
        step={step}
        onValueChange={(v) => onChange(v[0])}
        className="py-1"
      />
      <div className="flex justify-between font-mono text-[10px] text-muted-foreground">
        {tickItems.map((t) => (
          <span key={t.value ?? t}>{typeof t === "object" ? t.label : t.toFixed(2)}</span>
        ))}
      </div>
    </div>
  );
}
