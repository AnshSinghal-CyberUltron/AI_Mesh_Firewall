import { useState, useEffect } from "react";
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, AreaChart, Area, PieChart, Pie, Cell,
} from "recharts";
import { useAuth } from "../context/AuthContext";
import { SafeResponsiveChart } from "./SafeResponsiveChart";

const CHART_COLORS = ["#14b8a6", "#8b5cf6", "#f59e0b", "#ef4444", "#3b82f6", "#10b981", "#ec4899", "#6366f1"];

export function useModuleCharts(moduleId, period = "24h") {
  const { fetchWithAuth } = useAuth();
  const [charts, setCharts] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      try {
        const res = await fetchWithAuth(
          `/api/security/module-charts/${moduleId}/?period=${period}`
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (!cancelled) {
          setCharts(Array.isArray(data.charts) ? data.charts : []);
        }
      } catch {
        if (!cancelled) setCharts([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => { cancelled = true; };
  }, [fetchWithAuth, moduleId, period]);

  return { charts, loading };
}

export function ModuleChartRenderer({ chart }) {
  if (!chart || !chart.data || chart.data.length === 0) {
    return (
      <div className="flex items-center justify-center h-[280px] text-sm text-slate-400">
        Awaiting data...
      </div>
    );
  }

  switch (chart.type) {
    case "area":
      return <AreaChartRenderer data={chart.data} />;
    case "pie":
      return <PieChartRenderer data={chart.data} />;
    case "bar":
      return <BarChartRenderer data={chart.data} />;
    case "line":
      return <LineChartRenderer data={chart.data} />;
    default:
      return <BarChartRenderer data={chart.data} />;
  }
}

function AreaChartRenderer({ data }) {
  const sample = data[0] || {};
  const xKey = sample.time !== undefined ? "time" : "label";
  const valueKeys = Object.keys(sample).filter(
    (k) => k !== xKey && typeof sample[k] === "number"
  );

  return (
    <SafeResponsiveChart className="h-[280px] w-full">
      <AreaChart data={data}>
        <CartesianGrid strokeDasharray="3 3" stroke="#64748b" strokeOpacity={0.25} />
        <XAxis dataKey={xKey} stroke="#64748b" tick={{ fontSize: 11 }} />
        <YAxis stroke="#64748b" tick={{ fontSize: 11 }} />
        <Tooltip
          contentStyle={{
            backgroundColor: "rgba(15, 23, 42, 0.95)",
            border: "1px solid #475569",
            borderRadius: "8px",
            color: "#f1f5f9",
          }}
        />
        {valueKeys.map((key, i) => (
          <Area
            key={key}
            type="monotone"
            dataKey={key}
            stroke={CHART_COLORS[i % CHART_COLORS.length]}
            fill={CHART_COLORS[i % CHART_COLORS.length]}
            fillOpacity={0.3}
            stackId="stack"
            name={key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}
          />
        ))}
      </AreaChart>
    </SafeResponsiveChart>
  );
}

function PieChartRenderer({ data }) {
  const total = data.reduce((s, d) => s + (d.value || 0), 0);

  return (
    <SafeResponsiveChart className="h-[280px] w-full">
      <PieChart>
        <Pie
          data={data}
          cx="50%"
          cy="50%"
          outerRadius={90}
          dataKey="value"
          label={({ name, value }) =>
            `${name} ${total ? ((value / total) * 100).toFixed(0) : 0}%`
          }
          labelLine={false}
        >
          {data.map((entry, index) => (
            <Cell
              key={`cell-${index}`}
              fill={entry.color || CHART_COLORS[index % CHART_COLORS.length]}
            />
          ))}
        </Pie>
        <Tooltip formatter={(value) => [value.toLocaleString(), "Count"]} />
      </PieChart>
    </SafeResponsiveChart>
  );
}

function BarChartRenderer({ data }) {
  const sample = data[0] || {};
  const xKey = sample.range !== undefined ? "range" : "name";
  const numericKeys = Object.keys(sample).filter(
    (k) => k !== xKey && k !== "color" && typeof sample[k] === "number"
  );
  const yKey = numericKeys[0] || "count";

  return (
    <SafeResponsiveChart className="h-[280px] w-full">
      <BarChart data={data}>
        <CartesianGrid strokeDasharray="3 3" stroke="#64748b" strokeOpacity={0.25} />
        <XAxis
          dataKey={xKey}
          stroke="#64748b"
          tick={{ fontSize: 10 }}
          angle={-15}
          textAnchor="end"
          height={60}
        />
        <YAxis stroke="#64748b" tick={{ fontSize: 11 }} />
        <Tooltip />
        {numericKeys.length <= 1 ? (
          <Bar dataKey={yKey} fill="#3b82f6" radius={[2, 2, 0, 0]}>
            {data.map((entry, index) => (
              <Cell
                key={`cell-${index}`}
                fill={entry.color || "#3b82f6"}
              />
            ))}
          </Bar>
        ) : (
          numericKeys.map((key, i) => (
            <Bar
              key={key}
              dataKey={key}
              fill={CHART_COLORS[i % CHART_COLORS.length]}
              radius={[2, 2, 0, 0]}
              name={key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}
            />
          ))
        )}
      </BarChart>
    </SafeResponsiveChart>
  );
}

function LineChartRenderer({ data }) {
  const sample = data[0] || {};
  const xKey = sample.time !== undefined ? "time" : "label";
  const valueKeys = Object.keys(sample).filter(
    (k) => k !== xKey && typeof sample[k] === "number"
  );

  return (
    <SafeResponsiveChart className="h-[280px] w-full">
      <LineChart data={data}>
        <CartesianGrid strokeDasharray="3 3" stroke="#64748b" strokeOpacity={0.25} />
        <XAxis dataKey={xKey} stroke="#64748b" tick={{ fontSize: 11 }} />
        <YAxis stroke="#64748b" tick={{ fontSize: 11 }} />
        <Tooltip
          contentStyle={{
            backgroundColor: "rgba(15, 23, 42, 0.95)",
            border: "1px solid #475569",
            borderRadius: "8px",
            color: "#f1f5f9",
          }}
          labelStyle={{ color: "#94a3b8" }}
        />
        {valueKeys.map((key, i) => (
          <Line
            key={key}
            type="monotone"
            dataKey={key}
            stroke={CHART_COLORS[i % CHART_COLORS.length]}
            strokeWidth={2}
            dot={{ r: 3, fill: CHART_COLORS[i % CHART_COLORS.length] }}
            name={key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}
          />
        ))}
      </LineChart>
    </SafeResponsiveChart>
  );
}
