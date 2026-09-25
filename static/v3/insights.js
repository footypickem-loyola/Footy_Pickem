/* Delegation keeps the SVG legend working after HTMX swaps and history restores. */
document.addEventListener('change', event => {
  const control = event.target.closest('[data-chart-player]');
  if (!control) return;
  const section = control.closest('.insight-section');
  section.querySelectorAll('[data-chart-series]').forEach(series => {
    if (series.dataset.chartSeries === control.dataset.chartPlayer) {
      series.style.display = control.checked ? '' : 'none';
    }
  });
});
