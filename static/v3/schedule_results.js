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
    const sortButton = event.target.closest('[data-sort-column]');
    if (sortButton) {
      const table = sortButton.closest('table');
      const header = sortButton.closest('th');
      const column = Number(sortButton.dataset.sortColumn);
      const direction = header.getAttribute('aria-sort') === 'ascending' ? 'descending' : 'ascending';
      const rows = [...table.tBodies[0].rows];
      rows.sort((a, b) => {
        const left = a.cells[column].textContent.trim();
        const right = b.cells[column].textContent.trim();
        const comparison = sortButton.dataset.sortType === 'number'
          ? Number(left) - Number(right) : left.localeCompare(right, undefined, {sensitivity:'base'});
        return (direction === 'ascending' ? comparison : -comparison)
          || Number(a.dataset.officialOrder) - Number(b.dataset.officialOrder);
      });
      table.querySelectorAll('th[aria-sort]').forEach(th => th.removeAttribute('aria-sort'));
      header.setAttribute('aria-sort', direction);
      rows.forEach(row => table.tBodies[0].appendChild(row));
      return;
    }
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
