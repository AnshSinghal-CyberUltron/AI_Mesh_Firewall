// Slim, tree-shaken ECharts instance. Import THIS ("./echartsCore"), never the
// full "echarts" package, so only the chart types + components the ZeroShield
// dashboards actually use are bundled (see FRONTEND_AUDIT.md R3 — bundle size).
//
// Chart families in the codebase: Line, Bar, Area (= Line + areaStyle), Pie,
// Radar. Components: grid/tooltip/legend/title + dataZoom & brush-style zoom for
// interactive dashboards, radar coordinate system, visualMap for value-based
// coloring, markLine + graphic for annotations.
import * as echarts from "echarts/core";
import { LineChart, BarChart, PieChart, RadarChart, CustomChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
  DataZoomComponent,
  RadarComponent,
  VisualMapComponent,
  MarkLineComponent,
  GraphicComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { zsLightTheme, zsDarkTheme } from "../../utils/chartTheme";

echarts.use([
  LineChart,
  BarChart,
  PieChart,
  RadarChart,
  CustomChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
  DataZoomComponent,
  RadarComponent,
  VisualMapComponent,
  MarkLineComponent,
  GraphicComponent,
  CanvasRenderer,
]);

echarts.registerTheme("zs-light", zsLightTheme);
echarts.registerTheme("zs-dark", zsDarkTheme);

export default echarts;
export { echarts };
