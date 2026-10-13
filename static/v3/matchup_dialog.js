/* Native dialogs preserve the underlying matchup, focus and scroll position. */
(() => {
  let trigger = null;
  let scrollY = 0;
  document.addEventListener('click', event => {
    const opener = event.target.closest('[data-matchup-open]');
    if (opener) {
      const dialog = document.getElementById(opener.dataset.matchupOpen);
      if (!dialog) return;
      trigger = opener;
      scrollY = window.scrollY;
      dialog.showModal();
      document.documentElement.classList.add('mw-dialog-open');
    }
    if (event.target.closest('[data-matchup-close]')) event.target.closest('dialog').close();
  });
  document.addEventListener('close', event => {
    if (!event.target.matches('.mw-detail-dialog')) return;
    document.documentElement.classList.remove('mw-dialog-open');
    trigger?.focus({preventScroll: true});
    window.scrollTo(0, scrollY);
    trigger = null;
  }, true);
  // Keep both the dialog and underlying page stable while reading details.
  document.addEventListener('htmx:beforeRequest', event => {
    if (event.detail.elt?.id === 'matchweek-view' && document.querySelector('.mw-detail-dialog[open]')) event.preventDefault();
  });
  document.addEventListener('htmx:beforeSwap', event => {
    if (event.detail.target?.id === 'matchweek-view' && document.querySelector('.mw-detail-dialog[open]')) event.preventDefault();
  });
})();
