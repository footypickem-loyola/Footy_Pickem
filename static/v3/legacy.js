    let pendingPickForm = null;
    let arsenalBanterTimer = null;
    let lastArsenalBanterIndex = -1;
    const arsenalBanterImages = JSON.parse(document.getElementById("arsenal-banter-images").textContent);

    function setWeeklyView(button) {
      const rollup = button.closest('#weekly-rollup');
      const detailed = button.dataset.view === 'detailed';
      rollup.querySelector('#weekly-summary').hidden = detailed;
      rollup.querySelector('#weekly-detailed').hidden = !detailed;
      rollup.querySelectorAll('.weekly-toggle button').forEach((choice) => {
        choice.setAttribute('aria-pressed', String(choice === button));
      });
    }

    function syncTeamOptions(gameSelect) {
      const form = gameSelect.closest('form');
      const teamSelect = form.querySelector('select[name="team"]');
      const option = gameSelect.options[gameSelect.selectedIndex];
      const previous = teamSelect.value;
      teamSelect.innerHTML = '';
      [option.dataset.home, option.dataset.away].forEach((team) => {
        const teamOption = document.createElement('option');
        teamOption.value = team;
        teamOption.textContent = team;
        teamSelect.appendChild(teamOption);
      });
      if ([option.dataset.home, option.dataset.away].includes(previous)) {
        teamSelect.value = previous;
      }
    }

    function openPickConfirm(form) {
      pendingPickForm = form;
      const gameSelect = form.querySelector('select[name="fixture_id"]');
      const teamSelect = form.querySelector('select[name="team"]');
      document.getElementById('confirm-team').textContent = teamSelect ? teamSelect.value : form.elements.team.value;
      document.getElementById('confirm-fixture').textContent = gameSelect ? gameSelect.options[gameSelect.selectedIndex].textContent : form.dataset.fixtureLabel;
      document.getElementById('pick-confirm-modal').classList.add('open');
    }

    function closePickConfirm() {
      document.getElementById('pick-confirm-modal').classList.remove('open');
      pendingPickForm = null;
    }

    function submitConfirmedPick() {
      if (!pendingPickForm) return;
      const form = pendingPickForm;
      closePickConfirm();
      htmx.trigger(form, 'confirmedPick');
    }

    function randomArsenalBanterImage() {
      let imageIndex = Math.floor(Math.random() * arsenalBanterImages.length);
      if (arsenalBanterImages.length > 1 && imageIndex === lastArsenalBanterIndex) {
        imageIndex = (imageIndex + 1) % arsenalBanterImages.length;
      }
      lastArsenalBanterIndex = imageIndex;
      return arsenalBanterImages[imageIndex];
    }

    function showArsenalBanter() {
      if (!arsenalBanterImages.length) return;
      const overlay = document.getElementById('arsenal-banter');
      const image = document.getElementById('arsenal-banter-image');
      image.src = randomArsenalBanterImage();
      overlay.classList.add('open');
      overlay.setAttribute('aria-hidden', 'false');
      document.body.classList.add('banter-open');
      window.clearTimeout(arsenalBanterTimer);
      arsenalBanterTimer = window.setTimeout(closeArsenalBanter, 4000);
    }

    function closeArsenalBanter() {
      const overlay = document.getElementById('arsenal-banter');
      window.clearTimeout(arsenalBanterTimer);
      overlay.classList.remove('open');
      overlay.setAttribute('aria-hidden', 'true');
      document.body.classList.remove('banter-open');
    }

    document.body.addEventListener('arsenalBanter', showArsenalBanter);

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        closePickConfirm();
        closeArsenalBanter();
      }
    });
