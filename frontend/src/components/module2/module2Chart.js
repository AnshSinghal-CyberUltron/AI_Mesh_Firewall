/** Recharts tooltip props — suppress hover white boxes across Module 2 charts. */
export function Module2ChartTooltip() {
  return null;
}

export const module2TooltipProps = {
  content: Module2ChartTooltip,
  cursor: false,
  wrapperStyle: { display: "none" },
  isAnimationActive: false,
};
