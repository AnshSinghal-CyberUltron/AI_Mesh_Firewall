import { InfoTooltip } from "./InfoTooltip";

export function DataTable({ columns, rows, emptyMessage = "No data" }) {
  if (!rows?.length) {
    return (
      <p className="py-8 text-center text-sm text-slate-400">{emptyMessage}</p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-200 dark:border-slate-700">
            {columns.map((col) => (
              <th
                key={col.key}
                className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400"
              >
                <span className="inline-flex items-center gap-1.5">
                  <span>{col.label}</span>
                  <InfoTooltip text={col.helpText} />
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={row.id ?? i}
              className="border-b border-slate-100 hover:bg-slate-50 dark:border-slate-700/50 dark:hover:bg-slate-700/30"
            >
              {columns.map((col) => (
                <td key={col.key} className="px-3 py-2.5 text-slate-700 dark:text-slate-300">
                  {col.render ? col.render(row) : row[col.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
