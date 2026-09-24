/* Small, local presentation toggle; HTMX remains responsible for navigation. */
(() => {
  const mobile = window.matchMedia('(max-width:760px)');
  function update() {
    document.querySelectorAll('.desk-table-views').forEach(panel => {
      const view = panel.dataset.tableView === 'auto'
        ? (mobile.matches ? 'summary' : 'detailed') : panel.dataset.tableView;
      panel.querySelectorAll('[data-standings-view]').forEach(button => {
        button.setAttribute('aria-pressed', String(button.dataset.standingsView === view));
      });
    });
  }
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-standings-view]');
    if (!button) return;
    button.closest('.desk-table-views').dataset.tableView = button.dataset.standingsView;
    update();
  });
  document.addEventListener('DOMContentLoaded', update);
  document.addEventListener('htmx:afterSwap', update);
  document.addEventListener('htmx:historyRestore', update);
  mobile.addEventListener('change', update);
  update();
})();
