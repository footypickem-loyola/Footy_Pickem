/* Shared native dialog shell: server-rendered content, no football calculations. */
(() => {
  let returnFocus = null;
  let loading = false;
  const dialog = () => document.getElementById('pick-insight-dialog');
  function restoreFocus() {
    const target = returnFocus?.isConnected && returnFocus.getClientRects().length ? returnFocus :
      (document.querySelector('#matchweek-view .mw-pick:not(:disabled)') ||
       document.querySelector('#matchweek-view [data-insight-open], #matchweek-refresh'));
    target?.focus();
    returnFocus = null;
  }
  function showError() {
    const content = document.getElementById('pick-insight-content');
    content.textContent = '';
    const message = document.createElement('p');
    message.id = 'pick-insight-title';
    message.className = 'bulk-content';
    message.textContent = 'Pick insight is unavailable. Your saved picks are unchanged. Close and try Pick Recap again.';
    const close = document.createElement('button');
    close.className = 'btn';
    close.dataset.insightClose = '';
    close.textContent = 'Close';
    content.append(message, close);
    if (!dialog().open) dialog().showModal();
    close.focus();
  }
  async function openInsight(url, trigger) {
    if (loading) return;
    loading = true;
    returnFocus = trigger || null;
    // Never leave two modal layers open when a bulk list links to its recap.
    document.querySelectorAll('.bulk-dialog[open]').forEach(item => item.close());
    try {
      await htmx.ajax('GET', url, {target: '#pick-insight-content', swap: 'innerHTML'});
    } catch (_) {
      showError();
    } finally { loading = false; }
  }
  document.body.addEventListener('pickInsight', event => openInsight(event.detail.url));
  // HTMX resolves its AJAX promise on HTTP errors; handle these explicitly.
  document.body.addEventListener('htmx:responseError', event => {
    if (event.detail.target?.id === 'pick-insight-content') showError();
  });
  document.addEventListener('click', event => {
    const opener = event.target.closest('[data-insight-open]');
    if (opener) { openInsight(opener.dataset.insightOpen, opener); return; }
    if (event.target.closest('[data-insight-close]')) dialog().close();
  });
  document.body.addEventListener('htmx:afterSwap', event => {
    if (event.detail.target.id !== 'pick-insight-content') return;
    if (!dialog().open) dialog().showModal();
    dialog().querySelector('[data-insight-close]')?.focus();
    dialog().querySelector('.bulk-content')?.scrollTo(0, 0);
  });
  // Delegated listeners survive HTMX history restoration of the document body.
  document.addEventListener('close', event => {
    if (event.target.id === 'pick-insight-dialog') restoreFocus();
  }, true);
  document.addEventListener('error', event => {
    if (event.target.matches?.('.insight-crest')) event.target.hidden = true;
  }, true);
  document.body.addEventListener('htmx:beforeHistorySave', () => dialog()?.close());
})();
