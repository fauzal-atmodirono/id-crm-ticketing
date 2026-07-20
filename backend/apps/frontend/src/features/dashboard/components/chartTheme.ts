// Shared ApexCharts theming so chart labels, legends, and axes read clearly on
// the dark app theme. ApexCharts renders SVG <text> fills, so we pass literal
// hex values (CSS var() references don't resolve inside the chart). These
// mirror src/styles/tokens.css — keep them in sync.
export const CHART_TEXT = '#e2e8f0'; // --text
export const CHART_TEXT_MUTED = '#94a3b8'; // --text-muted
export const CHART_BORDER = '#334155'; // --border

type ApexOptions = Record<string, unknown>;

/**
 * Merge per-chart options over a shared dark-theme base. Only the theming keys
 * (chart.foreColor, grid, tooltip, legend label colors) are defaulted; every
 * chart-specific setting the caller passes (type, stacked, axes, formatters,
 * legend.position, …) is preserved.
 */
export function themed(options: ApexOptions): ApexOptions {
  const chart = (options.chart ?? {}) as Record<string, unknown>;
  const legend = (options.legend ?? {}) as Record<string, unknown>;
  const legendLabels = (legend.labels ?? {}) as Record<string, unknown>;
  return {
    ...options,
    chart: {
      foreColor: CHART_TEXT_MUTED,
      fontFamily: 'inherit',
      background: 'transparent',
      ...chart,
    },
    grid: { borderColor: CHART_BORDER, ...(options.grid as Record<string, unknown>) },
    tooltip: { theme: 'dark', ...(options.tooltip as Record<string, unknown>) },
    legend: {
      ...legend,
      labels: { colors: CHART_TEXT, ...legendLabels },
    },
  };
}
