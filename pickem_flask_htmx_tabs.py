#!/usr/bin/env python3
import argparse
import hashlib
import hmac
import json
import os
import random
import re
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import Flask, abort, flash, jsonify, make_response, redirect, render_template_string, request, session, url_for
from flask_session import Session
from sqlalchemy import (
    create_engine, Column, Integer, String, Text, ForeignKey, UniqueConstraint,
    DateTime, event, func
)
from sqlalchemy.orm import sessionmaker, declarative_base, relationship, scoped_session
from sqlalchemy.exc import IntegrityError
from jinja2 import DictLoader
import pandas as pd
import bleach
import markdown
from markupsafe import Markup

from correspondent import (
    CLASSIFIER_PROMPT_VERSION,
    DEFAULT_MODEL as DEFAULT_CORRESPONDENT_MODEL,
    PROMPT_VERSION as CORRESPONDENT_PROMPT_VERSION,
    ClassificationBatch,
    CorrespondentError,
    GeneratedRecap,
    SourceClassification,
    V2_PROMPT_VERSION,
    build_v2_context,
    build_classification_batch_request,
    classifier_client,
    classifier_model,
    classify_candidate_sources,
    generate_weekly_recap,
    generate_weekly_recap_v2,
    validate_classification_response,
)

FOOTBALL_DATA_BASE_URL = "https://api.football-data.org/v4"
FOOTBALL_DATA_PROVIDER = "football-data.org"
DEFAULT_API_COMPETITION = "PL"
DEFAULT_API_SEASON_YEAR = 2026
ARSENAL_BANTER_FILENAMES = [
    f"arsenal_banter/banter_{image_number:02d}.jpg"
    for image_number in range(1, 11)
]
ARSENAL_TEAM_ALIASES = {"arsenal", "arsenal fc"}

# -------------------- In-memory base + partial templates --------------------
BASE_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>EPL Pick 'Em</title>
  <script src="https://unpkg.com/htmx.org@2.0.2"></script>
  <style>
    :root { --blue:#0ea5e9; }
    body { font-family: -apple-system, system-ui, Segoe UI, Roboto, sans-serif; margin: 24px; color: #111; }
    a { color: var(--blue); text-decoration: none; }
    nav { display:flex; gap:12px; align-items:center; margin-bottom:16px; }
    .tabs { display:flex; gap:8px; align-items:center; }
    .tab { padding:8px 12px; border:1px solid #ddd; border-radius:999px; cursor:pointer; background:#f8f8f8; }
    .tab.active { background:var(--blue); color:#fff; border-color:#0284c7; }
    .navright { margin-left:auto; }
    .card { border: 1px solid #ddd; border-radius: 12px; padding: 16px; margin: 12px 0; box-shadow: 0 1px 4px rgba(0,0,0,0.04); }
    .row { display: flex; gap: 16px; flex-wrap: wrap; }
    .col { flex: 1; min-width: 320px; }
    .btn { padding: 8px 12px; border: 1px solid #ccc; background: #f8f8f8; border-radius: 8px; cursor: pointer; }
    .btn.primary { background: var(--blue); color: white; border-color: #0284c7; }
    .muted { color: #666; }
    .badge { display: inline-block; padding: 2px 8px; background: #eef; border: 1px solid #aac; border-radius: 999px; font-size: 12px; }
    select, input { padding: 6px; border: 1px solid #ccc; border-radius: 6px; }
    table { width: 100%; border-collapse: collapse; }
    th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #eee; }
    .centered-table th, .centered-table td { text-align: center; }
    .season-summary-table { width:min(100%, 520px); margin:0 auto; }
    .season-summary-table th, .season-summary-table td { padding:6px 10px; }
    .detailed-season-table th.for-header { background:#2563eb; color:#fff; }
    .detailed-season-table th.against-header { background:#dbeafe; color:#0f2852; }
    .detailed-season-table .against-start { border-left:3px solid #1d4ed8; }
    .detailed-season-table th.total-net-header { background:#f59e0b; color:#3b2600; border-left:3px solid #b45309; }
    .detailed-season-table td.total-net-cell { background:#fef3c7; font-weight:700; border-left:3px solid #b45309; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 8px; }
    .chip { border: 1px solid #ddd; border-radius: 999px; padding: 6px 10px; display: flex; justify-content: space-between; align-items: center; }
    .status { display:inline-block; padding:2px 8px; border-radius:999px; border:1px solid #ccc; font-size:12px; }
    .status.drafting { background:#eef; border-color:#99c; }
    .status.provisional { background:#ffe; border-color:#cc9; }
    .status.finalized { background:#efe; border-color:#9c9; }
    .table-scroll { overflow-x:auto; -webkit-overflow-scrolling:touch; }
    .modal-backdrop { display:none; position:fixed; inset:0; z-index:1000; background:rgba(0,0,0,.58); padding:20px; align-items:center; justify-content:center; }
    .modal-backdrop.open { display:flex; }
    .modal-card { width:min(420px, 100%); background:#fff; border-radius:16px; padding:22px; box-shadow:0 20px 60px rgba(0,0,0,.28); }
    .modal-actions { display:flex; gap:10px; justify-content:flex-end; margin-top:18px; }
    .confirm-team { font-size:24px; font-weight:700; margin:8px 0 2px; }
    .leader-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
    .leader-stat { background:#f8fafc; border:1px solid #e2e8f0; border-radius:10px; padding:10px; }
    .leader-label { color:#64748b; font-size:12px; font-weight:600; text-transform:uppercase; letter-spacing:.03em; }
    .leader-value { font-size:16px; font-weight:700; margin-top:3px; }
    .leader-detail { color:#64748b; font-size:12px; margin-top:2px; }
    .arsenal-banter { position:fixed; inset:0; z-index:2000; overflow:hidden; background:#111827; opacity:0; visibility:hidden; pointer-events:none; transition:opacity .18s ease, visibility .18s ease; }
    .arsenal-banter.open { opacity:1; visibility:visible; pointer-events:auto; }
    .arsenal-banter-image { position:absolute; inset:0; width:100%; height:100%; object-fit:cover; }
    .arsenal-banter-scrim { position:absolute; inset:0; background:linear-gradient(180deg, rgba(0,0,0,.12) 20%, rgba(0,0,0,.88) 100%); }
    .arsenal-banter-copy { position:absolute; left:0; right:0; bottom:0; color:#fff; padding:clamp(28px, 7vw, 72px); text-align:center; text-shadow:0 2px 16px rgba(0,0,0,.7); }
    .arsenal-banter-kicker { color:#fca5a5; font-size:13px; font-weight:800; letter-spacing:.18em; text-transform:uppercase; }
    .arsenal-banter-message { max-width:760px; margin:10px auto 0; font-size:clamp(30px, 7vw, 64px); font-weight:850; line-height:1.02; }
    .arsenal-banter-hint { margin-top:16px; font-size:13px; opacity:.78; }
    body.banter-open { overflow:hidden; }
    @media (max-width:640px) {
      body { margin:12px; }
      nav { align-items:flex-start; }
      .tabs { flex-wrap:wrap; }
      .navright { width:100%; margin-left:0; }
      .col { min-width:100%; }
      .arsenal-banter-copy { padding:28px 20px 36px; }
    }
  </style>
</head>
<body>
  <nav>
    <div class="tabs">
      <button class="tab {% if active_tab=='current' %}active{% endif %}"
              hx-get="{{ url_for('tab_current') }}"
              hx-target="#main" hx-swap="innerHTML" hx-push-url="true">
        Current Week
      </button>
      <button class="tab {% if active_tab=='open' %}active{% endif %}"
              hx-get="{{ url_for('tab_open') }}"
              hx-target="#main" hx-swap="innerHTML" hx-push-url="true">
        Open Weeks
      </button>
      <button class="tab {% if active_tab=='season' %}active{% endif %}"
              hx-get="{{ url_for('tab_season') }}"
              hx-target="#main" hx-swap="innerHTML" hx-push-url="true">
        Season
      </button>
      <button class="tab {% if active_tab=='stats' %}active{% endif %}"
              hx-get="{{ url_for('tab_stats') }}"
              hx-target="#main" hx-swap="innerHTML" hx-push-url="true">
        Stats
      </button>
    </div>
    <div class="navright muted">Logged in as: {{ you.name if you else 'Guest' }}</div>
  </nav>

  <div id="main">
    {{ body|safe }}
  </div>

  <div id="pick-confirm-modal" class="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="pick-confirm-title">
    <div class="modal-card">
      <h3 id="pick-confirm-title">Confirm pick</h3>
      <div id="confirm-team" class="confirm-team"></div>
      <div id="confirm-fixture" class="muted"></div>
      <div class="modal-actions">
        <button class="btn" type="button" onclick="closePickConfirm()">Go Back</button>
        <button class="btn primary" type="button" onclick="submitConfirmedPick()">Confirm Pick</button>
      </div>
    </div>
  </div>

  <div id="arsenal-banter" class="arsenal-banter" role="dialog" aria-modal="true" aria-hidden="true"
       aria-label="Arsenal pick celebration" onclick="closeArsenalBanter()">
    <img id="arsenal-banter-image" class="arsenal-banter-image" alt="Arsenal banter" decoding="async">
    <div class="arsenal-banter-scrim"></div>
    <div class="arsenal-banter-copy" aria-live="polite">
      <div class="arsenal-banter-kicker">Excellent judgment</div>
      <div class="arsenal-banter-message">You’ve picked the 2026 Champions. Nice pick!</div>
      <div class="arsenal-banter-hint">Tap anywhere to dismiss</div>
    </div>
  </div>

  <footer class="muted" style="margin-top:24px; font-size:12px; text-align:center;">
    Football data provided by the
    <a href="https://www.football-data.org/" target="_blank" rel="noopener noreferrer">Football-Data.org API</a>.
  </footer>

  <script>
    let pendingPickForm = null;
    let arsenalBanterTimer = null;
    let lastArsenalBanterIndex = -1;
    const arsenalBanterImages = {{ arsenal_banter_images|tojson }};

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
      document.getElementById('confirm-team').textContent = teamSelect.value;
      document.getElementById('confirm-fixture').textContent = gameSelect.options[gameSelect.selectedIndex].textContent;
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
  </script>
</body>
</html>
"""

# --- Admin template ---
ADMIN_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>EPL Pick 'Em — Admin</title>
  <script src="https://unpkg.com/htmx.org@2.0.2"></script>
  <style>
    body { font-family: -apple-system, system-ui, Segoe UI, Roboto, sans-serif; margin: 24px; color:#111; }
    .card { border: 1px solid #ddd; border-radius: 12px; padding: 16px; margin: 12px 0; box-shadow: 0 1px 4px rgba(0,0,0,0.04); }
    .row { display:flex; gap:16px; flex-wrap:wrap; }
    .col { flex:1; min-width: 320px; }
    table { width: 100%; border-collapse: collapse; }
    th, td { text-align:left; padding:6px 8px; border-bottom:1px solid #eee; }
    select, input, textarea { padding:6px; border:1px solid #ccc; border-radius:6px; font:inherit; }
    .btn { padding:8px 12px; border:1px solid #ccc; background:#f8f8f8; border-radius:8px; cursor:pointer; }
    .btn.primary { background:#0ea5e9; color:#fff; border-color:#0284c7; }
    .muted { color:#666; }
    .notice { padding:10px 12px; border-radius:8px; margin:8px 0; }
    .notice.success { background:#ecfdf5; border:1px solid #86efac; }
    .notice.error { background:#fef2f2; border:1px solid #fca5a5; }
    .api-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:8px; }
    .api-stat { background:#f8fafc; border-radius:8px; padding:10px; }
    .recap-body { white-space:pre-wrap; line-height:1.55; background:#f8fafc; border-radius:8px; padding:14px; }
    .source-form { display:grid; gap:8px; }
    .source-form textarea { width:min(100%,760px); min-height:110px; box-sizing:border-box; }
    .version-actions { display:flex; gap:8px; flex-wrap:wrap; }
    .official { color:#166534; font-weight:700; }
  </style>
</head>
<body>
  <div class="card">
    <h2>Admin — Edit Results</h2>
    {% for category, message in get_flashed_messages(with_categories=true) %}
      <div class="notice {{ category }}">{{ message }}</div>
    {% endfor %}
    {% if not is_admin %}
      <form method="post" action="{{ url_for('admin_login') }}">
        <label>Room Code <input name="room_code" required></label>
        <button class="btn primary" type="submit">Unlock</button>
      </form>
      <p class="muted">Enter the current season room code to unlock admin tools.</p>
    {% else %}
      <form method="post" action="{{ url_for('admin_logout') }}" style="margin-bottom:8px;">
        <button class="btn" type="submit">Lock Admin</button>
      </form>

      <div class="card">
        <h3>Premier League Data</h3>
        <p class="muted">
          API key: {{ 'Configured' if api_configured else 'Not configured' }}.
          The key is read from the server environment and is never displayed here.
        </p>

        {% if can_import_api %}
          <h4>Import 2026–27 Fixtures</h4>
          <form method="post" action="{{ url_for('admin_import_api_season') }}">
            <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
              <label>Season name <input name="season_name" value="2026–27" required></label>
              <label>Season year <input name="season_year" type="number" value="2026" required></label>
              <label>Room code <input name="room_code" value="{{ default_room_code }}" required></label>
            </div>
            <label style="display:block; margin-top:8px;">Players
              <input name="players" value="{{ default_players }}" style="width:min(100%,620px);" required>
            </label>
            <button class="btn primary" type="submit" style="margin-top:8px;">Import Premier League Fixtures</button>
          </form>
          <p class="muted">The import validates all 380 fixtures before writing anything.</p>
        {% elif season and not season.is_archived %}
          <form method="post" action="{{ url_for('admin_sync_api_results') }}">
            <button class="btn primary" type="submit" {% if not api_configured %}disabled{% endif %}>Sync Final Scores</button>
          </form>
        {% endif %}

        {% if api_state %}
          <div class="api-grid" style="margin-top:12px;">
            <div class="api-stat"><strong>Last successful sync</strong><br>{{ api_last_success }}</div>
            <div class="api-stat"><strong>Results imported</strong><br>{{ api_state.results_imported }}</div>
            <div class="api-stat"><strong>Matches pending</strong><br>{{ api_state.pending_matches }}</div>
            <div class="api-stat"><strong>Unmatched fixtures</strong><br>{{ api_state.unmatched_matches }}</div>
            <div class="api-stat"><strong>Requests remaining</strong><br>{{ api_state.requests_remaining if api_state.requests_remaining is not none else 'Unknown' }}</div>
            <div class="api-stat"><strong>Allowance reset</strong><br>{% if api_state.reset_seconds is not none %}{{ api_state.reset_seconds }} sec{% else %}Unknown{% endif %}</div>
          </div>
          {% if api_state.last_error %}<div class="notice error">Last API error: {{ api_state.last_error }}</div>{% endif %}
        {% endif %}
      </div>

      {% if week %}
      <div class="card">
        <form method="get" action="{{ url_for('admin') }}">
          <label>Week
            <select name="week" onchange="this.form.submit()">
              {% for wk in weeks %}
                <option value="{{ wk.number }}" {% if wk.number==week.number %}selected{% endif %}>Week {{ wk.number }} ({{ wk.status }})</option>
              {% endfor %}
            </select>
          </label>
        </form>
      </div>

      <div class="card">
        <h3>AI Correspondent — Week {{ week.number }}</h3>
        {% if week.status == 'finalized' %}
          <div class="version-actions">
            <form method="post" action="{{ url_for('admin_generate_recap') }}">
              <input type="hidden" name="week" value="{{ week.number }}">
              <input type="hidden" name="version" value="v1">
              <button class="btn {% if correspondent_default_version == 'v1' %}primary{% endif %}" type="submit" {% if not openai_configured %}disabled{% endif %}>
                Generate V1 Recap
              </button>
            </form>
            {% if correspondent_v2_enabled %}
              <form method="post" action="{{ url_for('admin_generate_recap') }}">
                <input type="hidden" name="week" value="{{ week.number }}">
                <input type="hidden" name="version" value="v2">
                <button class="btn {% if correspondent_default_version == 'v2' %}primary{% endif %}" type="submit"
                        {% if not openai_configured or not correspondent_sources %}disabled{% endif %}>
                  Generate V2 Recap
                </button>
              </form>
            {% endif %}
          </div>
          <p class="muted">
            Model: {{ correspondent_model }}.
            {% if not openai_configured %}Add OPENAI_API_KEY to enable generation.{% endif %}
            {% if correspondent_v2_enabled and not correspondent_sources %}Add at least one source to enable V2.{% endif %}
          </p>
        {% else %}
          <p class="muted">The recap can be generated after all ten results are final.</p>
        {% endif %}

        {% if displayed_recap %}
          <div class="notice {{ 'success' if displayed_recap.status == 'ready' else 'error' }}">
            Revision {{ displayed_recap.revision }} —
            {{ displayed_recap.correspondent_version|upper }} —
            {{ displayed_recap.status|capitalize }}
            <span class="muted">· Viewing</span>
            {% if selected_recap and selected_recap.id == displayed_recap.id %}
              <span class="official">· Official</span>
            {% endif %}
          </div>
          {% if displayed_recap.status == 'ready' %}
            <h4>{{ displayed_recap.title }}</h4>
            <div class="recap-body">{{ displayed_recap.body_markdown|recap_markdown }}</div>
          {% elif displayed_recap.error_message %}
            <div class="recap-body">{{ displayed_recap.error_message }}</div>
          {% endif %}
          <p class="muted">
            Prompt {{ displayed_recap.prompt_version }} · {{ displayed_recap.model }} ·
            {% if displayed_recap.correspondent_version == 'v2' %}
              {{ displayed_recap.source_count }} candidate source{{ '' if displayed_recap.source_count == 1 else 's' }} ·
              {{ recap_used_source_counts.get(displayed_recap.id, 0) }} used ·
            {% endif %}
            {{ displayed_recap.completed_at or displayed_recap.created_at }}
          </p>
        {% else %}
          <p class="muted">No recap has been generated for this week.</p>
        {% endif %}

        {% if recaps %}
          <details {% if displayed_recap and latest_recap and displayed_recap.id != latest_recap.id %}open{% endif %}>
            <summary>All recap revisions</summary>
            <table>
              <thead><tr><th>Revision</th><th>Version</th><th>Status</th><th>Title</th><th>Sources</th><th>View</th><th>Official recap</th></tr></thead>
              <tbody>
                {% for recap in recaps %}
                  <tr>
                    <td>{{ recap.revision }}</td>
                    <td>{{ recap.correspondent_version|upper }}</td>
                    <td>{{ recap.status }}</td>
                    <td>{{ recap.title or '—' }}</td>
                    <td>
                      {% if recap.correspondent_version == 'v2' %}
                        {{ recap.source_count }} candidate{{ '' if recap.source_count == 1 else 's' }} /
                        {{ recap_used_source_counts.get(recap.id, 0) }} used
                      {% else %}
                        —
                      {% endif %}
                    </td>
                    <td>
                      {% if displayed_recap and displayed_recap.id == recap.id %}
                        <span class="official">Viewing</span>
                      {% else %}
                        <a class="btn" href="{{ url_for('admin', week=week.number, recap_id=recap.id) }}">View recap</a>
                      {% endif %}
                    </td>
                    <td>
                      {% if selected_recap and selected_recap.id == recap.id %}
                        <span class="official">Selected</span>
                      {% elif recap.status == 'ready' %}
                        <form method="post" action="{{ url_for('admin_select_recap') }}">
                          <input type="hidden" name="week" value="{{ week.number }}">
                          <input type="hidden" name="recap_id" value="{{ recap.id }}">
                          <button class="btn" type="submit">Use this recap</button>
                        </form>
                      {% else %}
                        —
                      {% endif %}
                    </td>
                  </tr>
                {% endfor %}
              </tbody>
            </table>
          </details>
        {% endif %}
      </div>

      {% if correspondent_v2_enabled %}
      <div class="card">
        <h3>Correspondent V2 Sources — Week {{ week.number }}</h3>
        <p class="muted">
          Source text is stored as untrusted material and cannot change picks, scores,
          standings, or payouts. Workflow: ingest sources, classify them, inspect the
          results below, then generate the V2 recap.
        </p>
        <div style="padding:12px; margin:12px 0; border:1px solid #bae6fd; border-radius:8px; background:#f0f9ff;">
          <div class="version-actions">
            <form method="post" action="{{ url_for('admin_classify_correspondent_sources') }}">
              <input type="hidden" name="week" value="{{ week.number }}">
              <button class="btn primary" type="submit"
                      {% if not openai_configured or not correspondent_sources %}disabled{% endif %}>
                Classify Sources
              </button>
            </form>
            {% if correspondent_classification_jobs %}
              <form method="post" action="{{ url_for('admin_sync_correspondent_classification_jobs') }}">
                <input type="hidden" name="week" value="{{ week.number }}">
                <button class="btn" type="submit" {% if not openai_configured %}disabled{% endif %}>
                  Check Classification Status
                </button>
              </form>
            {% endif %}
          </div>
          <p class="muted" style="margin:8px 0 0;">
            Classification runs asynchronously and remains separate from recap generation.
            Refreshing this page does not hold an OpenAI request open.
          </p>
          {% if correspondent_classification_jobs %}
            <table style="margin-top:10px;">
              <thead><tr><th>Pass</th><th>Status</th><th>Progress</th><th>Prompt</th><th>Updated</th></tr></thead>
              <tbody>
                {% for job in correspondent_classification_jobs %}
                  <tr>
                    <td>{{ job.pass_number }}</td>
                    <td>{{ job.status }}</td>
                    <td>{{ job.completed_count }}/{{ job.total_count }} completed{% if job.failed_count %}; {{ job.failed_count }} failed{% endif %}</td>
                    <td>{{ job.prompt_version }}</td>
                    <td>{{ job.last_synced_at or job.submitted_at or job.created_at }}</td>
                  </tr>
                  {% if job.error_message %}
                    <tr><td colspan="5" class="notice error">{{ job.error_message }}</td></tr>
                  {% endif %}
                  {% for item in job.items if item.status == 'failed' %}
                    <tr>
                      <td colspan="5" class="muted">
                        Source #{{ item.source_id }} failed: {{ item.error_message or 'No result returned' }}
                      </td>
                    </tr>
                  {% endfor %}
                {% endfor %}
              </tbody>
            </table>
          {% endif %}
        </div>
        <form class="source-form" method="post" action="{{ url_for('admin_add_correspondent_source') }}">
          <input type="hidden" name="week" value="{{ week.number }}">
          <div style="display:flex; gap:8px; flex-wrap:wrap;">
            <label>Type
              <select name="source_type">
                <option value="curated_post">Curated X post</option>
                <option value="member_dm">Member DM submission</option>
                <option value="match_news">Match news</option>
                <option value="manual">Other manual context</option>
              </select>
            </label>
            <label>Author <input name="author_name" maxlength="160"></label>
            <label>Source URL <input name="canonical_url" type="url" maxlength="1000"></label>
          </div>
          <label>Source text<br><textarea name="body_text" maxlength="5000" required></textarea></label>
          <label>Optional submission note<br><textarea name="submission_note" maxlength="1000" style="min-height:70px;"></textarea></label>
          <div><button class="btn" type="submit">Add V2 Source</button></div>
        </form>

        {% if correspondent_sources %}
          <table style="margin-top:12px;">
            <thead><tr><th>ID</th><th>Type</th><th>Author</th><th>Source</th><th>Status</th><th>Classification</th><th>Latest V2 recap</th><th>Action</th></tr></thead>
            <tbody>
              {% for source in correspondent_sources %}
                <tr>
                  <td>#{{ source.id }}</td>
                  <td>{{ source.source_type }}</td>
                  <td>{{ source.author_name or '—' }}</td>
                  <td>
                    {% if source.canonical_url %}<a href="{{ source.canonical_url }}" target="_blank" rel="noopener noreferrer">Open</a> · {% endif %}
                    {{ source.body_text[:220] }}{% if source.body_text|length > 220 %}…{% endif %}
                  </td>
                  <td>{{ source.status }}</td>
                  <td>
                    {% set classification = correspondent_classifications.get(source.id) %}
                    {% if classification %}
                      <strong>{{ classification.route }}</strong><br>
                      <span class="muted">
                        {{ classification.pickem_impact }} · {{ classification.article_use }} ·
                        {{ classification.confidence }}<br>{{ classification.reason }}
                      </span>
                    {% elif source.id in correspondent_signal_only_source_ids %}
                      <strong>SIGNAL_ONLY</strong><br>
                      <span class="muted">Structural routing; not sent to the article classifier.</span>
                    {% else %}
                      <span class="muted">Not classified</span>
                    {% endif %}
                  </td>
                  <td>
                    {% if latest_v2_recap %}
                      {% if source.id in latest_v2_used_source_ids %}
                        <span class="official">Used in revision {{ latest_v2_recap.revision }}</span>
                      {% else %}
                        <span class="muted">Not used in revision {{ latest_v2_recap.revision }}</span>
                      {% endif %}
                    {% else %}
                      —
                    {% endif %}
                  </td>
                  <td>
                    <form method="post" action="{{ url_for('admin_set_correspondent_source_status', source_id=source.id) }}">
                      <input type="hidden" name="week" value="{{ week.number }}">
                      {% if source.status == 'accepted' %}
                        <input type="hidden" name="status" value="excluded">
                        <button class="btn" type="submit">Exclude</button>
                      {% else %}
                        <input type="hidden" name="status" value="accepted">
                        <button class="btn" type="submit">Include</button>
                      {% endif %}
                    </form>
                  </td>
                </tr>
              {% endfor %}
            </tbody>
          </table>
        {% else %}
          <p class="muted">No external sources have been added for this week.</p>
        {% endif %}
      </div>
      {% endif %}

      <div class="card">
        <h3>Results — Week {{ week.number }} ({{ week.status }})</h3>
        <form method="post" action="{{ url_for('admin_set_results') }}">
          <input type="hidden" name="week" value="{{ week.number }}">
          <table>
            <thead>
              <tr><th>#</th><th>Home</th><th>Away</th><th>Outcome</th></tr>
            </thead>
            <tbody>
              {% for f in fixtures %}
                <tr>
                  <td>#{{ f.match_number }}</td>
                  <td>{{ f.home }}</td>
                  <td>{{ f.away }}</td>
                  <td>
                    <select name="outcome_{{ f.id }}">
                      <option value="">(no change)</option>
                      <option value="{{ f.home }}" {% if results.get(f.id)=='Home' %}selected{% endif %}>{{ f.home }}</option>
                      <option value="{{ f.away }}" {% if results.get(f.id)=='Away' %}selected{% endif %}>{{ f.away }}</option>
                      <option value="Draw" {% if results.get(f.id)=='Draw' %}selected{% endif %}>Draw</option>
                    </select>
                  </td>
                </tr>
              {% endfor %}
            </tbody>
          </table>
          <div style="margin-top:10px; display:flex; gap:8px; align-items:center;">
            <button class="btn primary" type="submit">Save Changes</button>
            <label style="display:flex; gap:6px; align-items:center;">
              <input type="checkbox" name="force_status" value="provisional">
              Mark week as provisional after save
            </label>
          </div>
        </form>
        <p class="muted" style="margin-top:8px;">Admin edits bypass the UI lock — use carefully.</p>
      </div>
      {% else %}
        <div class="card muted">No active-season weeks have been initialized yet.</div>
      {% endif %}
    {% endif %}
  </div>
</body>
</html>
"""

ADMIN_SESSION_KEY = "is_admin"


def correspondent_v2_enabled() -> bool:
    return os.environ.get("CORRESPONDENT_V2_ENABLED", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }


def correspondent_default_version() -> str:
    configured = os.environ.get("CORRESPONDENT_DEFAULT_VERSION", "v1").strip().lower()
    if configured == "v2" and correspondent_v2_enabled():
        return "v2"
    return "v1"


def is_admin_session() -> bool:
    return bool(session.get(ADMIN_SESSION_KEY, False))


JOIN_HTML = """
<!doctype html>
<html><head><meta charset="utf-8"><title>Join</title>
  <style>body{font-family:-apple-system,system-ui,Segoe UI,Roboto,sans-serif;margin:24px;} .card{border:1px solid #ddd;border-radius:12px;padding:16px;}</style>
</head><body>
<div class="card">
  <h2>Join EPL Pick 'Em</h2>
  <form method="post">
    <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
      <label>Name <input name="name" required></label>
      <label>Room Code <input name="room_code" required></label>
      <button type="submit">Enter</button>
    </div>
  </form>
  <p class="muted">Allowed players: {{ allowed_names|join(', ') }}</p>
</div>
</body></html>
"""

CURRENT_PARTIAL = """
{% set wk = current_week %}
<div class="row">
  <div class="col">
    <div class="card">
      <h3>{{ season.name }} — Week {{ wk.number }} — Hello, {{ you.name }}</h3>
      <div class="muted">Room: {{ wk.room_code }}</div>
      <div>Status: <span class="status {{ wk.status }}">{{ wk.status|capitalize }}</span></div>
      {% if season.is_archived %}<div class="badge" style="margin-top:8px;">Archived — read only</div>{% endif %}
    </div>

    <div class="card" id="fixtures" hx-get="{{ url_for('fixtures_partial', week_number=wk.number, season=season.code) }}" hx-trigger="load">
      Loading fixtures...
    </div>

    <div class="card" id="scores" hx-get="{{ url_for('scores_partial', week_number=wk.number, season=season.code) }}" hx-trigger="load" hx-swap="outerHTML">
      Loading scores...
    </div>

  </div>

  <div class="col">
    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:center; gap:8px;">
        <h4 style="margin:0;">Season Leaders</h4>
        <a href="#" hx-get="{{ url_for('tab_stats', season=season.code) }}" hx-target="#main" hx-swap="innerHTML" hx-push-url="true">View all stats</a>
      </div>
      <div class="leader-grid" style="margin-top:10px;">
        {% for stat in leader_stats %}
          <div class="leader-stat">
            <div class="leader-label">{{ stat['label'] }}</div>
            <div class="leader-value">{{ stat['value'] }}</div>
            <div class="leader-detail">{{ stat['detail'] }}</div>
          </div>
        {% endfor %}
      </div>
    </div>

    <div id="matchups" class="card" hx-get="{{ url_for('matchups_partial', week_number=wk.number, season=season.code) }}" hx-trigger="load" hx-swap="outerHTML">
      Loading matchups...
    </div>
  </div>
</div>
"""

OPEN_PARTIAL = """
<div class="card">
  <h3>{{ season.name }} — Open Weeks</h3>
  <p class="muted">Any week not yet finalized shows up here. Auto-finalizes when all fixtures have results.</p>
  <table>
    <thead><tr><th>Week</th><th>Status</th><th>Completed Fixtures</th><th>Total Fixtures</th></tr></thead>
    <tbody>
      {% for row in open_rows %}
        <tr>
          <td><a href="#" hx-get="{{ url_for('tab_current', force_week=row['week'], season=season.code) }}" hx-target="#main" hx-swap="innerHTML" hx-push-url="true">Week {{ row['week'] }}</a></td>
          <td><span class="status {{ row['status'] }}">{{ row['status']|capitalize }}</span></td>
          <td>{{ row['done'] }}</td>
          <td>{{ row['total'] }}</td>
        </tr>
      {% endfor %}
      {% if not open_rows %}
        <tr><td colspan="4" class="muted">All weeks are finalized ✅</td></tr>
      {% endif %}
    </tbody>
  </table>
</div>
"""

SEASON_PARTIAL = """
<div class="card">
  <div style="display:flex; justify-content:space-between; gap:12px; align-items:center; flex-wrap:wrap;">
    <h3 style="margin:0;">{{ selected_season.name }} Summary</h3>
    <form hx-get="{{ url_for('tab_season') }}" hx-target="#main" hx-swap="innerHTML" hx-push-url="true">
      <label>Season
        <select name="season" onchange="this.form.requestSubmit()">
          {% for season in seasons %}
            <option value="{{ season.code }}" {% if season.id == selected_season.id %}selected{% endif %}>{{ season.name }}{% if season.is_archived %} (Archived){% endif %}</option>
          {% endfor %}
        </select>
      </label>
    </form>
  </div>
  <p class="muted">Cumulative points from finalized weeks. Net = For – Against.</p>
  <div class="table-scroll">
    <table class="centered-table season-summary-table">
      <thead><tr><th>Rank</th><th>Player</th><th>For</th><th>Against</th><th>Net</th></tr></thead>
      <tbody>
        {% for row in season_rows %}
          <tr><td>{{ row['rank'] }}</td><td>{{ row['name'] }}</td><td>{{ row['for'] }}</td><td>{{ row['against'] }}</td><td>{{ row['net'] }}</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

  <details style="margin-top:14px;">
    <summary class="btn" style="display:inline-block;">Show detailed breakdown</summary>
    <div class="table-scroll" style="margin-top:10px;">
      <table class="centered-table detailed-season-table">
        <thead>
          <tr>
            <th>Player</th>
            <th class="for-header">Correct</th><th class="for-header">Incorrect</th>
            <th class="for-header">Draws</th><th class="for-header">For Net</th>
            <th class="against-header against-start">Against Correct</th>
            <th class="against-header">Against Incorrect</th><th class="against-header">Against Draws</th>
            <th class="against-header">Against Net</th>
            <th class="total-net-header">Total Net</th>
          </tr>
        </thead>
        <tbody>
          {% for row in season_rows %}
            <tr>
              <td>{{ row['name'] }}</td>
              <td>{{ row['correct'] }}</td><td>{{ row['incorrect'] }}</td><td>{{ row['draws'] }}</td>
              <td>{{ row['for_net'] }}</td>
              <td class="against-start">{{ row['against_correct'] }}</td>
              <td>{{ row['against_incorrect'] }}</td><td>{{ row['against_draws'] }}</td>
              <td>{{ row['against_net'] }}</td>
              <td class="total-net-cell">{{ row['total_net'] }}</td>
            </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </details>
</div>

<div class="card">
  <h4>Weekly rollup</h4>
  <table class="centered-table">
    <thead>
      <tr>
        <th>Week</th>
        {% for p in players %}<th>{{ p.name }}</th>{% endfor %}
        <th>Status</th>
      </tr>
    </thead>
    <tbody>
      {% for wk in weeks %}
      <tr>
        <td><a href="#" hx-get="{{ url_for('tab_current', force_week=wk.number, season=selected_season.code) }}" hx-target="#main" hx-swap="innerHTML" hx-push-url="true">Week {{ wk.number }}</a></td>
        {% for p in players %}
          <td>{{ weekly_points[wk.number].get(p.id, 0) }}</td>
        {% endfor %}
        <td><span class="status {{ wk.status }}">{{ wk.status|capitalize }}</span></td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
"""

STATS_PARTIAL = """
<div class="card">
  <div style="display:flex; justify-content:space-between; gap:12px; align-items:center; flex-wrap:wrap;">
    <h3 style="margin:0;">{{ selected_season.name }} Statistics</h3>
    <form hx-get="{{ url_for('tab_stats') }}" hx-target="#main" hx-swap="innerHTML" hx-push-url="true"
          style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
      <label>Season
        <select name="season" onchange="this.form.requestSubmit()">
          {% for season in seasons %}
            <option value="{{ season.code }}" {% if season.id == selected_season.id %}selected{% endif %}>{{ season.name }}{% if season.is_archived %} (Archived){% endif %}</option>
          {% endfor %}
        </select>
      </label>
      <label>Player
        <select name="player" onchange="this.form.requestSubmit()">
          {% for player in players %}
            <option value="{{ player.id }}" {% if selected_player and player.id == selected_player.id %}selected{% endif %}>{{ player.name }}</option>
          {% endfor %}
        </select>
      </label>
      <label>Club
        <select name="club" onchange="this.form.requestSubmit()">
          <option value="">All clubs</option>
          {% for club_name in club_names %}
            <option value="{{ club_name }}" {% if club_filter == club_name %}selected{% endif %}>{{ club_name }}</option>
          {% endfor %}
        </select>
      </label>
      <label>Minimum picks
        <select name="min_picks" onchange="this.form.requestSubmit()">
          {% for choice in [1, 3, 5, 10] %}
            <option value="{{ choice }}" {% if min_picks == choice %}selected{% endif %}>{{ choice }}</option>
          {% endfor %}
        </select>
      </label>
      <label>Club sort
        <select name="club_sort" onchange="this.form.requestSubmit()">
          <option value="best" {% if club_sort == 'best' %}selected{% endif %}>Best record</option>
          <option value="worst" {% if club_sort == 'worst' %}selected{% endif %}>Worst record</option>
          <option value="most" {% if club_sort == 'most' %}selected{% endif %}>Most picked</option>
        </select>
      </label>
    </form>
  </div>
  <p class="muted">Only finalized weeks are included.</p>
  <div class="leader-grid">
    {% for stat in leader_stats %}
      <div class="leader-stat">
        <div class="leader-label">{{ stat['label'] }}</div>
        <div class="leader-value">{{ stat['value'] }}</div>
        <div class="leader-detail">{{ stat['detail'] }}</div>
      </div>
    {% endfor %}
  </div>
</div>

<div class="card">
  <h4>Head-to-Head{% if selected_player %} — {{ selected_player.name }}{% endif %}</h4>
  <div class="table-scroll">
    <table class="centered-table detailed-season-table">
      <thead>
        <tr>
          <th>Opponent</th><th>W-D-L</th>
          <th class="for-header">Correct</th><th class="for-header">Incorrect</th>
          <th class="for-header">Draws</th><th class="for-header">For Net</th>
          <th class="against-header against-start">Against Correct</th>
          <th class="against-header">Against Incorrect</th>
          <th class="against-header">Against Draws</th>
          <th class="against-header">Against Net</th>
          <th class="total-net-header">$ Net</th>
        </tr>
      </thead>
      <tbody>
        {% for row in head_to_head %}
          <tr>
            <td>{{ row['opponent'] }}</td><td>{{ row['wins'] }}-{{ row['ties'] }}-{{ row['losses'] }}</td>
            <td>{{ row['correct'] }}</td><td>{{ row['incorrect'] }}</td><td>{{ row['draws'] }}</td>
            <td>{{ '%+d'|format(row['net_for']) }}</td>
            <td class="against-start">{{ row['against_correct'] }}</td>
            <td>{{ row['against_incorrect'] }}</td><td>{{ row['against_draws'] }}</td>
            <td>{{ '%+d'|format(row['net_against']) }}</td>
            <td class="total-net-cell">{{ row['money_display'] }}</td>
          </tr>
        {% endfor %}
        {% if not head_to_head %}<tr><td colspan="11" class="muted">No finalized head-to-head matchups yet.</td></tr>{% endif %}
      </tbody>
    </table>
  </div>
</div>

<div class="card">
  <h4>Club-Picking Record{% if selected_player %} — {{ selected_player.name }}{% endif %}</h4>
  <div class="table-scroll">
    <table class="centered-table">
      <thead><tr><th>Club</th><th>Picks</th><th>Correct</th><th>Incorrect</th><th>Draws</th><th>Net</th><th>Accuracy</th></tr></thead>
      <tbody>
        {% for row in club_records %}
          <tr>
            <td>{{ row['club'] }}</td><td>{{ row['picks'] }}</td><td>{{ row['correct'] }}</td>
            <td>{{ row['incorrect'] }}</td><td>{{ row['draws'] }}</td><td>{{ '%+d'|format(row['net']) }}</td>
            <td>{{ row['accuracy_display'] }}</td>
          </tr>
        {% endfor %}
        {% if not club_records %}<tr><td colspan="7" class="muted">No club records match these filters yet.</td></tr>{% endif %}
      </tbody>
    </table>
  </div>
</div>
"""

FIXTURES_PARTIAL = """
<h4>Fixtures</h4>
<div class="grid">
{% for f in fixtures %}
  <div class="chip"><span>#{{ f.match_number }}: {{ f.home }} vs {{ f.away }}</span></div>
{% endfor %}
</div>
"""

MATCHUPS_PARTIAL = """
<div id="matchups" class="card">
  <h4>Matchups (click to pick)</h4>
  {% if read_only %}<p class="muted">This season is archived. Picks are available for viewing only.</p>{% endif %}
  {% for m in matchups %}
    <div class="card">
    <div style="display:flex; justify-content:space-between; align-items:center;">
      <div><strong>{{ m['a'] }}</strong> vs <strong>{{ m['b'] }}</strong></div>
      <div class="badge">First picker: {{ m['first'] }}</div>
    </div>

    <div class="muted">It's {{ m['turn_name'] }}'s turn</div>

    <div class="row">
      <div class="col">
        <h5>Available</h5>
        {% if m['available'] and not read_only %}
          <form hx-post="{{ url_for('make_pick') }}" hx-trigger="confirmedPick" hx-target="#matchups" hx-swap="outerHTML">
            <input type="hidden" name="week" value="{{ week.number }}">
            <input type="hidden" name="season" value="{{ season.code }}">
            <input type="hidden" name="matchup_id" value="{{ m['id'] }}">
            <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
              <label>Game
                <select name="fixture_id" required onchange="syncTeamOptions(this)">
                  {% for fx in m['available'] %}
                    <option value="{{ fx['id'] }}" data-home="{{ fx['home'] }}" data-away="{{ fx['away'] }}">#{{ fx['match_number'] }}: {{ fx['home'] }} vs {{ fx['away'] }}</option>
                  {% endfor %}
                </select>
              </label>
              <label>Team
                <select name="team" required>
                  <option value="{{ m['available'][0]['home'] }}">{{ m['available'][0]['home'] }}</option>
                  <option value="{{ m['available'][0]['away'] }}">{{ m['available'][0]['away'] }}</option>
                </select>
              </label>
              {% if you.id == m['turn_id'] %}
                <button class="btn primary" type="button" onclick="openPickConfirm(this.form)">Pick</button>
              {% else %}
                <button class="btn" type="button" disabled title="Not your turn">Pick</button>
              {% endif %}
            </div>
          </form>
        {% elif read_only %}
          <div class="muted">Archived — no additional picks can be submitted.</div>
        {% else %}
          <div class="muted">All games picked in this matchup.</div>
        {% endif %}
      </div>
      <div class="col">
        <h5>Pick Log</h5>
        {% if m['log'] %}
        <ul>
          {% for p in m['log'] %}
            <li>{{ p['when'] }} — <strong>{{ p['player'] }}</strong> picked <strong>{{ p['team'] }}</strong> in #{{ p['match_number'] }} ({{ p['home'] }} vs {{ p['away'] }})</li>
          {% endfor %}
        </ul>
        {% else %}
          <div class="muted">(no picks yet)</div>
        {% endif %}
      </div>
    </div>
    </div>
  {% endfor %}
</div>
"""

SCORES_PARTIAL = """
<div id="scores" class="card">
<h4>Scores & Payouts</h4>
<div class="table-scroll">
<table class="centered-table">
  <thead><tr><th>Player</th><th>Points</th><th>Games Finalized</th><th>Correct</th><th>Incorrect</th><th>Draws</th></tr></thead>
  <tbody>
    {% for row in scores %}
      <tr>
        <td>{{ row['name'] }}</td><td>{{ row['points'] }}</td><td>{{ row['games_finalized'] }}</td>
        <td>{{ row['correct'] }}</td><td>{{ row['incorrect'] }}</td><td>{{ row['draws'] }}</td>
      </tr>
    {% endfor %}
  </tbody>
</table>
</div>
<h5>Payouts ($5/pt)</h5>
<table>
  <thead><tr><th>From</th><th>To</th><th>Point Diff</th><th>Payout</th></tr></thead>
  <tbody>
    {% for p in payouts %}
      <tr><td>{{ p.get('from','-') }}</td><td>{{ p.get('to','-') }}</td><td>{{ p.get('points',0) }}</td><td>{{ p.get('payout',0) }}</td></tr>
    {% endfor %}
  </tbody>
</table>

<div class="card">
  <h5>Results — Week {{ week.number }}</h5>
  <table>
    <thead><tr><th>#</th><th>Home</th><th>Away</th><th>Score</th><th>Outcome</th><th>Source</th></tr></thead>
    <tbody>
      {% for fr in fixtures_with_results %}
        <tr>
          <td>#{{ fr['match_number'] }}</td>
          <td>{{ fr['home'] }}</td>
          <td>{{ fr['away'] }}</td>
          <td>{{ fr['score_display'] }}</td>
          <td>{{ fr['outcome_display'] }}</td>
          <td>{{ fr['source_display'] }}</td>
        </tr>
      {% endfor %}
    </tbody>
  </table>
</div>

<div class="card">
  <h5>Enter Results</h5>
  {% if not read_only %}
  <form hx-post="{{ url_for('set_result') }}" hx-target="#scores" hx-swap="outerHTML">
    <input type="hidden" name="week" value="{{ week.number }}">
    <input type="hidden" name="season" value="{{ season.code }}">
    <label>Match #
      <select name="fixture_id"
              hx-get="{{ url_for('outcome_options') }}"
              hx-target="#outcome-box"
              hx-swap="innerHTML"
              hx-trigger="load, change"
              hx-include="closest form">
        {% for f in fixtures %}
          <option value="{{ f.id }}">#{{ f.match_number }}: {{ f.home }} vs {{ f.away }}</option>
        {% endfor %}
      </select>
    </label>
    <label>Outcome</label>
    <div id="outcome-box">
      <select name="outcome" id="outcome-options">
        <option>(pick a match above)</option>
      </select>
    </div>
    <button class="btn" type="submit">Set Result</button>
  </form>
  {% else %}
    <div class="muted">Archived — results are read only.</div>
  {% endif %}
</div>
</div>
"""


# -------------------- App + DB --------------------
DB_PATH = os.environ.get("DB_PATH", "sqlite:///pickem.db")
SECRET = os.environ.get("FLASK_SECRET", "devsecret")

app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET
app.config["SESSION_TYPE"] = "filesystem"
Session(app)


def render_recap_markdown(value: Optional[str]) -> Markup:
    """Render the Correspondent's Markdown using a deliberately small safe subset."""
    rendered = markdown.markdown(value or "")
    cleaned = bleach.clean(
        rendered,
        tags={
            "p", "strong", "em", "h3", "h4", "h5",
            "ul", "ol", "li", "blockquote", "br",
        },
        attributes={},
        strip=True,
    )
    return Markup(cleaned)


app.jinja_env.filters["recap_markdown"] = render_recap_markdown

# For possible template inheritance later
app.jinja_loader = DictLoader({'base.html': BASE_HTML})

engine = create_engine(DB_PATH, connect_args={"check_same_thread": False})
SessionLocal = scoped_session(sessionmaker(bind=engine))
Base = declarative_base()

# --- SQLite performance pragmas (better concurrency) ---
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    try:
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=NORMAL;")
        cur.close()
    except Exception:
        pass

# --- Ensure sessions are cleaned up every request (prevents locks) ---
@app.teardown_appcontext
def remove_session(exception=None):
    SessionLocal.remove()

# -------------------- Models --------------------
class Player(Base):
    __tablename__ = "players"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)

class Season(Base):
    __tablename__ = "seasons"
    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    is_active = Column(Integer, nullable=False, default=0)
    is_archived = Column(Integer, nullable=False, default=0)
    api_competition_code = Column(String)
    api_season_year = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)

class Week(Base):
    __tablename__ = "weeks"
    id = Column(Integer, primary_key=True)
    season_id = Column(Integer, ForeignKey("seasons.id"), nullable=False)
    number = Column(Integer, nullable=False)
    room_code = Column(String, nullable=False)
    status = Column(String, default="drafting") # drafting | provisional | finalized
    finalized_at = Column(DateTime)
    season = relationship("Season")
    __table_args__ = (UniqueConstraint("season_id", "number", name="uix_season_week_number"),)

class Fixture(Base):
    __tablename__ = "fixtures"
    id = Column(Integer, primary_key=True)
    week_id = Column(Integer, ForeignKey("weeks.id"), nullable=False)
    match_number = Column(Integer, nullable=False)
    home = Column(String, nullable=False)
    away = Column(String, nullable=False)
    external_match_id = Column(Integer, unique=True)
    kickoff_utc = Column(DateTime)
    api_status = Column(String)
    __table_args__ = (UniqueConstraint("week_id", "match_number", name="uix_week_matchnumber"),)

class Matchup(Base):
    __tablename__ = "matchups"
    id = Column(Integer, primary_key=True)
    week_id = Column(Integer, ForeignKey("weeks.id"), nullable=False)
    player_a_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    player_b_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    first_picker_id = Column(Integer, ForeignKey("players.id"), nullable=False)

    player_a = relationship("Player", foreign_keys=[player_a_id])
    player_b = relationship("Player", foreign_keys=[player_b_id])
    first_picker = relationship("Player", foreign_keys=[first_picker_id])

class Pick(Base):
    __tablename__ = "picks"
    id = Column(Integer, primary_key=True)
    matchup_id = Column(Integer, ForeignKey("matchups.id"), nullable=False)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    fixture_id = Column(Integer, ForeignKey("fixtures.id"), nullable=False)
    team = Column(String, nullable=False)  # team name selected
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("matchup_id", "fixture_id", name="uix_matchup_fixture_once"),)
    player = relationship("Player")
    fixture = relationship("Fixture")
    matchup = relationship("Matchup")

class Result(Base):
    __tablename__ = "results"
    id = Column(Integer, primary_key=True)
    fixture_id = Column(Integer, ForeignKey("fixtures.id"), nullable=False, unique=True)
    outcome = Column(String, nullable=False)  # Home|Away|Draw
    home_score = Column(Integer)
    away_score = Column(Integer)
    source = Column(String, nullable=False, default="manual")
    updated_at = Column(DateTime, default=datetime.utcnow)
    fixture = relationship("Fixture")


class ApiSyncState(Base):
    __tablename__ = "api_sync_states"
    id = Column(Integer, primary_key=True)
    season_id = Column(Integer, ForeignKey("seasons.id"), nullable=False, unique=True)
    provider = Column(String, nullable=False, default=FOOTBALL_DATA_PROVIDER)
    last_attempt_at = Column(DateTime)
    last_success_at = Column(DateTime)
    last_error = Column(String)
    requests_remaining = Column(Integer)
    reset_seconds = Column(Integer)
    fixtures_imported = Column(Integer, nullable=False, default=0)
    results_imported = Column(Integer, nullable=False, default=0)
    pending_matches = Column(Integer, nullable=False, default=0)
    unmatched_matches = Column(Integer, nullable=False, default=0)
    season = relationship("Season")


class WeeklyRecap(Base):
    __tablename__ = "weekly_recaps"
    id = Column(Integer, primary_key=True)
    week_id = Column(Integer, ForeignKey("weeks.id"), nullable=False)
    revision = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default="generating")
    title = Column(String)
    body_markdown = Column(Text)
    context_json = Column(Text, nullable=False)
    context_hash = Column(String, nullable=False)
    prompt_version = Column(String, nullable=False)
    model = Column(String, nullable=False)
    provider_response_id = Column(String)
    error_message = Column(Text)
    correspondent_version = Column(String, nullable=False, default="v1")
    source_count = Column(Integer, nullable=False, default=0)
    external_context_hash = Column(String)
    automation_key = Column(String, unique=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime)
    week = relationship("Week")
    __table_args__ = (
        UniqueConstraint("week_id", "revision", name="uix_weekly_recap_revision"),
    )


class CorrespondentSource(Base):
    __tablename__ = "correspondent_sources"
    id = Column(Integer, primary_key=True)
    week_id = Column(Integer, ForeignKey("weeks.id"), nullable=False)
    fixture_id = Column(Integer, ForeignKey("fixtures.id"))
    provider = Column(String, nullable=False)
    source_type = Column(String, nullable=False)
    external_id = Column(String)
    canonical_url = Column(String)
    author_name = Column(String)
    body_text = Column(Text, nullable=False)
    published_at = Column(DateTime)
    submitted_by_player_id = Column(Integer, ForeignKey("players.id"))
    submission_note = Column(Text)
    metadata_json = Column(Text)
    content_hash = Column(String, nullable=False)
    status = Column(String, nullable=False, default="accepted")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    week = relationship("Week")
    fixture = relationship("Fixture")
    submitted_by = relationship("Player")
    __table_args__ = (
        UniqueConstraint(
            "week_id", "provider", "content_hash",
            name="uix_correspondent_source_content",
        ),
    )


class CorrespondentSourceClassification(Base):
    """Versioned, auditable semantic classification for one stored source."""

    __tablename__ = "correspondent_source_classifications"
    id = Column(Integer, primary_key=True)
    source_id = Column(
        Integer,
        ForeignKey("correspondent_sources.id"),
        nullable=False,
    )
    prompt_version = Column(String, nullable=False)
    pass_number = Column(Integer, nullable=False)
    pickem_impact = Column(String, nullable=False)
    editorial_functions_json = Column(Text, nullable=False)
    article_use = Column(String, nullable=False)
    confidence = Column(String, nullable=False)
    route = Column(String, nullable=False)
    reason_codes_json = Column(Text, nullable=False)
    reason = Column(Text, nullable=False)
    model = Column(String, nullable=False)
    provider_response_id = Column(String)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    source = relationship("CorrespondentSource")
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "prompt_version",
            "pass_number",
            name="uix_source_classification_version_pass",
        ),
    )


class CorrespondentClassificationJob(Base):
    """Durable state for one OpenAI source-classification Batch."""

    __tablename__ = "correspondent_classification_jobs"
    id = Column(Integer, primary_key=True)
    season_id = Column(Integer, ForeignKey("seasons.id"), nullable=False)
    week_id = Column(Integer, ForeignKey("weeks.id"), nullable=False)
    prompt_version = Column(String, nullable=False)
    pass_number = Column(Integer, nullable=False)
    model = Column(String, nullable=False)
    openai_batch_id = Column(String, unique=True)
    input_file_id = Column(String)
    output_file_id = Column(String)
    error_file_id = Column(String)
    status = Column(String, nullable=False, default="submitting")
    total_count = Column(Integer, nullable=False, default=0)
    completed_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    error_message = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    submitted_at = Column(DateTime)
    last_synced_at = Column(DateTime)
    completed_at = Column(DateTime)
    season = relationship("Season")
    week = relationship("Week")
    items = relationship(
        "CorrespondentClassificationJobItem",
        back_populates="job",
        cascade="all, delete-orphan",
    )


class CorrespondentClassificationJobItem(Base):
    """Persistent source-to-custom_id mapping for a classification Batch."""

    __tablename__ = "correspondent_classification_job_items"
    id = Column(Integer, primary_key=True)
    job_id = Column(
        Integer,
        ForeignKey("correspondent_classification_jobs.id"),
        nullable=False,
    )
    source_id = Column(
        Integer,
        ForeignKey("correspondent_sources.id"),
        nullable=False,
    )
    custom_id = Column(String, nullable=False, unique=True)
    active_reservation_key = Column(String, unique=True)
    status = Column(String, nullable=False, default="pending")
    provider_response_id = Column(String)
    error_message = Column(Text)
    imported_at = Column(DateTime)
    job = relationship("CorrespondentClassificationJob", back_populates="items")
    source = relationship("CorrespondentSource")
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "source_id",
            name="uix_classification_job_source",
        ),
    )


class CorrespondentXCollectionState(Base):
    """Authoritative one-page-at-a-time X collection checkpoint for a week."""

    __tablename__ = "correspondent_x_collection_states"
    id = Column(Integer, primary_key=True)
    week_id = Column(Integer, ForeignKey("weeks.id"), nullable=False, unique=True)
    status = Column(String, nullable=False, default="ready")
    window_start = Column(DateTime, nullable=False)
    window_end = Column(DateTime, nullable=False)
    next_token = Column(String)
    page_count = Column(Integer, nullable=False, default=0)
    retrieved_count = Column(Integer, nullable=False, default=0)
    persisted_count = Column(Integer, nullable=False, default=0)
    lease_token = Column(String, unique=True)
    lease_expires_at = Column(DateTime)
    last_error = Column(Text)
    cap_reached_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    week = relationship("Week")


class CorrespondentXPageReceipt(Base):
    """Idempotency receipt proving one paid X page was checkpointed."""

    __tablename__ = "correspondent_x_page_receipts"
    id = Column(Integer, primary_key=True)
    collection_id = Column(
        Integer,
        ForeignKey("correspondent_x_collection_states.id"),
        nullable=False,
    )
    claim_token = Column(String, nullable=False, unique=True)
    request_token = Column(String)
    next_token = Column(String)
    received_count = Column(Integer, nullable=False, default=0)
    created_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    collection = relationship("CorrespondentXCollectionState")


class RecapSourceUsage(Base):
    __tablename__ = "recap_source_usages"
    id = Column(Integer, primary_key=True)
    recap_id = Column(Integer, ForeignKey("weekly_recaps.id"), nullable=False)
    source_id = Column(Integer, ForeignKey("correspondent_sources.id"), nullable=False)
    recap = relationship("WeeklyRecap")
    source = relationship("CorrespondentSource")
    __table_args__ = (
        UniqueConstraint("recap_id", "source_id", name="uix_recap_source_usage"),
    )


class WeeklyRecapSelection(Base):
    __tablename__ = "weekly_recap_selections"
    id = Column(Integer, primary_key=True)
    week_id = Column(Integer, ForeignKey("weeks.id"), nullable=False, unique=True)
    recap_id = Column(Integer, ForeignKey("weekly_recaps.id"), nullable=False)
    selected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    week = relationship("Week")
    recap = relationship("WeeklyRecap")

def _database_file_path(target_engine) -> Optional[Path]:
    """Return the SQLite database file path, excluding in-memory databases."""
    if target_engine.dialect.name != "sqlite":
        return None
    database = target_engine.url.database
    if not database or database == ":memory:":
        return None
    return Path(database).expanduser().resolve()


def _backup_database(target_engine, suffix: str) -> Optional[Path]:
    """Create a one-time SQLite backup before a schema migration."""
    source_path = _database_file_path(target_engine)
    if source_path is None or not source_path.exists():
        return None
    backup_path = source_path.with_name(f"{source_path.stem}.{suffix}.db")
    if backup_path.exists():
        return backup_path
    source = sqlite3.connect(str(source_path))
    destination = sqlite3.connect(str(backup_path))
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    return backup_path


def _backup_legacy_database(target_engine) -> Optional[Path]:
    return _backup_database(target_engine, "pre_seasons")


def ensure_database_schema(target_engine=engine) -> bool:
    """Create the current schema and safely migrate a legacy single-season DB.

    Returns True only when the legacy weeks table was migrated.
    """
    migrated = False
    if target_engine.dialect.name != "sqlite":
        Base.metadata.create_all(target_engine)
        return migrated

    raw = target_engine.raw_connection()
    cursor = raw.cursor()
    try:
        weeks_exists = cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='weeks'"
        ).fetchone()
        week_columns = []
        if weeks_exists:
            week_columns = [row[1] for row in cursor.execute("PRAGMA table_info(weeks)").fetchall()]

        if weeks_exists and "season_id" not in week_columns:
            _backup_legacy_database(target_engine)
            cursor.execute("PRAGMA foreign_keys=OFF")
            cursor.execute("PRAGMA legacy_alter_table=ON")
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS seasons (
                    id INTEGER PRIMARY KEY,
                    code VARCHAR NOT NULL UNIQUE,
                    name VARCHAR NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 0,
                    is_archived INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME
                )
                """
            )
            legacy_season = cursor.execute(
                "SELECT id FROM seasons WHERE code=?", ("year-1",)
            ).fetchone()
            if legacy_season:
                legacy_season_id = legacy_season[0]
                cursor.execute(
                    "UPDATE seasons SET name=?, is_active=0, is_archived=1 WHERE id=?",
                    ("Year 1", legacy_season_id),
                )
            else:
                cursor.execute(
                    "INSERT INTO seasons (code, name, is_active, is_archived, created_at) VALUES (?, ?, 0, 1, ?)",
                    ("year-1", "Year 1", datetime.utcnow()),
                )
                legacy_season_id = cursor.lastrowid

            legacy_count = cursor.execute("SELECT COUNT(*) FROM weeks").fetchone()[0]
            cursor.execute("ALTER TABLE weeks RENAME TO weeks_legacy")
            cursor.execute(
                """
                CREATE TABLE weeks (
                    id INTEGER PRIMARY KEY,
                    season_id INTEGER NOT NULL,
                    number INTEGER NOT NULL,
                    room_code VARCHAR NOT NULL,
                    status VARCHAR,
                    CONSTRAINT uix_season_week_number UNIQUE (season_id, number),
                    FOREIGN KEY(season_id) REFERENCES seasons(id)
                )
                """
            )
            cursor.execute(
                """
                INSERT INTO weeks (id, season_id, number, room_code, status)
                SELECT id, ?, number, room_code, status FROM weeks_legacy
                """,
                (legacy_season_id,),
            )
            migrated_count = cursor.execute("SELECT COUNT(*) FROM weeks").fetchone()[0]
            if migrated_count != legacy_count:
                raise RuntimeError("Season migration row-count check failed")
            cursor.execute("DROP TABLE weeks_legacy")
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_weeks_season_id ON weeks (season_id)")
            raw.commit()
            cursor.execute("PRAGMA legacy_alter_table=OFF")
            cursor.execute("PRAGMA foreign_keys=ON")
            migrated = True

        # Add football-data.org metadata without rebuilding or deleting any
        # existing Year 1/Year 2 records. SQLite's ADD COLUMN keeps legacy rows.
        api_columns = {
            "seasons": {
                "api_competition_code": "VARCHAR",
                "api_season_year": "INTEGER",
            },
            "fixtures": {
                "external_match_id": "INTEGER",
                "kickoff_utc": "DATETIME",
                "api_status": "VARCHAR",
            },
            "results": {
                "home_score": "INTEGER",
                "away_score": "INTEGER",
                "source": "VARCHAR DEFAULT 'manual'",
                "updated_at": "DATETIME",
            },
        }
        missing_columns = []
        for table, columns in api_columns.items():
            table_exists = cursor.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not table_exists:
                continue
            existing = {
                row[1] for row in cursor.execute(f"PRAGMA table_info({table})").fetchall()
            }
            missing_columns.extend(
                (table, name, definition)
                for name, definition in columns.items()
                if name not in existing
            )

        if missing_columns:
            _backup_database(target_engine, "pre_football_api")
            for table, name, definition in missing_columns:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
            cursor.execute(
                "UPDATE results SET source='manual' WHERE source IS NULL OR source=''"
            )
            cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_fixtures_external_match_id "
                "ON fixtures(external_match_id) WHERE external_match_id IS NOT NULL"
            )
            raw.commit()

        # V2 is additive: preserve every V1 recap and mark legacy rows as V1.
        recap_exists = cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='weekly_recaps'"
        ).fetchone()
        if recap_exists:
            existing_recap_columns = {
                row[1]
                for row in cursor.execute("PRAGMA table_info(weekly_recaps)").fetchall()
            }
            recap_columns = {
                "correspondent_version": "VARCHAR DEFAULT 'v1'",
                "source_count": "INTEGER DEFAULT 0",
                "external_context_hash": "VARCHAR",
            }
            missing_recap_columns = [
                (name, definition)
                for name, definition in recap_columns.items()
                if name not in existing_recap_columns
            ]
            if missing_recap_columns:
                _backup_database(target_engine, "pre_correspondent_v2")
                for name, definition in missing_recap_columns:
                    cursor.execute(
                        f"ALTER TABLE weekly_recaps ADD COLUMN {name} {definition}"
                    )
                cursor.execute(
                    "UPDATE weekly_recaps SET correspondent_version='v1' "
                    "WHERE correspondent_version IS NULL OR correspondent_version=''"
                )
                cursor.execute(
                    "UPDATE weekly_recaps SET source_count=0 WHERE source_count IS NULL"
                )
                raw.commit()
            if "automation_key" not in existing_recap_columns:
                _backup_database(target_engine, "pre_recap_automation")
                cursor.execute(
                    "ALTER TABLE weekly_recaps ADD COLUMN automation_key VARCHAR"
                )
            cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_weekly_recaps_automation_key "
                "ON weekly_recaps(automation_key) WHERE automation_key IS NOT NULL"
            )
            raw.commit()

        week_exists = cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='weeks'"
        ).fetchone()
        if week_exists:
            existing_week_columns = {
                row[1] for row in cursor.execute("PRAGMA table_info(weeks)").fetchall()
            }
            if "finalized_at" not in existing_week_columns:
                _backup_database(target_engine, "pre_week_finalized_at")
                cursor.execute("ALTER TABLE weeks ADD COLUMN finalized_at DATETIME")
                raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        cursor.close()
        raw.close()

    Base.metadata.create_all(target_engine)
    return migrated


ensure_database_schema(engine)


# -------------------- football-data.org client --------------------
class FootballDataError(RuntimeError):
    pass


class FootballDataRateLimitError(FootballDataError):
    pass


class FootballDataClient:
    """Small v4 client that obeys the provider's rate-limit response headers."""

    def __init__(
        self,
        token: str,
        base_url: str = FOOTBALL_DATA_BASE_URL,
        opener: Callable[..., Any] = urlopen,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        max_inline_wait: int = 65,
    ):
        if not token.strip():
            raise FootballDataError("FOOTBALL_DATA_API_KEY is not configured")
        self.token = token.strip()
        self.base_url = base_url.rstrip("/")
        self.opener = opener
        self.sleeper = sleeper
        self.monotonic = monotonic
        self.max_inline_wait = max_inline_wait
        self.requests_remaining: Optional[int] = None
        self.reset_seconds: Optional[int] = None
        self._not_before = 0.0

    @staticmethod
    def _header_int(headers, name: str) -> Optional[int]:
        if headers is None:
            return None
        value = headers.get(name)
        if value is None:
            return None
        try:
            return max(0, int(float(value)))
        except (TypeError, ValueError):
            return None

    def _capture_rate_headers(self, headers) -> None:
        remaining = self._header_int(headers, "X-Requests-Available-Minute")
        reset = self._header_int(headers, "X-RequestCounter-Reset")
        if remaining is not None:
            self.requests_remaining = remaining
        if reset is not None:
            self.reset_seconds = reset
        if self.requests_remaining == 0 and self.reset_seconds:
            self._not_before = max(
                self._not_before,
                self.monotonic() + self.reset_seconds + 0.25,
            )

    def _wait_for_allowance(self) -> None:
        wait_seconds = self._not_before - self.monotonic()
        if wait_seconds <= 0:
            return
        if wait_seconds > self.max_inline_wait:
            raise FootballDataRateLimitError(
                f"API allowance resets in approximately {int(wait_seconds)} seconds"
            )
        self.sleeper(wait_seconds)
        self._not_before = 0.0

    def get_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        query = urlencode(params or {})
        url = f"{self.base_url}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{query}"
        request_object = Request(url, headers={"X-Auth-Token": self.token})

        for attempt in range(2):
            self._wait_for_allowance()
            try:
                with self.opener(request_object, timeout=25) as response:
                    self._capture_rate_headers(response.headers)
                    payload = json.loads(response.read().decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise FootballDataError("Football API returned an unexpected response")
                    return payload
            except HTTPError as exc:
                self._capture_rate_headers(exc.headers)
                retry_after = self._header_int(exc.headers, "Retry-After")
                wait_seconds = retry_after or self.reset_seconds or 0
                if exc.code == 429 and attempt == 0 and 0 < wait_seconds <= self.max_inline_wait:
                    self.sleeper(wait_seconds + 0.25)
                    self._not_before = 0.0
                    continue
                if exc.code == 429:
                    raise FootballDataRateLimitError(
                        f"Football API rate limit reached; retry in about {wait_seconds or 'a few'} seconds"
                    ) from exc
                raise FootballDataError(f"Football API returned HTTP {exc.code}") from exc
            except (URLError, TimeoutError) as exc:
                raise FootballDataError(f"Could not reach football-data.org: {exc.reason if hasattr(exc, 'reason') else exc}") from exc
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise FootballDataError("Football API returned invalid JSON") from exc

        raise FootballDataError("Football API request failed")

    def competition_matches(self, competition: str, season_year: int) -> List[Dict[str, Any]]:
        payload = self.get_json(
            f"competitions/{competition}/matches",
            {"season": int(season_year)},
        )
        matches = payload.get("matches")
        if not isinstance(matches, list):
            raise FootballDataError("Football API response did not include a match list")
        return matches


def football_data_client() -> FootballDataClient:
    return FootballDataClient(os.environ.get("FOOTBALL_DATA_API_KEY", ""))


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def format_utc_timestamp(value: Optional[datetime]) -> str:
    if value is None:
        return "Never"
    return value.strftime("%b %d, %Y %I:%M:%S %p UTC").replace(" 0", " ")


def parse_api_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FootballDataError(f"Invalid fixture date from football API: {value}") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def api_team_name(team: Dict[str, Any]) -> str:
    name = team.get("shortName") or team.get("name")
    if not isinstance(name, str) or not name.strip():
        raise FootballDataError("Football API returned a fixture without a team name")
    return name.strip()


def validate_api_matches(matches: List[Dict[str, Any]]) -> Dict[int, List[Dict[str, Any]]]:
    """Validate a complete 38-matchday Premier League schedule before any DB writes."""
    by_matchday: Dict[int, List[Dict[str, Any]]] = {}
    external_ids = set()
    for match in matches:
        external_id = match.get("id")
        matchday = match.get("matchday")
        if not isinstance(external_id, int) or not isinstance(matchday, int):
            raise FootballDataError("Football API returned a fixture without an ID or matchday")
        if external_id in external_ids:
            raise FootballDataError(f"Duplicate football API match ID: {external_id}")
        external_ids.add(external_id)
        api_team_name(match.get("homeTeam") or {})
        api_team_name(match.get("awayTeam") or {})
        parse_api_datetime(match.get("utcDate"))
        by_matchday.setdefault(matchday, []).append(match)

    expected_matchdays = set(range(1, 39))
    if set(by_matchday) != expected_matchdays:
        missing = sorted(expected_matchdays - set(by_matchday))
        raise FootballDataError(
            f"Expected all 38 Premier League matchdays; missing: {missing or 'none'}"
        )
    wrong_counts = {
        matchday: len(items) for matchday, items in by_matchday.items() if len(items) != 10
    }
    if wrong_counts or len(matches) != 380:
        raise FootballDataError(
            f"Expected 380 fixtures (10 per matchday); received {len(matches)}"
        )
    return by_matchday


def _sync_state(db, season: Season) -> ApiSyncState:
    state = db.query(ApiSyncState).filter_by(season_id=season.id).first()
    if state is None:
        state = ApiSyncState(season_id=season.id, provider=FOOTBALL_DATA_PROVIDER)
        db.add(state)
        db.flush()
    return state


def _copy_rate_state(state: ApiSyncState, client: FootballDataClient) -> None:
    state.requests_remaining = client.requests_remaining
    state.reset_seconds = client.reset_seconds


def _set_week_status_without_commit(db, week: Week) -> None:
    if week.season and week.season.is_archived:
        return
    done, total = count_results_for_week(db, week)
    if done == 0:
        new_status = "drafting"
    elif done < total:
        new_status = "provisional"
    else:
        new_status = "finalized"
    week.status = new_status
    if new_status == "finalized" and week.finalized_at is None:
        week.finalized_at = utcnow()
    db.add(week)


def init_season_from_api(
    players: List[str],
    room_code: str,
    season_code: str = "year-2",
    season_name: str = "2026–27",
    competition: str = DEFAULT_API_COMPETITION,
    season_year: int = DEFAULT_API_SEASON_YEAR,
    client: Optional[FootballDataClient] = None,
) -> Season:
    clean_players = list(dict.fromkeys(name.strip() for name in players if name.strip()))
    if len(clean_players) != 6:
        raise ValueError("Exactly six unique player names are required")
    if not room_code.strip():
        raise ValueError("A room code is required")

    api_client = client or football_data_client()
    matches = api_client.competition_matches(competition, season_year)
    by_matchday = validate_api_matches(matches)

    db = SessionLocal()
    try:
        season = db.query(Season).filter_by(code=season_code).first()
        if season is not None and db.query(Week).filter_by(season_id=season.id).count():
            raise RuntimeError(
                f"{season.name} already contains weeks; API import will not replace them"
            )
        if season is None:
            season = Season(code=season_code, name=season_name)
            db.add(season)
            db.flush()

        season.name = season_name
        season.is_active = 1
        season.is_archived = 0
        season.api_competition_code = competition
        season.api_season_year = int(season_year)
        db.query(Season).filter(Season.id != season.id).update(
            {Season.is_active: 0}, synchronize_session=False
        )

        for name in clean_players:
            if db.query(Player).filter_by(name=name).first() is None:
                db.add(Player(name=name))
        db.flush()
        player_rows = db.query(Player).filter(Player.name.in_(clean_players)).all()
        players_by_name = {player.name: player for player in player_rows}

        match_number = 0
        for matchday in range(1, 39):
            week = Week(
                season_id=season.id,
                number=matchday,
                room_code=room_code.strip(),
                status="drafting",
            )
            db.add(week)
            db.flush()
            ordered_matches = sorted(
                by_matchday[matchday],
                key=lambda item: (item.get("utcDate") or "", item["id"]),
            )
            for match in ordered_matches:
                match_number += 1
                db.add(Fixture(
                    week_id=week.id,
                    match_number=match_number,
                    home=api_team_name(match["homeTeam"]),
                    away=api_team_name(match["awayTeam"]),
                    external_match_id=match["id"],
                    kickoff_utc=parse_api_datetime(match.get("utcDate")),
                    api_status=match.get("status"),
                ))

            shuffled_names = clean_players[:]
            random.shuffle(shuffled_names)
            for index in range(0, len(shuffled_names), 2):
                player_a = players_by_name[shuffled_names[index]]
                player_b = players_by_name[shuffled_names[index + 1]]
                first_picker = random.choice([player_a, player_b])
                db.add(Matchup(
                    week_id=week.id,
                    player_a_id=player_a.id,
                    player_b_id=player_b.id,
                    first_picker_id=first_picker.id,
                ))

        state = _sync_state(db, season)
        state.last_attempt_at = utcnow()
        state.last_success_at = state.last_attempt_at
        state.last_error = None
        state.fixtures_imported = match_number
        state.results_imported = 0
        state.pending_matches = match_number
        state.unmatched_matches = 0
        _copy_rate_state(state, api_client)
        db.commit()
        return season
    except Exception:
        db.rollback()
        raise


def sync_results_from_api(
    season: Season,
    client: Optional[FootballDataClient] = None,
) -> Dict[str, int]:
    if season.is_archived:
        raise RuntimeError("Archived seasons cannot be synchronized")
    if not season.api_competition_code or season.api_season_year is None:
        raise RuntimeError(f"{season.name} is not linked to football-data.org")

    db = SessionLocal()
    season = db.get(Season, season.id)
    state = _sync_state(db, season)
    state.last_attempt_at = utcnow()
    state.last_error = None
    db.commit()
    api_client = client or football_data_client()

    try:
        matches = api_client.competition_matches(
            season.api_competition_code, season.api_season_year
        )
        api_matches = {
            match["id"]: match for match in matches if isinstance(match.get("id"), int)
        }
        fixtures = db.query(Fixture).join(Week).filter(Week.season_id == season.id).all()
        local_ids = {
            fixture.external_match_id for fixture in fixtures
            if fixture.external_match_id is not None
        }
        applied_results = 0
        manual_overrides = 0
        for fixture in fixtures:
            match = api_matches.get(fixture.external_match_id)
            if match is None:
                continue
            fixture.kickoff_utc = parse_api_datetime(match.get("utcDate"))
            fixture.api_status = match.get("status")
            if match.get("status") != "FINISHED":
                continue
            full_time = (match.get("score") or {}).get("fullTime") or {}
            home_score = full_time.get("home")
            away_score = full_time.get("away")
            if not isinstance(home_score, int) or not isinstance(away_score, int):
                continue
            outcome = "Draw"
            if home_score > away_score:
                outcome = "Home"
            elif away_score > home_score:
                outcome = "Away"

            result = db.query(Result).filter_by(fixture_id=fixture.id).first()
            if result is not None and result.source == "manual":
                manual_overrides += 1
                continue
            if result is None:
                result = Result(fixture_id=fixture.id)
                db.add(result)
            result.outcome = outcome
            result.home_score = home_score
            result.away_score = away_score
            result.source = FOOTBALL_DATA_PROVIDER
            result.updated_at = utcnow()
            applied_results += 1

        db.flush()
        weeks = db.query(Week).filter_by(season_id=season.id).all()
        for week in weeks:
            _set_week_status_without_commit(db, week)

        result_count = db.query(Result).join(Fixture).join(Week).filter(
            Week.season_id == season.id
        ).count()
        fixtures_without_external_id = sum(
            1 for fixture in fixtures if fixture.external_match_id is None
        )
        unmatched = (
            fixtures_without_external_id
            + len(local_ids - set(api_matches))
            + len(set(api_matches) - local_ids)
        )
        state = _sync_state(db, season)
        state.last_success_at = utcnow()
        state.last_error = None
        state.results_imported = applied_results
        state.pending_matches = max(0, len(fixtures) - result_count)
        state.unmatched_matches = unmatched
        _copy_rate_state(state, api_client)
        db.commit()
        return {
            "results_imported": applied_results,
            "pending_matches": state.pending_matches,
            "unmatched_matches": unmatched,
            "manual_overrides": manual_overrides,
        }
    except Exception as exc:
        db.rollback()
        state = _sync_state(db, season)
        state.last_attempt_at = utcnow()
        state.last_error = str(exc)[:500]
        _copy_rate_state(state, api_client)
        db.commit()
        raise

# -------------------- Helpers --------------------
def current_player(db):
    name = session.get("player_name")
    if not name:
        return None
    return db.query(Player).filter_by(name=name).first()


def active_season(db) -> Optional[Season]:
    season = db.query(Season).filter_by(is_active=1).order_by(Season.id.desc()).first()
    if season:
        return season
    season = db.query(Season).filter_by(is_archived=0).order_by(Season.id.desc()).first()
    if season:
        return season
    return db.query(Season).order_by(Season.id.desc()).first()


def requested_season(db, code: Optional[str] = None) -> Optional[Season]:
    season_code = code or request.args.get("season") or request.form.get("season")
    if season_code:
        season = db.query(Season).filter_by(code=season_code).first()
        if season:
            return season
    return active_season(db)


def season_players(db, season: Season) -> List[Player]:
    matchups = db.query(Matchup).join(Week).filter(Week.season_id == season.id).all()
    player_ids = set()
    for matchup in matchups:
        player_ids.update((matchup.player_a_id, matchup.player_b_id))
    if not player_ids:
        return db.query(Player).order_by(Player.name.asc()).all()
    return db.query(Player).filter(Player.id.in_(player_ids)).order_by(Player.name.asc()).all()


def season_week(db, season: Season, number: int) -> Optional[Week]:
    return db.query(Week).filter_by(season_id=season.id, number=number).first()

def matchup_order(m: Matchup) -> Tuple[int, int]:
    first = m.first_picker_id
    second = m.player_b_id if first == m.player_a_id else m.player_a_id
    return first, second

def compute_next_turn(db, m: Matchup) -> int:
    picks = db.query(Pick).filter_by(matchup_id=m.id).order_by(Pick.created_at.asc(), Pick.id.asc()).all()
    count = len(picks)
    first, second = matchup_order(m)
    chunk = count // 2
    order = [first, second] if chunk % 2 == 0 else [second, first]
    return order[count % 2]

def available_fixtures_for_matchup(db, m: Matchup) -> list:
    picked_fixture_ids = [p.fixture_id for p in db.query(Pick.fixture_id).filter_by(matchup_id=m.id).all()]
    fixtures = db.query(Fixture).filter_by(week_id=m.week_id).order_by(Fixture.match_number.asc()).all()
    return [f for f in fixtures if f.id not in picked_fixture_ids]

def weekly_points_map(db, week: Week) -> Dict[int, int]:
    players = {p.id: 0 for p in db.query(Player).all()}
    results = {r.fixture_id: r.outcome for r in db.query(Result).join(Fixture).filter(Fixture.week_id==week.id)}
    for p in db.query(Pick).join(Matchup).filter(Matchup.week_id==week.id):
        fx = p.fixture
        outcome = results.get(fx.id)
        if outcome is None:
            continue
        if outcome == "Draw":
            delta = 0
        elif outcome == "Home":
            delta = 1 if p.team == fx.home else -1
        else:
            delta = 1 if p.team == fx.away else -1
        players[p.player_id] = players.get(p.player_id, 0) + delta
    return players

def weekly_for_against(db, week: Week) -> Dict[int, Dict[str,int]]:
    points = weekly_points_map(db, week)
    out: Dict[int, Dict[str,int]] = {pid: {'for': 0, 'against': 0} for pid in points.keys()}
    for m in db.query(Matchup).filter_by(week_id=week.id).all():
        pa = points.get(m.player_a_id, 0); pb = points.get(m.player_b_id, 0)
        out[m.player_a_id]['for'] += pa; out[m.player_a_id]['against'] += pb
        out[m.player_b_id]['for'] += pb; out[m.player_b_id]['against'] += pa
    return out

def weekly_pick_records(db, week: Week) -> Dict[int, Dict[str, int]]:
    """Return correct/incorrect/draw pick counts for every player in a week."""
    records: Dict[int, Dict[str, int]] = {
        p.id: {'correct': 0, 'incorrect': 0, 'draws': 0}
        for p in db.query(Player).all()
    }
    results = {
        r.fixture_id: r.outcome
        for r in db.query(Result).join(Fixture).filter(Fixture.week_id == week.id)
    }
    picks = db.query(Pick).join(Matchup).filter(Matchup.week_id == week.id).all()
    for pick in picks:
        outcome = results.get(pick.fixture_id)
        if outcome is None:
            continue
        if outcome == "Draw":
            bucket = "draws"
        else:
            winning_team = pick.fixture.home if outcome == "Home" else pick.fixture.away
            bucket = "correct" if pick.team == winning_team else "incorrect"
        records[pick.player_id][bucket] += 1
    return records

def season_detailed_totals_finalized(db, season: Season) -> Dict[int, Dict[str, int]]:
    """Aggregate pick records and opponents' pick records across finalized weeks."""
    totals: Dict[int, Dict[str, int]] = {
        p.id: {
            'correct': 0, 'incorrect': 0, 'draws': 0,
            'against_correct': 0, 'against_incorrect': 0, 'against_draws': 0,
        }
        for p in season_players(db, season)
    }
    for week in db.query(Week).filter_by(season_id=season.id, status="finalized").order_by(Week.number.asc()).all():
        records = weekly_pick_records(db, week)
        for matchup in db.query(Matchup).filter_by(week_id=week.id).all():
            a_record = records[matchup.player_a_id]
            b_record = records[matchup.player_b_id]
            for key in ('correct', 'incorrect', 'draws'):
                totals[matchup.player_a_id][key] += a_record[key]
                totals[matchup.player_b_id][key] += b_record[key]
                totals[matchup.player_a_id][f'against_{key}'] += b_record[key]
                totals[matchup.player_b_id][f'against_{key}'] += a_record[key]
    return totals

def season_totals_finalized(db, season: Season) -> Dict[int, Dict[str,int]]:
    totals: Dict[int, Dict[str,int]] = {}
    for wk in db.query(Week).filter_by(season_id=season.id).order_by(Week.number.asc()).all():
        if wk.status != "finalized":
            continue
        fa = weekly_for_against(db, wk)
        for pid, vals in fa.items():
            if pid not in totals:
                totals[pid] = {'for': 0, 'against': 0, 'net': 0}
            totals[pid]['for'] += vals['for']
            totals[pid]['against'] += vals['against']
    for pid, vals in totals.items():
        vals['net'] = vals['for'] - vals['against']
    return totals


def head_to_head_for_player(db, season: Season, player: Player) -> List[Dict[str, Any]]:
    rows: Dict[int, Dict[str, Any]] = {}
    finalized_weeks = db.query(Week).filter_by(
        season_id=season.id, status="finalized"
    ).order_by(Week.number.asc()).all()
    for week in finalized_weeks:
        points = weekly_points_map(db, week)
        records = weekly_pick_records(db, week)
        matchups = db.query(Matchup).filter_by(week_id=week.id).all()
        for matchup in matchups:
            if player.id not in (matchup.player_a_id, matchup.player_b_id):
                continue
            opponent_id = (
                matchup.player_b_id
                if matchup.player_a_id == player.id
                else matchup.player_a_id
            )
            opponent = db.get(Player, opponent_id)
            row = rows.setdefault(opponent_id, {
                "opponent": opponent.name,
                "wins": 0,
                "ties": 0,
                "losses": 0,
                "correct": 0,
                "incorrect": 0,
                "draws": 0,
                "net_for": 0,
                "against_correct": 0,
                "against_incorrect": 0,
                "against_draws": 0,
                "net_against": 0,
                "money_net": 0,
            })
            player_points = points.get(player.id, 0)
            opponent_points = points.get(opponent_id, 0)
            if player_points > opponent_points:
                row["wins"] += 1
            elif player_points < opponent_points:
                row["losses"] += 1
            else:
                row["ties"] += 1
            player_record = records.get(
                player.id, {"correct": 0, "incorrect": 0, "draws": 0}
            )
            for key in ("correct", "incorrect", "draws"):
                row[key] += player_record[key]
            opponent_record = records.get(
                opponent_id, {"correct": 0, "incorrect": 0, "draws": 0}
            )
            for key in ("correct", "incorrect", "draws"):
                row[f"against_{key}"] += opponent_record[key]
            row["net_for"] += player_points
            row["net_against"] += opponent_points

    for row in rows.values():
        row["money_net"] = (row["net_for"] - row["net_against"]) * 5
        amount = row["money_net"]
        row["money_display"] = f"{'+$' if amount >= 0 else '-$'}{abs(amount)}"
    return sorted(rows.values(), key=lambda row: row["opponent"].lower())


def club_records_for_player(
    db,
    season: Season,
    player: Player,
    club_filter: str = "",
    min_picks: int = 1,
    sort_mode: str = "best",
) -> List[Dict[str, Any]]:
    results = {
        result.fixture_id: result.outcome
        for result in db.query(Result).join(Fixture).join(Week).filter(
            Week.season_id == season.id,
            Week.status == "finalized",
        )
    }
    picks = db.query(Pick).join(Matchup).join(Week).filter(
        Week.season_id == season.id,
        Week.status == "finalized",
        Pick.player_id == player.id,
    ).all()
    records: Dict[str, Dict[str, Any]] = {}
    for pick in picks:
        outcome = results.get(pick.fixture_id)
        if outcome is None:
            continue
        row = records.setdefault(pick.team, {
            "club": pick.team,
            "correct": 0,
            "incorrect": 0,
            "draws": 0,
        })
        if outcome == "Draw":
            row["draws"] += 1
        else:
            winning_team = pick.fixture.home if outcome == "Home" else pick.fixture.away
            row["correct" if pick.team == winning_team else "incorrect"] += 1

    rows = []
    for row in records.values():
        row["picks"] = row["correct"] + row["incorrect"] + row["draws"]
        row["net"] = row["correct"] - row["incorrect"]
        decisions = row["correct"] + row["incorrect"]
        row["accuracy"] = row["correct"] / decisions if decisions else 0.0
        row["accuracy_display"] = f"{row['accuracy'] * 100:.1f}%" if decisions else "—"
        if row["picks"] >= min_picks and (not club_filter or row["club"] == club_filter):
            rows.append(row)

    if sort_mode == "worst":
        rows.sort(key=lambda row: (row["net"], row["accuracy"], -row["picks"], row["club"].lower()))
    elif sort_mode == "most":
        rows.sort(key=lambda row: (-row["picks"], -row["net"], row["club"].lower()))
    else:
        rows.sort(key=lambda row: (-row["net"], -row["accuracy"], -row["picks"], row["club"].lower()))
    return rows


def season_leader_stats(
    db,
    season: Season,
    max_week_number: Optional[int] = None,
) -> List[Dict[str, str]]:
    players = season_players(db, season)
    week_query = db.query(Week).filter_by(season_id=season.id, status="finalized")
    if max_week_number is not None:
        week_query = week_query.filter(Week.number <= max_week_number)
    finalized_weeks = week_query.order_by(Week.number.asc()).all()
    no_data = {"value": "No one yet", "detail": "No finalized weeks"}
    if not finalized_weeks:
        return [
            {"label": "Biggest weekly win", **no_data},
            {"label": "Most correct picks", **no_data},
            {"label": "Most incorrect picks", **no_data},
            {"label": "Most perfect weeks", **no_data},
            {"label": "Longest win streak", **no_data},
            {"label": "Longest losing streak", **no_data},
        ]

    biggest_win: Optional[Dict[str, Any]] = None
    perfect_counts = {player.id: 0 for player in players}
    current_streaks = {player.id: 0 for player in players}
    longest_streaks = {player.id: 0 for player in players}
    current_losing_streaks = {player.id: 0 for player in players}
    longest_losing_streaks = {player.id: 0 for player in players}
    names = {player.id: player.name for player in players}

    for week in finalized_weeks:
        points = weekly_points_map(db, week)
        records = weekly_pick_records(db, week)
        for player in players:
            record = records.get(
                player.id, {"correct": 0, "incorrect": 0, "draws": 0}
            )
            if record == {"correct": 5, "incorrect": 0, "draws": 0}:
                perfect_counts[player.id] += 1

        for matchup in db.query(Matchup).filter_by(week_id=week.id).all():
            a_points = points.get(matchup.player_a_id, 0)
            b_points = points.get(matchup.player_b_id, 0)
            margin = abs(a_points - b_points)
            if margin:
                winner_id = matchup.player_a_id if a_points > b_points else matchup.player_b_id
                loser_id = matchup.player_b_id if winner_id == matchup.player_a_id else matchup.player_a_id
                candidate = {
                    "winner": names[winner_id],
                    "loser": names[loser_id],
                    "margin": margin,
                    "week": week.number,
                }
                if biggest_win is None or margin > biggest_win["margin"]:
                    biggest_win = candidate
            for player_id, opponent_points in (
                (matchup.player_a_id, b_points),
                (matchup.player_b_id, a_points),
            ):
                if points.get(player_id, 0) > opponent_points:
                    current_streaks[player_id] += 1
                    longest_streaks[player_id] = max(
                        longest_streaks[player_id], current_streaks[player_id]
                    )
                    current_losing_streaks[player_id] = 0
                elif points.get(player_id, 0) < opponent_points:
                    current_streaks[player_id] = 0
                    current_losing_streaks[player_id] += 1
                    longest_losing_streaks[player_id] = max(
                        longest_losing_streaks[player_id],
                        current_losing_streaks[player_id],
                    )
                else:
                    current_streaks[player_id] = 0
                    current_losing_streaks[player_id] = 0

    detailed = {
        player.id: {"correct": 0, "incorrect": 0, "draws": 0}
        for player in players
    }
    for week in finalized_weeks:
        records = weekly_pick_records(db, week)
        for player in players:
            for key in ("correct", "incorrect", "draws"):
                detailed[player.id][key] += records.get(player.id, {}).get(key, 0)
    max_correct = max((detailed.get(player.id, {}).get("correct", 0) for player in players), default=0)
    correct_leaders = [
        player.name for player in players
        if detailed.get(player.id, {}).get("correct", 0) == max_correct and max_correct > 0
    ]
    max_incorrect = max((detailed.get(player.id, {}).get("incorrect", 0) for player in players), default=0)
    incorrect_leaders = [
        player.name for player in players
        if detailed.get(player.id, {}).get("incorrect", 0) == max_incorrect and max_incorrect > 0
    ]
    max_perfect = max(perfect_counts.values(), default=0)
    perfect_leaders = [names[player_id] for player_id, count in perfect_counts.items() if count == max_perfect and max_perfect > 0]
    max_streak = max(longest_streaks.values(), default=0)
    streak_leaders = [names[player_id] for player_id, count in longest_streaks.items() if count == max_streak and max_streak > 0]
    max_losing_streak = max(longest_losing_streaks.values(), default=0)
    losing_streak_leaders = [
        names[player_id]
        for player_id, count in longest_losing_streaks.items()
        if count == max_losing_streak and max_losing_streak > 0
    ]

    return [
        {
            "label": "Biggest weekly win",
            "value": biggest_win["winner"] if biggest_win else "No one yet",
            "detail": (
                f"+{biggest_win['margin']} vs {biggest_win['loser']} · Week {biggest_win['week']}"
                if biggest_win else "No matchup wins yet"
            ),
        },
        {
            "label": "Most correct picks",
            "value": ", ".join(correct_leaders) if correct_leaders else "No one yet",
            "detail": f"{max_correct} correct" if max_correct else "No completed picks yet",
        },
        {
            "label": "Most incorrect picks",
            "value": ", ".join(incorrect_leaders) if incorrect_leaders else "No one yet",
            "detail": f"{max_incorrect} incorrect" if max_incorrect else "No incorrect picks yet",
        },
        {
            "label": "Most perfect weeks",
            "value": ", ".join(perfect_leaders) if perfect_leaders else "No one yet",
            "detail": f"{max_perfect} perfect week{'s' if max_perfect != 1 else ''}" if max_perfect else "A perfect week is 5–0–0",
        },
        {
            "label": "Longest win streak",
            "value": ", ".join(streak_leaders) if streak_leaders else "No one yet",
            "detail": f"{max_streak} week{'s' if max_streak != 1 else ''}" if max_streak else "No winning streak yet",
        },
        {
            "label": "Longest losing streak",
            "value": ", ".join(losing_streak_leaders) if losing_streak_leaders else "No one yet",
            "detail": f"{max_losing_streak} week{'s' if max_losing_streak != 1 else ''}" if max_losing_streak else "No losing streak yet",
        },
    ]


def standings_through_week(
    db,
    season: Season,
    max_week_number: int,
) -> List[Dict[str, Any]]:
    """Return structured standings using finalized weeks up to a cutoff."""
    players = season_players(db, season)
    rows = {
        player.id: {
            "player_id": player.id,
            "player": player.name,
            "points_for": 0,
            "points_against": 0,
            "net_points": 0,
            "correct": 0,
            "incorrect": 0,
            "draws": 0,
        }
        for player in players
    }
    weeks = db.query(Week).filter(
        Week.season_id == season.id,
        Week.status == "finalized",
        Week.number <= max_week_number,
    ).order_by(Week.number.asc()).all()
    for week in weeks:
        for player_id, values in weekly_for_against(db, week).items():
            if player_id not in rows:
                continue
            rows[player_id]["points_for"] += values["for"]
            rows[player_id]["points_against"] += values["against"]
        for player_id, record in weekly_pick_records(db, week).items():
            if player_id not in rows:
                continue
            for key in ("correct", "incorrect", "draws"):
                rows[player_id][key] += record[key]

    standings = list(rows.values())
    for row in standings:
        row["net_points"] = row["points_for"] - row["points_against"]
    standings.sort(
        key=lambda row: (-row["net_points"], -row["correct"], row["player"].lower())
    )
    previous_key = None
    current_rank = 1
    for position, row in enumerate(standings, start=1):
        rank_key = (row["net_points"], row["correct"])
        if rank_key != previous_key:
            current_rank = position
            previous_key = rank_key
        row["rank"] = current_rank
    return standings


def _pick_evaluation(pick: Pick, result: Result) -> str:
    if result.outcome == "Draw":
        return "draw"
    winning_team = pick.fixture.home if result.outcome == "Home" else pick.fixture.away
    return "correct" if pick.team == winning_team else "incorrect"


def build_weekly_recap_context(db, week: Week) -> Dict[str, Any]:
    """Build the complete factual record supplied to the V1 Correspondent."""
    if week.status != "finalized":
        raise ValueError("A recap can only be generated for a finalized week")
    completed_results, total_fixtures = count_results_for_week(db, week)
    if not total_fixtures or completed_results != total_fixtures:
        raise ValueError("A recap requires a result for every fixture in the week")

    season = week.season or db.get(Season, week.season_id)
    players = season_players(db, season)
    names = {player.id: player.name for player in players}
    points = weekly_points_map(db, week)
    records = weekly_pick_records(db, week)
    results = {
        result.fixture_id: result
        for result in db.query(Result).join(Fixture).filter(Fixture.week_id == week.id)
    }
    fixtures = db.query(Fixture).filter_by(week_id=week.id).order_by(
        Fixture.match_number.asc()
    ).all()

    fixture_rows = []
    for fixture in fixtures:
        result = results[fixture.id]
        winning_team = None
        if result.outcome == "Home":
            winning_team = fixture.home
        elif result.outcome == "Away":
            winning_team = fixture.away
        fixture_rows.append({
            "fixture_id": fixture.id,
            "match_number": fixture.match_number,
            "home": fixture.home,
            "away": fixture.away,
            "kickoff_utc": fixture.kickoff_utc.isoformat() if fixture.kickoff_utc else None,
            "home_score": result.home_score,
            "away_score": result.away_score,
            "outcome": result.outcome.lower(),
            "winning_team": winning_team,
            "result_source": result.source,
        })

    matchup_rows = []
    for matchup in db.query(Matchup).filter_by(week_id=week.id).order_by(Matchup.id.asc()):
        a_points = points.get(matchup.player_a_id, 0)
        b_points = points.get(matchup.player_b_id, 0)
        margin = abs(a_points - b_points)
        winner_id = None
        loser_id = None
        if a_points > b_points:
            winner_id, loser_id = matchup.player_a_id, matchup.player_b_id
        elif b_points > a_points:
            winner_id, loser_id = matchup.player_b_id, matchup.player_a_id

        pick_rows = []
        picks = db.query(Pick).filter_by(matchup_id=matchup.id).order_by(
            Pick.created_at.asc(), Pick.id.asc()
        ).all()
        for pick in picks:
            result = results[pick.fixture_id]
            pick_rows.append({
                "pick_id": pick.id,
                "player_id": pick.player_id,
                "player": names[pick.player_id],
                "fixture_id": pick.fixture_id,
                "match_number": pick.fixture.match_number,
                "fixture": f"{pick.fixture.home} vs {pick.fixture.away}",
                "team_picked": pick.team,
                "evaluation": _pick_evaluation(pick, result),
            })

        matchup_rows.append({
            "matchup_id": matchup.id,
            "player_a": {
                "player_id": matchup.player_a_id,
                "player": names[matchup.player_a_id],
                "points": a_points,
                **records[matchup.player_a_id],
            },
            "player_b": {
                "player_id": matchup.player_b_id,
                "player": names[matchup.player_b_id],
                "points": b_points,
                **records[matchup.player_b_id],
            },
            "first_picker": names[matchup.first_picker_id],
            "winner": names[winner_id] if winner_id else None,
            "loser": names[loser_id] if loser_id else None,
            "tied": winner_id is None,
            "point_margin": margin,
            "payout_dollars": margin * 5,
            "picks": pick_rows,
        })

    standings_before = standings_through_week(db, season, week.number - 1)
    standings_after = standings_through_week(db, season, week.number)
    before_by_player = {row["player_id"]: row for row in standings_before}
    completed_weeks_before = db.query(Week).filter(
        Week.season_id == season.id,
        Week.status == "finalized",
        Week.number < week.number,
    ).count()
    rank_changes = []
    if completed_weeks_before:
        for row in standings_after:
            previous_rank = before_by_player[row["player_id"]]["rank"]
            rank_changes.append({
                "player_id": row["player_id"],
                "player": row["player"],
                "rank_before": previous_rank,
                "rank_after": row["rank"],
                "places_moved": previous_rank - row["rank"],
            })

    player_summaries = []
    for player in players:
        record = records[player.id]
        player_summaries.append({
            "player_id": player.id,
            "player": player.name,
            "points": points.get(player.id, 0),
            **record,
        })

    matchup_margins = [row["point_margin"] for row in matchup_rows]
    largest_margin = max(matchup_margins, default=0)
    smallest_margin = min(matchup_margins, default=0)
    best_points = max((row["points"] for row in player_summaries), default=0)
    worst_points = min((row["points"] for row in player_summaries), default=0)

    return {
        "schema_version": "weekly_recap.v1",
        "season": {
            "season_id": season.id,
            "code": season.code,
            "name": season.name,
        },
        "week": {
            "week_id": week.id,
            "number": week.number,
            "status": week.status,
            "fixture_count": total_fixtures,
        },
        "scoring_rules": {
            "correct_pick_points": 1,
            "incorrect_pick_points": -1,
            "drawn_fixture_points": 0,
            "payout_dollars_per_matchup_point": 5,
        },
        "fixtures": fixture_rows,
        "players": player_summaries,
        "matchups": matchup_rows,
        "standings_before": standings_before,
        "standings_after": standings_after,
        "rank_changes": rank_changes,
        "highlights": {
            "biggest_matchups": [
                row for row in matchup_rows if row["point_margin"] == largest_margin
            ],
            "closest_matchups": [
                row for row in matchup_rows if row["point_margin"] == smallest_margin
            ],
            "best_weekly_performers": [
                row for row in player_summaries if row["points"] == best_points
            ],
            "worst_weekly_performers": [
                row for row in player_summaries if row["points"] == worst_points
            ],
            "perfect_pick_records": [
                row for row in player_summaries
                if row["correct"] == 5 and row["incorrect"] == 0 and row["draws"] == 0
            ],
            "season_leaders_after_week": season_leader_stats(
                db, season, max_week_number=week.number
            ),
        },
    }


CORRESPONDENT_SOURCE_TYPES = {
    "curated_post",
    "member_dm",
    "match_news",
    "manual",
}
AUTOMATED_CORRESPONDENT_SOURCE_TYPES = CORRESPONDENT_SOURCE_TYPES - {"manual"}
CORRESPONDENT_INGEST_ALLOWED_FIELDS = {
    "season_code",
    "week_number",
    "provider",
    "source_type",
    "external_id",
    "canonical_url",
    "author_name",
    "body_text",
    "published_at",
    "submitted_by_player",
    "submission_note",
    "metadata",
}
CORRESPONDENT_INGEST_REQUIRED_FIELDS = {
    "season_code",
    "week_number",
    "provider",
    "source_type",
    "external_id",
    "body_text",
}
CORRESPONDENT_INGEST_MAX_BODY_BYTES = 25_000
CORRESPONDENT_BATCH_MAX_BODY_BYTES = 10_000_000
CORRESPONDENT_BATCH_MAX_SOURCES = 1_000
CORRESPONDENT_FINALIZATION_BUFFER = timedelta(hours=1)
CORRESPONDENT_X_WEEKLY_CAP = 1_000
CORRESPONDENT_X_PAGE_SIZE = 100
CORRESPONDENT_X_LEASE_DURATION = timedelta(minutes=10)
CORRESPONDENT_PROVIDER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class CorrespondentIngestConflict(ValueError):
    """Raised when an idempotency key is reused for different source data."""


class CorrespondentIngestNotFound(ValueError):
    """Raised when the requested application-owned target does not exist."""


def accepted_correspondent_sources(db, week: Week) -> List[CorrespondentSource]:
    return db.query(CorrespondentSource).filter_by(
        week_id=week.id,
        status="accepted",
    ).order_by(
        CorrespondentSource.created_at.asc(),
        CorrespondentSource.id.asc(),
    ).all()


def _correspondent_source_metadata(source: CorrespondentSource) -> Dict[str, Any]:
    try:
        metadata = json.loads(source.metadata_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return metadata if isinstance(metadata, dict) else {}


def correspondent_source_is_article_candidate(source: CorrespondentSource) -> bool:
    """Apply the approved structural routing without deleting the raw source."""
    metadata = _correspondent_source_metadata(source)
    approved_routing = metadata.get("approved_routing")
    if isinstance(approved_routing, Mapping):
        article_candidate = approved_routing.get("article_candidate")
        if isinstance(article_candidate, bool):
            return article_candidate

    objective_audit = metadata.get("objective_audit")
    if isinstance(objective_audit, Mapping):
        if objective_audit.get("is_retweet") is True:
            return False
        reference_types = objective_audit.get("reference_types")
        if isinstance(reference_types, list) and "retweeted" in reference_types:
            return False

    references = metadata.get("referenced_tweets")
    if isinstance(references, list):
        for reference in references:
            if isinstance(reference, Mapping) and reference.get("type") == "retweeted":
                return False
    return True


def classification_candidate_sources(
    db,
    week: Week,
) -> Tuple[List[Dict[str, Any]], List[CorrespondentSource]]:
    """Return model-ready candidates and signal-only sources from durable rows."""
    candidates: List[Dict[str, Any]] = []
    signal_only: List[CorrespondentSource] = []
    for source in accepted_correspondent_sources(db, week):
        if not correspondent_source_is_article_candidate(source):
            signal_only.append(source)
            continue
        candidates.append({
            "source_id": source.id,
            "provider": source.provider,
            "source_type": source.source_type,
            "external_id": source.external_id,
            "canonical_url": source.canonical_url,
            "author": source.author_name,
            "text": source.body_text,
            "published_at": (
                source.published_at.isoformat() if source.published_at else None
            ),
            "metadata": _correspondent_source_metadata(source),
        })
    return candidates, signal_only


def _classification_as_input(classification: SourceClassification) -> Dict[str, Any]:
    return {
        "pickem_impact": classification.pickem_impact,
        "editorial_functions": list(classification.editorial_functions),
        "article_use": classification.article_use,
        "confidence": classification.confidence,
        "route": classification.route,
        "reason_codes": list(classification.reason_codes),
        "reason": classification.reason,
    }


def store_source_classification(
    db,
    classification: SourceClassification,
    batch: ClassificationBatch,
    *,
    prompt_version: str = CLASSIFIER_PROMPT_VERSION,
) -> CorrespondentSourceClassification:
    """Idempotently store one classifier decision and its explanation."""
    record = db.query(CorrespondentSourceClassification).filter_by(
        source_id=classification.source_id,
        prompt_version=prompt_version,
        pass_number=batch.pass_number,
    ).first()
    if record is None:
        record = CorrespondentSourceClassification(
            source_id=classification.source_id,
            prompt_version=prompt_version,
            pass_number=batch.pass_number,
            created_at=utcnow(),
        )
    record.pickem_impact = classification.pickem_impact
    record.editorial_functions_json = json.dumps(
        list(classification.editorial_functions),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    record.article_use = classification.article_use
    record.confidence = classification.confidence
    record.route = classification.route
    record.reason_codes_json = json.dumps(
        list(classification.reason_codes),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    record.reason = classification.reason
    record.model = batch.model
    record.provider_response_id = batch.provider_response_id
    db.add(record)
    return record


def _classification_batches(
    candidates: List[Dict[str, Any]],
    batch_size: int,
) -> Iterable[List[Dict[str, Any]]]:
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")
    for start in range(0, len(candidates), batch_size):
        yield candidates[start:start + batch_size]


def classify_and_store_week_sources(
    db,
    week: Week,
    *,
    client: Any = None,
    model: Optional[str] = None,
    batch_size: int = 20,
) -> Dict[str, Any]:
    """Run the approved two-pass classifier and persist every decision."""
    league_context = build_weekly_recap_context(db, week)
    candidates, signal_only = classification_candidate_sources(db, week)
    if not candidates:
        raise ValueError("No article-candidate sources are available for classification")

    first_pass: Dict[int, SourceClassification] = {}
    for candidate_batch in _classification_batches(candidates, batch_size):
        result = classify_candidate_sources(
            candidate_batch,
            league_context,
            pass_number=1,
            client=client,
            model=model,
        )
        for classification in result.classifications:
            first_pass[classification.source_id] = classification
            store_source_classification(db, classification, result)
        db.commit()

    candidates_by_id = {candidate["source_id"]: candidate for candidate in candidates}
    review_candidates: List[Dict[str, Any]] = []
    for source_id, classification in first_pass.items():
        if classification.route != "AUTOMATED_REVIEW":
            continue
        enriched = dict(candidates_by_id[source_id])
        enriched["initial_classification"] = _classification_as_input(classification)
        review_candidates.append(enriched)

    second_pass: Dict[int, SourceClassification] = {}
    for candidate_batch in _classification_batches(review_candidates, batch_size):
        result = classify_candidate_sources(
            candidate_batch,
            league_context,
            pass_number=2,
            client=client,
            model=model,
        )
        for classification in result.classifications:
            second_pass[classification.source_id] = classification
            store_source_classification(db, classification, result)
        db.commit()

    effective = dict(first_pass)
    effective.update(second_pass)
    route_counts: Dict[str, int] = {}
    for classification in effective.values():
        route_counts[classification.route] = route_counts.get(classification.route, 0) + 1

    return {
        "stored_sources": len(candidates) + len(signal_only),
        "article_candidates": len(candidates),
        "signal_only_sources": len(signal_only),
        "first_pass_classifications": len(first_pass),
        "automated_reviews": len(review_candidates),
        "second_pass_classifications": len(second_pass),
        "effective_route_counts": route_counts,
        "prompt_version": CLASSIFIER_PROMPT_VERSION,
    }


ACTIVE_CLASSIFICATION_JOB_STATUSES = frozenset({
    "submitting",
    "submission_unknown",
    "validating",
    "in_progress",
    "finalizing",
    "cancelling",
})
TERMINAL_CLASSIFICATION_JOB_STATUSES = frozenset({
    "completed",
    "failed",
    "expired",
    "cancelled",
})
CLASSIFICATION_IMPORT_CHUNK_SIZE = 50


def _openai_value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _classification_record_as_input(
    record: CorrespondentSourceClassification,
) -> Dict[str, Any]:
    return {
        "pickem_impact": record.pickem_impact,
        "editorial_functions": json.loads(record.editorial_functions_json),
        "article_use": record.article_use,
        "confidence": record.confidence,
        "route": record.route,
        "reason_codes": json.loads(record.reason_codes_json),
        "reason": record.reason,
    }


def _active_classification_source_ids(
    db,
    week: Week,
    pass_number: int,
) -> set[int]:
    rows = db.query(CorrespondentClassificationJobItem.source_id).join(
        CorrespondentClassificationJob
    ).filter(
        CorrespondentClassificationJob.week_id == week.id,
        CorrespondentClassificationJob.prompt_version == CLASSIFIER_PROMPT_VERSION,
        CorrespondentClassificationJob.pass_number == pass_number,
        CorrespondentClassificationJob.status.in_(ACTIVE_CLASSIFICATION_JOB_STATUSES),
    ).all()
    return {row[0] for row in rows}


def classification_batch_candidates(
    db,
    week: Week,
    pass_number: int,
) -> Tuple[List[Dict[str, Any]], List[CorrespondentSource]]:
    """Select unclassified article candidates not reserved by an active Batch."""
    if pass_number not in {1, 2}:
        raise ValueError("pass_number must be 1 or 2")
    candidates, signal_only = classification_candidate_sources(db, week)
    active_source_ids = _active_classification_source_ids(db, week, pass_number)
    completed = {
        record.source_id: record
        for record in db.query(CorrespondentSourceClassification).filter(
            CorrespondentSourceClassification.source_id.in_(
                [candidate["source_id"] for candidate in candidates]
            ),
            CorrespondentSourceClassification.prompt_version
            == CLASSIFIER_PROMPT_VERSION,
            CorrespondentSourceClassification.pass_number == pass_number,
        )
    } if candidates else {}

    selected: List[Dict[str, Any]] = []
    for candidate in candidates:
        source_id = candidate["source_id"]
        if source_id in completed or source_id in active_source_ids:
            continue
        if pass_number == 1:
            selected.append(candidate)
            continue
        first_pass = db.query(CorrespondentSourceClassification).filter_by(
            source_id=source_id,
            prompt_version=CLASSIFIER_PROMPT_VERSION,
            pass_number=1,
        ).first()
        if first_pass is None or first_pass.route != "AUTOMATED_REVIEW":
            continue
        enriched = dict(candidate)
        enriched["initial_classification"] = _classification_record_as_input(first_pass)
        selected.append(enriched)
    return selected, signal_only


def _classification_custom_id(job_id: int, source_id: int, pass_number: int) -> str:
    return (
        f"source_{source_id}__{CLASSIFIER_PROMPT_VERSION}__"
        f"pass_{pass_number}__job_{job_id}"
    )


def _classification_reservation_key(source_id: int, pass_number: int) -> str:
    return f"{source_id}:{CLASSIFIER_PROMPT_VERSION}:{pass_number}"


def _update_classification_job_from_batch(
    job: CorrespondentClassificationJob,
    remote_batch: Any,
) -> None:
    job.openai_batch_id = _openai_value(remote_batch, "id", job.openai_batch_id)
    job.output_file_id = _openai_value(
        remote_batch,
        "output_file_id",
        job.output_file_id,
    )
    job.error_file_id = _openai_value(
        remote_batch,
        "error_file_id",
        job.error_file_id,
    )
    job.status = _openai_value(remote_batch, "status", job.status)
    counts = _openai_value(remote_batch, "request_counts")
    if counts is not None:
        job.total_count = int(_openai_value(counts, "total", job.total_count) or 0)
        job.completed_count = int(
            _openai_value(counts, "completed", job.completed_count) or 0
        )
        job.failed_count = int(_openai_value(counts, "failed", job.failed_count) or 0)
    job.last_synced_at = utcnow()
    if job.status in TERMINAL_CLASSIFICATION_JOB_STATUSES and job.completed_at is None:
        job.completed_at = utcnow()


def submit_week_classification_batch(
    db,
    week: Week,
    *,
    pass_number: int = 1,
    client: Any = None,
    model: Optional[str] = None,
) -> CorrespondentClassificationJob:
    """Reserve eligible sources and submit one asynchronous OpenAI Batch."""
    candidates, _signal_only = classification_batch_candidates(db, week, pass_number)
    if not candidates:
        raise ValueError(
            f"No pass-{pass_number} sources are eligible for classification; "
            "they are already completed or assigned to an active batch"
        )
    selected_model = classifier_model(model)
    league_context = build_weekly_recap_context(db, week)
    job = CorrespondentClassificationJob(
        season_id=week.season_id,
        week_id=week.id,
        prompt_version=CLASSIFIER_PROMPT_VERSION,
        pass_number=pass_number,
        model=selected_model,
        status="submitting",
        total_count=len(candidates),
        created_at=utcnow(),
    )
    db.add(job)
    db.flush()

    request_lines: List[str] = []
    for candidate in candidates:
        custom_id = _classification_custom_id(
            job.id,
            candidate["source_id"],
            pass_number,
        )
        db.add(CorrespondentClassificationJobItem(
            job_id=job.id,
            source_id=candidate["source_id"],
            custom_id=custom_id,
            active_reservation_key=_classification_reservation_key(
                candidate["source_id"],
                pass_number,
            ),
            status="pending",
        ))
        request_lines.append(json.dumps(
            build_classification_batch_request(
                custom_id,
                [candidate],
                league_context,
                pass_number=pass_number,
                model=selected_model,
            ),
            ensure_ascii=False,
            separators=(",", ":"),
        ))
    # Commit the reservation before network I/O so a duplicate click cannot submit
    # the same source while the OpenAI upload is in progress.
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError(
            "One or more sources were assigned to another active classification batch"
        ) from exc

    openai_client = classifier_client(client)
    input_bytes = ("\n".join(request_lines) + "\n").encode("utf-8")
    try:
        uploaded = openai_client.files.create(
            file=(f"correspondent-classification-job-{job.id}.jsonl", input_bytes),
            purpose="batch",
        )
        job.input_file_id = _openai_value(uploaded, "id")
        db.commit()
    except Exception as exc:
        job.status = "failed"
        job.error_message = f"OpenAI Batch input upload failed: {exc}"
        job.completed_at = utcnow()
        for item in job.items:
            item.status = "failed"
            item.error_message = job.error_message
            item.active_reservation_key = None
        db.commit()
        raise CorrespondentError(job.error_message) from exc

    try:
        remote_batch = openai_client.batches.create(
            input_file_id=job.input_file_id,
            endpoint="/v1/responses",
            completion_window="24h",
            metadata={
                "season_id": str(week.season_id),
                "week_id": str(week.id),
                "prompt_version": CLASSIFIER_PROMPT_VERSION,
                "pass_number": str(pass_number),
            },
        )
        _update_classification_job_from_batch(job, remote_batch)
        if job.status in TERMINAL_CLASSIFICATION_JOB_STATUSES:
            for item in job.items:
                item.active_reservation_key = None
        job.submitted_at = utcnow()
        db.commit()
    except Exception as exc:
        db.rollback()
        job = db.get(CorrespondentClassificationJob, job.id)
        job.status = "submission_unknown"
        job.error_message = (
            "OpenAI Batch submission outcome is unknown; the source reservation "
            f"was retained to prevent a duplicate Batch: {exc}"
        )
        job.last_synced_at = utcnow()
        db.commit()
        raise CorrespondentError(job.error_message) from exc
    return job


def ensure_week_classification_batch(
    db,
    week: Week,
    *,
    pass_number: int,
    client: Any = None,
    model: Optional[str] = None,
) -> Tuple[Optional[CorrespondentClassificationJob], bool]:
    """Idempotently return or create the Batch needed for one classifier pass."""
    if pass_number not in {1, 2}:
        raise ValueError("pass_number must be 1 or 2")
    candidates, _ = classification_batch_candidates(db, week, pass_number)
    if candidates:
        return submit_week_classification_batch(
            db,
            week,
            pass_number=pass_number,
            client=client,
            model=model,
        ), True
    latest = db.query(CorrespondentClassificationJob).filter_by(
        week_id=week.id,
        prompt_version=CLASSIFIER_PROMPT_VERSION,
        pass_number=pass_number,
    ).order_by(CorrespondentClassificationJob.id.desc()).first()
    return latest, False


def _download_openai_file_text(openai_client: Any, file_id: str) -> str:
    content = openai_client.files.content(file_id)
    text_value = getattr(content, "text", None)
    if isinstance(text_value, str):
        return text_value
    byte_value = getattr(content, "content", None)
    if isinstance(byte_value, bytes):
        return byte_value.decode("utf-8")
    read = getattr(content, "read", None)
    if callable(read):
        value = read()
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)
    if isinstance(content, bytes):
        return content.decode("utf-8")
    if isinstance(content, str):
        return content
    raise CorrespondentError(f"Unable to read OpenAI Batch file {file_id}")


def _batch_error_message(line: Mapping[str, Any]) -> str:
    error = line.get("error")
    if isinstance(error, Mapping):
        return str(error.get("message") or error.get("code") or "Batch request failed")
    response = line.get("response")
    if isinstance(response, Mapping):
        body = response.get("body")
        if isinstance(body, Mapping):
            nested_error = body.get("error")
            if isinstance(nested_error, Mapping):
                return str(
                    nested_error.get("message")
                    or nested_error.get("code")
                    or "Batch request failed"
                )
        status_code = response.get("status_code")
        if status_code:
            return f"Batch request returned HTTP {status_code}"
    return "Batch request failed"


def _import_classification_file(
    db,
    job: CorrespondentClassificationJob,
    openai_client: Any,
    file_id: str,
    *,
    error_file: bool = False,
) -> int:
    items_by_custom_id = {item.custom_id: item for item in job.items}
    imported = 0
    pending_changes = 0

    def record_change() -> None:
        nonlocal pending_changes
        pending_changes += 1
        if pending_changes >= CLASSIFICATION_IMPORT_CHUNK_SIZE:
            db.commit()
            pending_changes = 0

    for raw_line in _download_openai_file_text(openai_client, file_id).splitlines():
        if not raw_line.strip():
            continue
        try:
            line = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            job.error_message = f"OpenAI Batch file contains invalid JSONL: {exc}"
            record_change()
            continue
        custom_id = line.get("custom_id") if isinstance(line, Mapping) else None
        item = items_by_custom_id.get(custom_id)
        if item is None:
            job.error_message = f"OpenAI Batch returned unknown custom_id: {custom_id}"
            record_change()
            continue
        if item.status == "completed" and item.imported_at is not None:
            continue
        response = line.get("response") if isinstance(line, Mapping) else None
        status_code = (
            response.get("status_code") if isinstance(response, Mapping) else None
        )
        if error_file or line.get("error") is not None or status_code != 200:
            item.status = "failed"
            item.error_message = _batch_error_message(line)
            record_change()
            continue
        response_body = response.get("body")
        try:
            result = validate_classification_response(
                response_body,
                [item.source_id],
                pass_number=job.pass_number,
                model=job.model,
            )
            store_source_classification(
                db,
                result.classifications[0],
                result,
                prompt_version=job.prompt_version,
            )
        except (CorrespondentError, TypeError, ValueError) as exc:
            item.status = "failed"
            item.error_message = f"Classification import failed: {exc}"
            record_change()
            continue
        item.status = "completed"
        item.provider_response_id = result.provider_response_id
        item.error_message = None
        item.imported_at = utcnow()
        imported += 1
        record_change()
    db.commit()
    return imported


def sync_week_classification_jobs(
    db,
    week: Week,
    *,
    client: Any = None,
    create_second_pass: bool = True,
) -> Dict[str, Any]:
    """Refresh Batch state, idempotently import results, and start pass 2."""
    jobs = db.query(CorrespondentClassificationJob).filter_by(
        week_id=week.id,
    ).order_by(CorrespondentClassificationJob.id.asc()).all()
    if not jobs:
        raise ValueError("No classification batches exist for this week")
    openai_client = classifier_client(client)
    imported = 0
    for job in jobs:
        if not job.openai_batch_id:
            continue
        try:
            remote_batch = openai_client.batches.retrieve(job.openai_batch_id)
            _update_classification_job_from_batch(job, remote_batch)
            if job.output_file_id:
                imported += _import_classification_file(
                    db,
                    job,
                    openai_client,
                    job.output_file_id,
                )
            if job.error_file_id:
                _import_classification_file(
                    db,
                    job,
                    openai_client,
                    job.error_file_id,
                    error_file=True,
                )
            if job.status in TERMINAL_CLASSIFICATION_JOB_STATUSES:
                for item in job.items:
                    if item.status == "pending":
                        item.status = "failed"
                        item.error_message = (
                            f"Batch ended with status {job.status} without a result"
                        )
                    item.active_reservation_key = None
            db.commit()
        except Exception as exc:
            db.rollback()
            persisted_job = db.get(CorrespondentClassificationJob, job.id)
            persisted_job.last_synced_at = utcnow()
            persisted_job.error_message = f"Batch status sync failed: {exc}"
            db.commit()

    second_pass_job = None
    completed_pass_one_jobs = [
        job for job in jobs
        if job.prompt_version == CLASSIFIER_PROMPT_VERSION
        and job.pass_number == 1
        and job.status == "completed"
    ]
    if create_second_pass and completed_pass_one_jobs:
        pass_two_candidates, _ = classification_batch_candidates(db, week, 2)
        if pass_two_candidates:
            second_pass_job, _created = ensure_week_classification_batch(
                db,
                week,
                pass_number=2,
                client=openai_client,
                model=completed_pass_one_jobs[-1].model,
            )
    return {
        "jobs_checked": len(jobs),
        "classifications_imported": imported,
        "second_pass_job_id": None if second_pass_job is None else second_pass_job.id,
        "prompt_version": CLASSIFIER_PROMPT_VERSION,
    }


def create_manual_correspondent_source(
    db,
    week: Week,
    *,
    source_type: str,
    canonical_url: str,
    author_name: str,
    body_text: str,
    submission_note: str = "",
) -> CorrespondentSource:
    """Validate and store one source without allowing it to touch game state."""
    source_type = source_type.strip().lower()
    canonical_url = canonical_url.strip()
    author_name = author_name.strip()
    body_text = body_text.strip()
    submission_note = submission_note.strip()
    if source_type not in CORRESPONDENT_SOURCE_TYPES:
        raise ValueError("Unknown Correspondent source type")
    if not body_text:
        raise ValueError("Source text is required")
    if len(body_text) > 5000:
        raise ValueError("Source text must be 5,000 characters or fewer")
    if len(author_name) > 160:
        raise ValueError("Source author must be 160 characters or fewer")
    if len(canonical_url) > 1000:
        raise ValueError("Source URL must be 1,000 characters or fewer")
    if canonical_url and not canonical_url.lower().startswith(("https://", "http://")):
        raise ValueError("Source URL must begin with http:// or https://")
    if len(submission_note) > 1000:
        raise ValueError("Submission note must be 1,000 characters or fewer")

    normalized = json.dumps(
        {
            "url": canonical_url,
            "author": author_name,
            "text": body_text,
            "note": submission_note,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    existing = db.query(CorrespondentSource).filter_by(
        week_id=week.id,
        provider="manual",
        content_hash=content_hash,
    ).first()
    if existing:
        raise ValueError("That source has already been added")

    source = CorrespondentSource(
        week_id=week.id,
        provider="manual",
        source_type=source_type,
        external_id=content_hash,
        canonical_url=canonical_url or None,
        author_name=author_name or None,
        body_text=body_text,
        submission_note=submission_note or None,
        metadata_json="{}",
        content_hash=content_hash,
        status="accepted",
        created_at=utcnow(),
    )
    db.add(source)
    db.commit()
    return source


def _required_ingest_text(payload: Mapping[str, Any], name: str, max_length: int) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    value = value.strip()
    if len(value) > max_length:
        raise ValueError(f"{name} must be {max_length:,} characters or fewer")
    return value


def _optional_ingest_text(payload: Mapping[str, Any], name: str, max_length: int) -> str:
    value = payload.get(name)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string or null")
    value = value.strip()
    if len(value) > max_length:
        raise ValueError(f"{name} must be {max_length:,} characters or fewer")
    return value


def _parse_correspondent_published_at(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("published_at must be an ISO-8601 string or null")
    normalized = value.strip()
    if normalized.endswith(("Z", "z")):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        published_at = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("published_at must be a valid ISO-8601 timestamp") from exc
    if published_at.tzinfo is None:
        raise ValueError("published_at must include a timezone")
    return published_at.astimezone(timezone.utc).replace(tzinfo=None)


def ingest_correspondent_source(
    db,
    payload: Mapping[str, Any],
    *,
    commit: bool = True,
) -> Tuple[CorrespondentSource, bool]:
    """Validate and idempotently store one normalized external source."""
    if not isinstance(payload, Mapping):
        raise ValueError("Request JSON must be an object")

    unknown_fields = set(payload) - CORRESPONDENT_INGEST_ALLOWED_FIELDS
    if unknown_fields:
        raise ValueError(
            "Unknown field(s): " + ", ".join(sorted(str(field) for field in unknown_fields))
        )
    missing_fields = CORRESPONDENT_INGEST_REQUIRED_FIELDS - set(payload)
    if missing_fields:
        raise ValueError(
            "Missing required field(s): " + ", ".join(sorted(missing_fields))
        )

    season_code = _required_ingest_text(payload, "season_code", 80)
    provider = _required_ingest_text(payload, "provider", 80).lower()
    source_type = _required_ingest_text(payload, "source_type", 40).lower()
    external_id = _required_ingest_text(payload, "external_id", 255)
    body_text = _required_ingest_text(payload, "body_text", 5000)
    canonical_url = _optional_ingest_text(payload, "canonical_url", 1000)
    author_name = _optional_ingest_text(payload, "author_name", 160)
    submitted_by_name = _optional_ingest_text(
        payload, "submitted_by_player", 160
    )
    submission_note = _optional_ingest_text(payload, "submission_note", 1000)

    week_number = payload.get("week_number")
    if isinstance(week_number, bool) or not isinstance(week_number, int):
        raise ValueError("week_number must be an integer")
    if not 1 <= week_number <= 38:
        raise ValueError("week_number must be between 1 and 38")
    if provider == "manual" or not CORRESPONDENT_PROVIDER_PATTERN.fullmatch(provider):
        raise ValueError(
            "provider must use lowercase letters, numbers, dots, underscores, or hyphens"
        )
    if source_type not in AUTOMATED_CORRESPONDENT_SOURCE_TYPES:
        raise ValueError("Unknown automated Correspondent source type")
    if canonical_url and not canonical_url.lower().startswith(("https://", "http://")):
        raise ValueError("canonical_url must begin with http:// or https://")

    published_at = _parse_correspondent_published_at(payload.get("published_at"))
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be a JSON object")
    if any(not isinstance(key, str) for key in metadata):
        raise ValueError("metadata keys must be strings")
    try:
        metadata_json = json.dumps(
            metadata,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("metadata must contain valid JSON values") from exc
    if len(metadata_json.encode("utf-8")) > 10_000:
        raise ValueError("metadata must be 10,000 bytes or fewer")

    season = active_season(db)
    if season is None or season.is_archived or season.code != season_code:
        raise CorrespondentIngestConflict(
            "Sources may only be added to the writable active season"
        )
    week = season_week(db, season, week_number)
    if week is None:
        raise CorrespondentIngestNotFound("Week not found")

    submitted_by = None
    if submitted_by_name:
        submitted_by = next(
            (
                player
                for player in season_players(db, season)
                if player.name.casefold() == submitted_by_name.casefold()
            ),
            None,
        )
        if submitted_by is None:
            raise ValueError("submitted_by_player is not a player in the active season")

    normalized = json.dumps(
        {
            "external_id": external_id,
            "source_type": source_type,
            "url": canonical_url,
            "author": author_name,
            "text": body_text,
            "published_at": None if published_at is None else published_at.isoformat(),
            "submitted_by_player": None if submitted_by is None else submitted_by.name,
            "note": submission_note,
            "metadata": metadata,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    existing = db.query(CorrespondentSource).filter_by(
        week_id=week.id,
        provider=provider,
        external_id=external_id,
    ).first()
    if existing:
        if existing.content_hash == content_hash:
            return existing, False
        raise CorrespondentIngestConflict(
            "provider and external_id already identify different source data"
        )

    source = CorrespondentSource(
        week_id=week.id,
        provider=provider,
        source_type=source_type,
        external_id=external_id,
        canonical_url=canonical_url or None,
        author_name=author_name or None,
        body_text=body_text,
        published_at=published_at,
        submitted_by_player_id=None if submitted_by is None else submitted_by.id,
        submission_note=submission_note or None,
        metadata_json=metadata_json,
        content_hash=content_hash,
        status="accepted",
        created_at=utcnow(),
    )
    db.add(source)
    if commit:
        db.commit()
    else:
        db.flush()
    return source, True


def effective_source_classifications(
    db,
    week: Week,
) -> Dict[int, CorrespondentSourceClassification]:
    """Return the latest completed classifier pass for each source in a week."""
    records = db.query(CorrespondentSourceClassification).join(
        CorrespondentSource,
        CorrespondentSource.id == CorrespondentSourceClassification.source_id,
    ).filter(
        CorrespondentSource.week_id == week.id,
        CorrespondentSourceClassification.prompt_version
        == CLASSIFIER_PROMPT_VERSION,
    ).order_by(
        CorrespondentSourceClassification.source_id.asc(),
        CorrespondentSourceClassification.pass_number.asc(),
    ).all()
    effective: Dict[int, CorrespondentSourceClassification] = {}
    for record in records:
        effective[record.source_id] = record
    return effective


def correspondent_classification_readiness(
    db,
    week: Week,
) -> Dict[str, Any]:
    """Derive whether every accepted article candidate has its final V2 pass."""
    candidates, signal_only = classification_candidate_sources(db, week)
    candidate_ids = [candidate["source_id"] for candidate in candidates]
    records = (
        db.query(CorrespondentSourceClassification).filter(
            CorrespondentSourceClassification.source_id.in_(candidate_ids),
            CorrespondentSourceClassification.prompt_version
            == CLASSIFIER_PROMPT_VERSION,
        ).all()
        if candidate_ids
        else []
    )
    by_source_and_pass = {
        (record.source_id, record.pass_number): record for record in records
    }
    missing_pass_one: List[int] = []
    awaiting_pass_two: List[int] = []
    final_source_ids: List[int] = []
    for source_id in candidate_ids:
        first_pass = by_source_and_pass.get((source_id, 1))
        if first_pass is None:
            missing_pass_one.append(source_id)
            continue
        if first_pass.route == "AUTOMATED_REVIEW":
            if by_source_and_pass.get((source_id, 2)) is None:
                awaiting_pass_two.append(source_id)
                continue
        final_source_ids.append(source_id)
    return {
        "ready": bool(candidate_ids)
        and not missing_pass_one
        and not awaiting_pass_two,
        "article_candidates": len(candidate_ids),
        "signal_only_sources": len(signal_only),
        "final_classifications": len(final_source_ids),
        "missing_pass_one_source_ids": missing_pass_one,
        "awaiting_pass_two_source_ids": awaiting_pass_two,
        "prompt_version": CLASSIFIER_PROMPT_VERSION,
    }


def _utc_iso(value: Optional[datetime]) -> Optional[str]:
    return None if value is None else f"{value.isoformat(timespec='seconds')}Z"


def correspondent_recap_automation_key(week: Week) -> str:
    return (
        f"week:{week.id}:classifier:{CLASSIFIER_PROMPT_VERSION}:"
        f"recap:{V2_PROMPT_VERSION}"
    )


def _normalized_x_next_token(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("next_token must be a string or null")
    value = value.strip()
    return value or None


def _x_collection_json(
    state: Optional[CorrespondentXCollectionState],
) -> Optional[Dict[str, Any]]:
    if state is None:
        return None
    return {
        "id": state.id,
        "status": state.status,
        "window_start": _utc_iso(state.window_start),
        "window_end": _utc_iso(state.window_end),
        "has_next_token": bool(state.next_token),
        "page_count": state.page_count,
        "retrieved_count": state.retrieved_count,
        "persisted_count": state.persisted_count,
        "cap": CORRESPONDENT_X_WEEKLY_CAP,
        "cap_reached": state.status == "capped",
        "cap_reached_at": _utc_iso(state.cap_reached_at),
        "completed_at": _utc_iso(state.completed_at),
        "last_error": state.last_error,
    }


def _week_x_source_count(db, week: Week) -> int:
    return db.query(func.count(CorrespondentSource.id)).filter_by(
        week_id=week.id,
        provider="x",
        status="accepted",
    ).scalar() or 0


def claim_x_collection_page(
    db,
    week: Week,
    *,
    window_start: datetime,
    window_end: datetime,
    now: Optional[datetime] = None,
) -> Tuple[CorrespondentXCollectionState, Optional[Dict[str, Any]]]:
    """Reserve exactly one paid X page request for a bounded lease."""
    now = now or utcnow()
    if window_end <= window_start:
        raise ValueError("X collection window_end must be after window_start")
    state = db.query(CorrespondentXCollectionState).filter_by(
        week_id=week.id,
    ).one_or_none()
    if state is None:
        existing_count = _week_x_source_count(db, week)
        status = "capped" if existing_count >= CORRESPONDENT_X_WEEKLY_CAP else "ready"
        state = CorrespondentXCollectionState(
            week_id=week.id,
            status=status,
            window_start=window_start,
            window_end=window_end,
            retrieved_count=min(existing_count, CORRESPONDENT_X_WEEKLY_CAP),
            persisted_count=min(existing_count, CORRESPONDENT_X_WEEKLY_CAP),
            cap_reached_at=now if status == "capped" else None,
            completed_at=now if status == "capped" else None,
            created_at=now,
            updated_at=now,
        )
        db.add(state)
        db.commit()
    elif state.window_start != window_start or state.window_end != window_end:
        raise ValueError("X collection window cannot change after collection starts")

    if state.status in {"completed", "capped"}:
        return state, None
    if state.status == "claimed" and state.lease_token:
        if state.lease_expires_at is not None and state.lease_expires_at <= now:
            state.status = "error"
            state.last_error = (
                "X page claim expired without a checkpoint; automatic re-fetch is "
                "blocked to avoid a duplicate paid request"
            )
            state.updated_at = now
            db.commit()
        return state, None
    if state.status == "error":
        return state, None

    remaining = CORRESPONDENT_X_WEEKLY_CAP - state.retrieved_count
    if remaining <= 0:
        state.status = "capped"
        state.cap_reached_at = state.cap_reached_at or now
        state.completed_at = state.completed_at or now
        state.lease_token = None
        state.lease_expires_at = None
        state.last_error = None
        state.updated_at = now
        db.commit()
        return state, None

    state.status = "claimed"
    state.lease_token = secrets.token_urlsafe(24)
    state.lease_expires_at = now + CORRESPONDENT_X_LEASE_DURATION
    state.last_error = None
    state.updated_at = now
    db.commit()
    return state, {
        "claim_token": state.lease_token,
        "pagination_token": state.next_token,
        "max_results": min(CORRESPONDENT_X_PAGE_SIZE, remaining),
        "window_start": _utc_iso(state.window_start),
        "window_end": _utc_iso(state.window_end),
    }


def checkpoint_x_collection_page(
    db,
    week: Week,
    *,
    claim_token: str,
    next_token: Any,
    sources: List[Mapping[str, Any]],
    now: Optional[datetime] = None,
) -> Tuple[CorrespondentXCollectionState, CorrespondentXPageReceipt, bool]:
    """Atomically persist one X page and its continuation checkpoint."""
    now = now or utcnow()
    claim_token = str(claim_token or "").strip()
    if not claim_token:
        raise ValueError("claim_token is required")
    existing_receipt = db.query(CorrespondentXPageReceipt).filter_by(
        claim_token=claim_token,
    ).one_or_none()
    if existing_receipt is not None:
        state = db.get(CorrespondentXCollectionState, existing_receipt.collection_id)
        if state is None or state.week_id != week.id:
            raise ValueError("claim_token belongs to a different week")
        return state, existing_receipt, False

    state = db.query(CorrespondentXCollectionState).filter_by(
        week_id=week.id,
    ).one_or_none()
    if (
        state is None
        or state.status not in {"claimed", "error"}
        or state.lease_token != claim_token
    ):
        raise ValueError("X page claim is missing, expired, or already superseded")
    if not isinstance(sources, list):
        raise ValueError("sources must be an array")
    if len(sources) > CORRESPONDENT_X_PAGE_SIZE:
        raise ValueError(
            f"sources must contain no more than {CORRESPONDENT_X_PAGE_SIZE} items"
        )
    normalized_next_token = _normalized_x_next_token(next_token)
    request_token = state.next_token
    season_code = week.season.code
    external_ids = set()
    for source in sources:
        if not isinstance(source, Mapping):
            raise ValueError("Every source must be a JSON object")
        if str(source.get("provider") or "").strip().lower() != "x":
            raise ValueError("X collection checkpoints may contain only provider=x")
        if source.get("season_code") != season_code or source.get("week_number") != week.number:
            raise ValueError("X source target does not match the claimed season/week")
        external_id = str(source.get("external_id") or "").strip()
        if not external_id:
            raise ValueError("Every X source must have an external_id")
        external_ids.add(external_id)
    remaining = CORRESPONDENT_X_WEEKLY_CAP - state.retrieved_count
    if len(external_ids) > remaining:
        raise ValueError("X page would exceed the 1,000-post weekly ceiling")

    created_count = 0
    try:
        for source in sources:
            _, created = ingest_correspondent_source(db, source, commit=False)
            created_count += int(created)
        state.page_count += 1
        state.retrieved_count += created_count
        state.persisted_count += created_count
        state.next_token = normalized_next_token
        state.lease_token = None
        state.lease_expires_at = None
        state.last_error = None
        state.updated_at = now
        if state.retrieved_count >= CORRESPONDENT_X_WEEKLY_CAP:
            state.status = "capped"
            state.cap_reached_at = state.cap_reached_at or now
            state.completed_at = state.completed_at or now
        elif normalized_next_token is None:
            state.status = "completed"
            state.completed_at = state.completed_at or now
        else:
            state.status = "ready"
        receipt = CorrespondentXPageReceipt(
            collection_id=state.id,
            claim_token=claim_token,
            request_token=request_token,
            next_token=normalized_next_token,
            received_count=len(sources),
            created_count=created_count,
            created_at=now,
        )
        db.add(receipt)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return state, receipt, True


def correspondent_automation_status(
    db,
    week: Week,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Derive the current automation phase from authoritative persisted facts."""
    now = now or utcnow()
    completed_results, fixture_count = count_results_for_week(db, week)
    eligible_at = (
        None
        if week.finalized_at is None
        else week.finalized_at + CORRESPONDENT_FINALIZATION_BUFFER
    )
    readiness = correspondent_classification_readiness(db, week)
    jobs = db.query(CorrespondentClassificationJob).filter_by(
        week_id=week.id,
        prompt_version=CLASSIFIER_PROMPT_VERSION,
    ).order_by(CorrespondentClassificationJob.id.asc()).all()
    active_jobs = [job for job in jobs if job.status in ACTIVE_CLASSIFICATION_JOB_STATUSES]
    active_passes = {job.pass_number for job in active_jobs}
    unknown_submission = next(
        (job for job in reversed(jobs) if job.status == "submission_unknown"),
        None,
    )
    recap = db.query(WeeklyRecap).filter_by(
        week_id=week.id,
        correspondent_version="v2",
        prompt_version=V2_PROMPT_VERSION,
        automation_key=correspondent_recap_automation_key(week),
    ).order_by(WeeklyRecap.id.desc()).first()
    x_collection = db.query(CorrespondentXCollectionState).filter_by(
        week_id=week.id,
    ).one_or_none()

    if week.status != "finalized" or fixture_count != 10 or completed_results != 10:
        phase = "WAITING_FOR_FINALIZATION"
        allowed_actions: List[str] = []
    elif eligible_at is None:
        phase = "WAITING_FOR_FINALIZATION_TIMESTAMP"
        allowed_actions = []
    elif now < eligible_at:
        phase = "WAITING_FOR_BUFFER"
        allowed_actions = []
    elif x_collection is None:
        phase = "READY_FOR_X_COLLECTION"
        allowed_actions = ["claim_x_page"]
    elif x_collection.status == "error":
        phase = "ERROR_RETRY"
        allowed_actions = []
    elif x_collection.status in {"ready", "claimed"}:
        phase = "COLLECTING_X"
        allowed_actions = ["claim_x_page"] if x_collection.status != "claimed" else []
    elif recap is not None and recap.status == "generating":
        phase = "GENERATING_RECAP"
        allowed_actions = []
    elif recap is not None and recap.status == "ready":
        phase = "RECAP_GENERATED"
        allowed_actions = []
    elif unknown_submission is not None:
        phase = "ERROR_RETRY"
        allowed_actions = []
    elif 1 in active_passes:
        phase = "CLASSIFYING_PASS_1"
        allowed_actions = ["reconcile_classification"]
    elif readiness["missing_pass_one_source_ids"]:
        phase = "READY_FOR_PASS_1"
        allowed_actions = ["submit_pass_1"]
    elif 2 in active_passes:
        phase = "CLASSIFYING_PASS_2"
        allowed_actions = ["reconcile_classification"]
    elif readiness["awaiting_pass_two_source_ids"]:
        phase = "PASS_1_COMPLETE_PASS_2_REQUIRED"
        allowed_actions = ["reconcile_classification"]
    elif readiness["ready"]:
        phase = "READY_TO_GENERATE"
        allowed_actions = ["generate_recap"]
    else:
        phase = "WAITING_FOR_SOURCES"
        allowed_actions = []

    return {
        "phase": phase,
        "allowed_actions": allowed_actions,
        "season_code": week.season.code,
        "week_number": week.number,
        "week_status": week.status,
        "fixture_count": fixture_count,
        "completed_results": completed_results,
        "finalized_at": _utc_iso(week.finalized_at),
        "eligible_at": _utc_iso(eligible_at),
        "x_collection": _x_collection_json(x_collection),
        "classification": readiness,
        "jobs": [
            {
                "id": job.id,
                "pass_number": job.pass_number,
                "status": job.status,
                "total": job.total_count,
                "completed": job.completed_count,
                "failed": job.failed_count,
            }
            for job in jobs
        ],
        "recap": None if recap is None else {
            "id": recap.id,
            "status": recap.status,
            "revision": recap.revision,
        },
    }


def _classification_for_writer(
    record: CorrespondentSourceClassification,
) -> Dict[str, Any]:
    try:
        editorial_functions = json.loads(record.editorial_functions_json)
    except (TypeError, json.JSONDecodeError):
        editorial_functions = []
    try:
        reason_codes = json.loads(record.reason_codes_json)
    except (TypeError, json.JSONDecodeError):
        reason_codes = []
    return {
        "prompt_version": record.prompt_version,
        "pass_number": record.pass_number,
        "pickem_impact": record.pickem_impact,
        "editorial_functions": editorial_functions,
        "article_use": record.article_use,
        "confidence": record.confidence,
        "route": record.route,
        "reason_codes": reason_codes,
        "reason": record.reason,
    }


def writer_candidate_sources(
    db,
    week: Week,
) -> Tuple[List[CorrespondentSource], Dict[int, Dict[str, Any]]]:
    """Select only approved, classified sources for the V2 writer."""
    structural_candidates = [
        source
        for source in accepted_correspondent_sources(db, week)
        if correspondent_source_is_article_candidate(source)
    ]
    if not structural_candidates:
        raise ValueError("Correspondent V2 requires at least one accepted source")

    readiness = correspondent_classification_readiness(db, week)
    if not readiness["ready"]:
        if readiness["missing_pass_one_source_ids"]:
            raise ValueError(
                "Every eligible Correspondent V2 source must be classified; "
                "Pass 1 is required"
            )
        raise ValueError(
            "Correspondent V2 classification is incomplete; Pass 2 is required "
            "for every AUTOMATED_REVIEW source"
        )

    effective = effective_source_classifications(db, week)

    selected: List[CorrespondentSource] = []
    classification_context: Dict[int, Dict[str, Any]] = {}
    for source in structural_candidates:
        record = effective[source.id]
        if record.route not in {"ADVANCE", "ADVANCE_LOW_CONFIDENCE"}:
            continue
        if record.article_use == "NO_USE":
            continue
        selected.append(source)
        classification_context[source.id] = _classification_for_writer(record)
    if not selected:
        raise ValueError("No classified Correspondent sources advanced to the V2 writer")
    return selected, classification_context


def build_weekly_recap_context_v2(db, week: Week) -> Dict[str, Any]:
    """Add approved external candidates around the unchanged V1 fact packet."""
    league_context = build_weekly_recap_context(db, week)
    sources, classifications = writer_candidate_sources(db, week)
    return build_v2_context(
        league_context,
        sources,
        classifications=classifications,
    )


def selected_weekly_recap(db, week: Week) -> Optional[WeeklyRecap]:
    selection = db.query(WeeklyRecapSelection).filter_by(week_id=week.id).first()
    return None if selection is None else db.get(WeeklyRecap, selection.recap_id)


def _select_recap_if_none(db, week: Week, recap: WeeklyRecap) -> None:
    if db.query(WeeklyRecapSelection).filter_by(week_id=week.id).first() is None:
        db.add(WeeklyRecapSelection(
            week_id=week.id,
            recap_id=recap.id,
            selected_at=utcnow(),
        ))


def select_weekly_recap(db, week: Week, recap: WeeklyRecap) -> WeeklyRecapSelection:
    if recap.week_id != week.id:
        raise ValueError("The recap does not belong to that week")
    if recap.status != "ready":
        raise ValueError("Only a completed recap can be selected")
    selection = db.query(WeeklyRecapSelection).filter_by(week_id=week.id).first()
    if selection is None:
        selection = WeeklyRecapSelection(week_id=week.id)
    selection.recap_id = recap.id
    selection.selected_at = utcnow()
    db.add(selection)
    db.commit()
    return selection


def generate_and_store_weekly_recap(db, week: Week) -> WeeklyRecap:
    """Persist the fact snapshot, call the writer, and retain success or failure."""
    context = build_weekly_recap_context(db, week)
    context_json = json.dumps(context, ensure_ascii=False, sort_keys=True)
    context_hash = hashlib.sha256(context_json.encode("utf-8")).hexdigest()
    latest_revision = db.query(func.max(WeeklyRecap.revision)).filter_by(
        week_id=week.id
    ).scalar() or 0
    model = (os.environ.get("OPENAI_MODEL") or DEFAULT_CORRESPONDENT_MODEL).strip()
    recap = WeeklyRecap(
        week_id=week.id,
        revision=latest_revision + 1,
        status="generating",
        context_json=context_json,
        context_hash=context_hash,
        prompt_version=CORRESPONDENT_PROMPT_VERSION,
        model=model,
        correspondent_version="v1",
        source_count=0,
        created_at=utcnow(),
    )
    db.add(recap)
    db.commit()

    try:
        generated = generate_weekly_recap(context, model=model)
        recap.status = "ready"
        recap.title = generated.title
        recap.body_markdown = generated.body_markdown
        recap.model = generated.model
        recap.provider_response_id = generated.provider_response_id
        recap.completed_at = utcnow()
        db.flush()
        _select_recap_if_none(db, week, recap)
    except CorrespondentError as exc:
        recap.status = "failed"
        recap.error_message = str(exc)[:1000]
        recap.completed_at = utcnow()
    db.add(recap)
    db.commit()
    return recap


def generate_and_store_weekly_recap_v2(
    db,
    week: Week,
    *,
    automation_key: Optional[str] = None,
) -> WeeklyRecap:
    """Generate V2 beside V1; a V2 attempt never replaces the selected recap."""
    if not correspondent_v2_enabled():
        raise ValueError("Correspondent V2 is not enabled")
    if automation_key:
        existing = db.query(WeeklyRecap).filter_by(
            automation_key=automation_key,
        ).first()
        if existing is not None:
            return existing
    context = build_weekly_recap_context_v2(db, week)
    context_json = json.dumps(context, ensure_ascii=False, sort_keys=True)
    context_hash = hashlib.sha256(context_json.encode("utf-8")).hexdigest()
    external_json = json.dumps(
        context["external_context"], ensure_ascii=False, sort_keys=True
    )
    external_context_hash = hashlib.sha256(external_json.encode("utf-8")).hexdigest()
    latest_revision = db.query(func.max(WeeklyRecap.revision)).filter_by(
        week_id=week.id
    ).scalar() or 0
    model = (os.environ.get("OPENAI_MODEL") or DEFAULT_CORRESPONDENT_MODEL).strip()
    recap = WeeklyRecap(
        week_id=week.id,
        revision=latest_revision + 1,
        status="generating",
        context_json=context_json,
        context_hash=context_hash,
        prompt_version=V2_PROMPT_VERSION,
        model=model,
        correspondent_version="v2",
        source_count=context["external_context"]["candidate_source_count"],
        external_context_hash=external_context_hash,
        automation_key=automation_key,
        created_at=utcnow(),
    )
    db.add(recap)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if not automation_key:
            raise
        existing = db.query(WeeklyRecap).filter_by(
            automation_key=automation_key,
        ).one()
        return existing

    try:
        generated = generate_weekly_recap_v2(context, model=model)
        recap.status = "ready"
        recap.title = generated.title
        recap.body_markdown = generated.body_markdown
        recap.model = generated.model
        recap.provider_response_id = generated.provider_response_id
        recap.completed_at = utcnow()
        for source_id in generated.used_source_ids:
            db.add(RecapSourceUsage(recap_id=recap.id, source_id=source_id))
        _select_recap_if_none(db, week, recap)
    except CorrespondentError as exc:
        recap.status = "failed"
        recap.error_message = str(exc)[:1000]
        recap.completed_at = utcnow()
    db.add(recap)
    db.commit()
    return recap

def count_results_for_week(db, wk: Week) -> Tuple[int,int]:
    total = db.query(Fixture).filter_by(week_id=wk.id).count()
    done = db.query(Result).join(Fixture).filter(Fixture.week_id==wk.id).count()
    return done, total

def update_week_status(db, wk: Week) -> None:
    _set_week_status_without_commit(db, wk)
    db.commit()

def current_drafting_week(db, season: Season) -> Optional[Week]:
    base = db.query(Week).filter_by(season_id=season.id)
    wk = base.filter_by(status="drafting").order_by(Week.number.asc()).first()
    if wk: return wk
    wk = base.filter_by(status="provisional").order_by(Week.number.asc()).first()
    if wk: return wk
    return base.order_by(Week.number.asc()).first()

# -------------------- Tab routes (HTMX content) --------------------
@app.get("/tab/current")
def tab_current():
    db = SessionLocal()
    you = current_player(db)
    season = requested_season(db)
    if season is None:
        return "<div class='card'>No seasons initialized yet.</div>"
    # Optionally force a specific week via query param (?force_week=5)
    force = request.args.get("force_week", type=int)
    if force:
        wk = season_week(db, season, force)
        if wk is None:
            abort(404, "Week not found")
    else:
        wk = current_drafting_week(db, season)
    if wk is None:
        return f"<div class='card'>No weeks initialized for {season.name} yet.</div>"
    update_week_status(db, wk)
    return render_template_string(
        CURRENT_PARTIAL,
        current_week=wk,
        season=season,
        leader_stats=season_leader_stats(db, season),
        you=you,
    )

@app.get("/tab/open")
def tab_open():
    db = SessionLocal()
    you = current_player(db)
    season = requested_season(db)
    if season is None:
        return "<div class='card'>No seasons initialized yet.</div>"
    rows = []
    for wk in db.query(Week).filter_by(season_id=season.id).order_by(Week.number.asc()).all():
        if wk.status == "finalized":
            continue
        done, total = count_results_for_week(db, wk)
        rows.append({"week": wk.number, "status": wk.status, "done": done, "total": total})
    return render_template_string(OPEN_PARTIAL, open_rows=rows, season=season, you=you)

@app.get("/admin")
def admin():
    db = SessionLocal()
    season = active_season(db)
    weeks = [] if season is None else db.query(Week).filter_by(
        season_id=season.id
    ).order_by(Week.number.asc()).all()
    wk = None
    fixtures = []
    res_map = {}
    recaps = []
    correspondent_sources = []
    correspondent_classifications = {}
    correspondent_classification_jobs = []
    correspondent_signal_only_source_ids = set()
    selected_recap = None
    latest_recap = None
    displayed_recap = None
    latest_v2_recap = None
    recap_used_source_counts = {}
    latest_v2_used_source_ids = set()
    if weeks:
        sel = request.args.get("week", type=int)
        wk = season_week(db, season, sel) if sel else current_drafting_week(db, season)
        wk = wk or weeks[0]
        fixtures = db.query(Fixture).filter_by(week_id=wk.id).order_by(
            Fixture.match_number.asc()
        ).all()
        res_map = {
            result.fixture_id: result.outcome
            for result in db.query(Result).join(Fixture).filter(Fixture.week_id == wk.id)
        }
        recaps = db.query(WeeklyRecap).filter_by(week_id=wk.id).order_by(
            WeeklyRecap.revision.desc()
        ).all()
        latest_recap = recaps[0] if recaps else None
        requested_recap_id = request.args.get("recap_id", type=int)
        if request.args.get("recap_id") is not None and requested_recap_id is None:
            abort(404, "Recap not found")
        if requested_recap_id is None:
            displayed_recap = latest_recap
        else:
            displayed_recap = next(
                (recap for recap in recaps if recap.id == requested_recap_id),
                None,
            )
            if displayed_recap is None:
                abort(404, "Recap not found")
        latest_v2_recap = next(
            (
                recap for recap in recaps
                if recap.correspondent_version == "v2" and recap.status == "ready"
            ),
            None,
        )
        recap_ids = [recap.id for recap in recaps]
        recap_used_source_ids = {recap_id: set() for recap_id in recap_ids}
        if recap_ids:
            for usage in db.query(RecapSourceUsage).filter(
                RecapSourceUsage.recap_id.in_(recap_ids)
            ):
                recap_used_source_ids[usage.recap_id].add(usage.source_id)
        recap_used_source_counts = {
            recap_id: len(source_ids)
            for recap_id, source_ids in recap_used_source_ids.items()
        }
        if latest_v2_recap is not None:
            latest_v2_used_source_ids = recap_used_source_ids[latest_v2_recap.id]
        correspondent_sources = db.query(CorrespondentSource).filter_by(
            week_id=wk.id
        ).order_by(
            CorrespondentSource.created_at.desc(),
            CorrespondentSource.id.desc(),
        ).all()
        correspondent_classifications = effective_source_classifications(db, wk)
        correspondent_classification_jobs = db.query(
            CorrespondentClassificationJob
        ).filter_by(
            week_id=wk.id,
        ).order_by(
            CorrespondentClassificationJob.id.desc()
        ).all()
        correspondent_signal_only_source_ids = {
            source.id
            for source in correspondent_sources
            if source.status == "accepted"
            and not correspondent_source_is_article_candidate(source)
        }
        selected_recap = selected_weekly_recap(db, wk)

    year_two = db.query(Season).filter_by(code="year-2").first()
    can_import_api = year_two is None or db.query(Week).filter_by(
        season_id=year_two.id
    ).count() == 0
    defaults_from = season_players(db, season) if season is not None else db.query(
        Player
    ).order_by(Player.name.asc()).all()
    api_state = None if season is None else db.query(ApiSyncState).filter_by(
        season_id=season.id
    ).first()
    return render_template_string(
        ADMIN_HTML,
        is_admin=is_admin_session(),
        season=season,
        weeks=weeks,
        week=wk,
        fixtures=fixtures,
        results=res_map,
        api_configured=bool(os.environ.get("FOOTBALL_DATA_API_KEY", "").strip()),
        api_state=api_state,
        api_last_success=format_utc_timestamp(
            None if api_state is None else api_state.last_success_at
        ),
        openai_configured=bool(os.environ.get("OPENAI_API_KEY", "").strip()),
        correspondent_model=(
            os.environ.get("OPENAI_MODEL") or DEFAULT_CORRESPONDENT_MODEL
        ).strip(),
        recaps=recaps,
        latest_recap=latest_recap,
        displayed_recap=displayed_recap,
        selected_recap=selected_recap,
        latest_v2_recap=latest_v2_recap,
        recap_used_source_counts=recap_used_source_counts,
        latest_v2_used_source_ids=latest_v2_used_source_ids,
        correspondent_sources=correspondent_sources,
        correspondent_classifications=correspondent_classifications,
        correspondent_classification_jobs=correspondent_classification_jobs,
        correspondent_signal_only_source_ids=correspondent_signal_only_source_ids,
        correspondent_v2_enabled=correspondent_v2_enabled(),
        correspondent_default_version=correspondent_default_version(),
        can_import_api=can_import_api,
        default_players=",".join(player.name for player in defaults_from),
        default_room_code=wk.room_code if wk is not None else "",
    )

@app.post("/admin/login")
def admin_login():
    db = SessionLocal()
    code = request.form.get("room_code","").strip()
    season = active_season(db)
    wk = None if season is None else db.query(Week).filter_by(season_id=season.id, room_code=code).first()
    if wk:
        session[ADMIN_SESSION_KEY] = True
    return redirect(url_for("admin"))

@app.post("/admin/logout")
def admin_logout():
    session.pop(ADMIN_SESSION_KEY, None)
    return redirect(url_for("admin"))


@app.post("/admin/import-api-season")
def admin_import_api_season():
    if not is_admin_session():
        abort(403, "Admin locked")
    try:
        players = [name.strip() for name in request.form.get("players", "").split(",")]
        season = init_season_from_api(
            players=players,
            room_code=request.form.get("room_code", ""),
            season_code="year-2",
            season_name=request.form.get("season_name", "2026–27").strip() or "2026–27",
            competition=DEFAULT_API_COMPETITION,
            season_year=int(request.form.get("season_year", DEFAULT_API_SEASON_YEAR)),
        )
        flash(f"Imported 380 fixtures into {season.name}.", "success")
    except (FootballDataError, RuntimeError, ValueError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin"))


@app.post("/admin/sync-api-results")
def admin_sync_api_results():
    if not is_admin_session():
        abort(403, "Admin locked")
    db = SessionLocal()
    season = active_season(db)
    if season is None or season.is_archived:
        abort(403, "No writable active season")
    try:
        summary = sync_results_from_api(season)
        flash(
            f"Score sync complete: {summary['results_imported']} final results imported; "
            f"{summary['pending_matches']} matches pending.",
            "success",
        )
    except (FootballDataError, RuntimeError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin"))


@app.post("/admin/generate-recap")
def admin_generate_recap():
    if not is_admin_session():
        abort(403, "Admin locked")
    db = SessionLocal()
    season = active_season(db)
    if season is None:
        abort(404, "No active season")
    week_number = request.form.get("week", type=int)
    week = None if week_number is None else season_week(db, season, week_number)
    if week is None:
        abort(404, "Week not found")
    version = request.form.get(
        "version", correspondent_default_version()
    ).strip().lower()
    try:
        if version == "v1":
            recap = generate_and_store_weekly_recap(db, week)
        elif version == "v2":
            recap = generate_and_store_weekly_recap_v2(db, week)
        else:
            raise ValueError("Unknown Correspondent version")
        if recap.status == "ready":
            flash(
                f"Week {week.number} {version.upper()} recap revision "
                f"{recap.revision} generated.",
                "success",
            )
        else:
            flash(recap.error_message or "Recap generation failed", "error")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin", week=week.number))


@app.post("/admin/classify-correspondent-sources")
def admin_classify_correspondent_sources():
    if not is_admin_session():
        abort(403, "Admin locked")
    if not correspondent_v2_enabled():
        abort(404, "Correspondent V2 is not enabled")
    db = SessionLocal()
    season = active_season(db)
    if season is None:
        abort(404, "No active season")
    week_number = request.form.get("week", type=int)
    week = None if week_number is None else season_week(db, season, week_number)
    if week is None:
        abort(404, "Week not found")
    try:
        job = submit_week_classification_batch(db, week, pass_number=1)
        flash(
            f"Week {week.number} classification Batch submitted: "
            f"pass={job.pass_number}; requests={job.total_count}; "
            f"status={job.status}; prompt_version={job.prompt_version}.",
            "success",
        )
    except (ValueError, CorrespondentError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin", week=week.number))


@app.post("/admin/sync-correspondent-classification-jobs")
def admin_sync_correspondent_classification_jobs():
    if not is_admin_session():
        abort(403, "Admin locked")
    if not correspondent_v2_enabled():
        abort(404, "Correspondent V2 is not enabled")
    db = SessionLocal()
    season = active_season(db)
    if season is None:
        abort(404, "No active season")
    week_number = request.form.get("week", type=int)
    week = None if week_number is None else season_week(db, season, week_number)
    if week is None:
        abort(404, "Week not found")
    try:
        summary = sync_week_classification_jobs(db, week)
        second_pass = summary.get("second_pass_job_id")
        flash(
            f"Week {week.number} classification status checked: "
            f"jobs={summary['jobs_checked']}; "
            f"classifications_imported={summary['classifications_imported']}; "
            f"pass_2_submitted={'yes' if second_pass is not None else 'no'}; "
            f"prompt_version={summary['prompt_version']}.",
            "success",
        )
    except (ValueError, CorrespondentError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin", week=week.number))


@app.post("/admin/correspondent-sources")
def admin_add_correspondent_source():
    if not is_admin_session():
        abort(403, "Admin locked")
    if not correspondent_v2_enabled():
        abort(404, "Correspondent V2 is not enabled")
    db = SessionLocal()
    season = active_season(db)
    if season is None:
        abort(404, "No active season")
    week_number = request.form.get("week", type=int)
    week = None if week_number is None else season_week(db, season, week_number)
    if week is None:
        abort(404, "Week not found")
    try:
        source = create_manual_correspondent_source(
            db,
            week,
            source_type=request.form.get("source_type", "manual"),
            canonical_url=request.form.get("canonical_url", ""),
            author_name=request.form.get("author_name", ""),
            body_text=request.form.get("body_text", ""),
            submission_note=request.form.get("submission_note", ""),
        )
        flash(f"Added Correspondent source #{source.id}.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin", week=week.number))


@app.post("/admin/select-recap")
def admin_select_recap():
    if not is_admin_session():
        abort(403, "Admin locked")
    db = SessionLocal()
    season = active_season(db)
    if season is None:
        abort(404, "No active season")
    week_number = request.form.get("week", type=int)
    recap_id = request.form.get("recap_id", type=int)
    week = None if week_number is None else season_week(db, season, week_number)
    recap = None if recap_id is None else db.get(WeeklyRecap, recap_id)
    if week is None or recap is None:
        abort(404, "Week or recap not found")
    try:
        select_weekly_recap(db, week, recap)
        flash(
            f"Revision {recap.revision} ({recap.correspondent_version.upper()}) "
            "is now the official recap.",
            "success",
        )
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin", week=week.number))


@app.post("/admin/correspondent-sources/<int:source_id>/status")
def admin_set_correspondent_source_status(source_id: int):
    if not is_admin_session():
        abort(403, "Admin locked")
    if not correspondent_v2_enabled():
        abort(404, "Correspondent V2 is not enabled")
    db = SessionLocal()
    season = active_season(db)
    source = db.get(CorrespondentSource, source_id)
    week = None if source is None else db.get(Week, source.week_id)
    if season is None or source is None or week is None or week.season_id != season.id:
        abort(404, "Correspondent source not found")
    status = request.form.get("status", "").strip().lower()
    if status not in {"accepted", "excluded"}:
        abort(400, "Invalid Correspondent source status")
    source.status = status
    db.add(source)
    db.commit()
    flash(f"Source #{source.id} is now {status}.", "success")
    return redirect(url_for("admin", week=week.number))


@app.post("/tasks/sync-results")
def scheduled_sync_results():
    """Run one score sync from a scheduler without exposing admin credentials."""
    expected_secret = os.environ.get("SYNC_SECRET", "").strip()
    if not expected_secret:
        return jsonify(ok=False, error="Scheduled sync is not configured"), 503

    supplied_secret = request.headers.get("X-Sync-Secret", "")
    if not hmac.compare_digest(supplied_secret, expected_secret):
        return jsonify(ok=False, error="Forbidden"), 403

    db = SessionLocal()
    season = active_season(db)
    if season is None or season.is_archived:
        return jsonify(ok=False, error="No writable active season"), 409

    try:
        summary = sync_results_from_api(season)
    except FootballDataRateLimitError as exc:
        return jsonify(ok=False, error=str(exc)), 429
    except (FootballDataError, RuntimeError) as exc:
        return jsonify(ok=False, error=str(exc)), 502
    return jsonify(ok=True, season=season.code, **summary)


def _correspondent_automation_request_error():
    expected_secret = os.environ.get("CORRESPONDENT_AUTOMATION_SECRET", "").strip()
    if not expected_secret:
        return jsonify(ok=False, error="Correspondent automation is not configured"), 503
    supplied_secret = request.headers.get("X-Correspondent-Automation-Secret", "")
    if not hmac.compare_digest(supplied_secret, expected_secret):
        return jsonify(ok=False, error="Forbidden"), 403
    if not correspondent_v2_enabled():
        return jsonify(ok=False, error="Correspondent V2 is not enabled"), 404
    return None


def _correspondent_automation_week(db, values: Mapping[str, Any]) -> Week:
    season_code = str(values.get("season_code") or "").strip()
    try:
        week_number = int(values.get("week_number"))
    except (TypeError, ValueError) as exc:
        raise ValueError("week_number must be an integer") from exc
    season = db.query(Season).filter_by(
        code=season_code,
        is_active=1,
        is_archived=0,
    ).one_or_none()
    if season is None:
        raise ValueError("Requested season is not the active writable season")
    week = season_week(db, season, week_number)
    if week is None:
        raise ValueError("Week not found")
    return week


@app.get("/tasks/correspondent/status")
def scheduled_correspondent_status():
    auth_error = _correspondent_automation_request_error()
    if auth_error is not None:
        return auth_error
    db = SessionLocal()
    try:
        week = _correspondent_automation_week(db, request.args)
        return jsonify(ok=True, **correspondent_automation_status(db, week))
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400


def _correspondent_automation_json() -> Mapping[str, Any]:
    if not request.is_json:
        raise ValueError("Content-Type must be application/json")
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    unknown = set(payload) - {"season_code", "week_number"}
    if unknown:
        raise ValueError(
            "Unknown field(s): " + ", ".join(sorted(str(field) for field in unknown))
        )
    return payload


def _require_week_correspondent_eligible(db, week: Week) -> None:
    completed_results, fixture_count = count_results_for_week(db, week)
    if (
        week.status != "finalized"
        or week.finalized_at is None
        or fixture_count != 10
        or completed_results != 10
    ):
        raise ValueError("Week is not finalized for Correspondent automation")
    if utcnow() < week.finalized_at + CORRESPONDENT_FINALIZATION_BUFFER:
        raise ValueError("Correspondent one-hour finalization buffer has not elapsed")


def _require_x_collection_complete(db, week: Week) -> None:
    state = db.query(CorrespondentXCollectionState).filter_by(
        week_id=week.id,
    ).one_or_none()
    if state is None or state.status not in {"completed", "capped"}:
        raise ValueError("X collection is not complete for this week")


def _parse_required_utc_timestamp(value: Any, field_name: str) -> datetime:
    try:
        parsed = _parse_correspondent_published_at(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid ISO-8601 timestamp") from exc
    if parsed is None:
        raise ValueError(f"{field_name} is required")
    return parsed


@app.post("/tasks/correspondent/x/claim")
def scheduled_correspondent_x_claim():
    auth_error = _correspondent_automation_request_error()
    if auth_error is not None:
        return auth_error
    db = SessionLocal()
    try:
        if not request.is_json:
            raise ValueError("Content-Type must be application/json")
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object")
        unknown = set(payload) - {
            "season_code", "week_number", "window_start", "window_end"
        }
        if unknown:
            raise ValueError(
                "Unknown field(s): "
                + ", ".join(sorted(str(field) for field in unknown))
            )
        week = _correspondent_automation_week(db, payload)
        state, x_request = claim_x_collection_page(
            db,
            week,
            window_start=_parse_required_utc_timestamp(
                payload.get("window_start"), "window_start"
            ),
            window_end=_parse_required_utc_timestamp(
                payload.get("window_end"), "window_end"
            ),
        )
        return jsonify(
            ok=True,
            claimed=x_request is not None,
            x_request=x_request,
            x_collection=_x_collection_json(state),
        ), 202 if x_request is not None else 200
    except ValueError as exc:
        db.rollback()
        return jsonify(ok=False, error=str(exc)), 409


@app.post("/tasks/correspondent/x/checkpoint")
def scheduled_correspondent_x_checkpoint():
    auth_error = _correspondent_automation_request_error()
    if auth_error is not None:
        return auth_error
    db = SessionLocal()
    try:
        if not request.is_json:
            raise ValueError("Content-Type must be application/json")
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object")
        unknown = set(payload) - {
            "season_code", "week_number", "claim_token", "next_token", "sources"
        }
        if unknown:
            raise ValueError(
                "Unknown field(s): "
                + ", ".join(sorted(str(field) for field in unknown))
            )
        week = _correspondent_automation_week(db, payload)
        state, receipt, imported = checkpoint_x_collection_page(
            db,
            week,
            claim_token=payload.get("claim_token"),
            next_token=payload.get("next_token"),
            sources=payload.get("sources"),
        )
        return jsonify(
            ok=True,
            imported=imported,
            receipt={
                "id": receipt.id,
                "received": receipt.received_count,
                "created": receipt.created_count,
            },
            x_collection=_x_collection_json(state),
        ), 201 if imported else 200
    except CorrespondentIngestNotFound as exc:
        db.rollback()
        return jsonify(ok=False, error=str(exc)), 404
    except (CorrespondentIngestConflict, ValueError) as exc:
        db.rollback()
        return jsonify(ok=False, error=str(exc)), 409


def _classification_job_json(
    job: Optional[CorrespondentClassificationJob],
) -> Optional[Dict[str, Any]]:
    if job is None:
        return None
    return {
        "id": job.id,
        "pass_number": job.pass_number,
        "status": job.status,
        "total": job.total_count,
        "completed": job.completed_count,
        "failed": job.failed_count,
        "prompt_version": job.prompt_version,
    }


@app.post("/tasks/correspondent/classification/submit")
def scheduled_correspondent_classification_submit():
    auth_error = _correspondent_automation_request_error()
    if auth_error is not None:
        return auth_error
    db = SessionLocal()
    try:
        week = _correspondent_automation_week(db, _correspondent_automation_json())
        _require_week_correspondent_eligible(db, week)
        _require_x_collection_complete(db, week)
        job, created = ensure_week_classification_batch(db, week, pass_number=1)
        return jsonify(
            ok=True,
            created=created,
            job=_classification_job_json(job),
            status=correspondent_automation_status(db, week),
        ), 202 if created else 200
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 409
    except CorrespondentError as exc:
        return jsonify(ok=False, error=str(exc)), 502


@app.post("/tasks/correspondent/classification/reconcile")
def scheduled_correspondent_classification_reconcile():
    auth_error = _correspondent_automation_request_error()
    if auth_error is not None:
        return auth_error
    db = SessionLocal()
    try:
        week = _correspondent_automation_week(db, _correspondent_automation_json())
        _require_week_correspondent_eligible(db, week)
        job_count = db.query(CorrespondentClassificationJob).filter_by(
            week_id=week.id,
            prompt_version=CLASSIFIER_PROMPT_VERSION,
        ).count()
        summary = (
            sync_week_classification_jobs(db, week)
            if job_count
            else {
                "jobs_checked": 0,
                "classifications_imported": 0,
                "second_pass_job_id": None,
                "prompt_version": CLASSIFIER_PROMPT_VERSION,
            }
        )
        return jsonify(
            ok=True,
            **summary,
            status=correspondent_automation_status(db, week),
        )
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 409
    except CorrespondentError as exc:
        return jsonify(ok=False, error=str(exc)), 502


@app.post("/tasks/correspondent/recap/generate")
def scheduled_correspondent_recap_generate():
    auth_error = _correspondent_automation_request_error()
    if auth_error is not None:
        return auth_error
    db = SessionLocal()
    try:
        week = _correspondent_automation_week(db, _correspondent_automation_json())
        _require_week_correspondent_eligible(db, week)
        recap = generate_and_store_weekly_recap_v2(
            db,
            week,
            automation_key=correspondent_recap_automation_key(week),
        )
        payload = {
            "id": recap.id,
            "revision": recap.revision,
            "status": recap.status,
            "title": recap.title,
            "prompt_version": recap.prompt_version,
            "automation_key": recap.automation_key,
        }
        if recap.status == "failed":
            return jsonify(
                ok=False,
                error=recap.error_message or "Automated V2 recap generation failed",
                recap=payload,
            ), 502
        return jsonify(
            ok=True,
            recap=payload,
            status=correspondent_automation_status(db, week),
        )
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 409


@app.post("/api/correspondent/sources")
def ingest_correspondent_source_api():
    """Accept one normalized, untrusted source from n8n or another ingester."""
    expected_secret = os.environ.get("CORRESPONDENT_INGEST_SECRET", "").strip()
    if not expected_secret:
        return jsonify(ok=False, error="Correspondent ingestion is not configured"), 503

    supplied_secret = request.headers.get("X-Correspondent-Secret", "")
    if not hmac.compare_digest(supplied_secret, expected_secret):
        return jsonify(ok=False, error="Forbidden"), 403
    if (
        request.content_length
        and request.content_length > CORRESPONDENT_INGEST_MAX_BODY_BYTES
    ):
        return jsonify(ok=False, error="Request body is too large"), 413
    if not request.is_json:
        return jsonify(ok=False, error="Content-Type must be application/json"), 415
    if len(request.get_data(cache=True)) > CORRESPONDENT_INGEST_MAX_BODY_BYTES:
        return jsonify(ok=False, error="Request body is too large"), 413

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(ok=False, error="Request body must be a JSON object"), 400

    db = SessionLocal()
    try:
        source, created = ingest_correspondent_source(db, payload)
    except CorrespondentIngestNotFound as exc:
        return jsonify(ok=False, error=str(exc)), 404
    except CorrespondentIngestConflict as exc:
        return jsonify(ok=False, error=str(exc)), 409
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400

    week = db.get(Week, source.week_id)
    season = None if week is None else db.get(Season, week.season_id)
    return jsonify(
        ok=True,
        created=created,
        source={
            "id": source.id,
            "season_code": None if season is None else season.code,
            "week_number": None if week is None else week.number,
            "provider": source.provider,
            "source_type": source.source_type,
            "external_id": source.external_id,
            "status": source.status,
        },
    ), 201 if created else 200


@app.post("/api/correspondent/sources/batch")
def ingest_correspondent_source_batch_api():
    """Atomically accept one bounded gameweek batch from n8n."""
    expected_secret = os.environ.get("CORRESPONDENT_INGEST_SECRET", "").strip()
    if not expected_secret:
        return jsonify(ok=False, error="Correspondent ingestion is not configured"), 503

    supplied_secret = request.headers.get("X-Correspondent-Secret", "")
    if not hmac.compare_digest(supplied_secret, expected_secret):
        return jsonify(ok=False, error="Forbidden"), 403
    if (
        request.content_length
        and request.content_length > CORRESPONDENT_BATCH_MAX_BODY_BYTES
    ):
        return jsonify(ok=False, error="Request body is too large"), 413
    if not request.is_json:
        return jsonify(ok=False, error="Content-Type must be application/json"), 415
    if len(request.get_data(cache=True)) > CORRESPONDENT_BATCH_MAX_BODY_BYTES:
        return jsonify(ok=False, error="Request body is too large"), 413

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(ok=False, error="Request body must be a JSON object"), 400
    unknown_fields = set(payload) - {"sources"}
    if unknown_fields:
        return jsonify(
            ok=False,
            error="Unknown batch field(s): "
            + ", ".join(sorted(str(field) for field in unknown_fields)),
        ), 400
    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        return jsonify(ok=False, error="sources must be a non-empty array"), 400
    if len(sources) > CORRESPONDENT_BATCH_MAX_SOURCES:
        return jsonify(
            ok=False,
            error=(
                "sources must contain no more than "
                f"{CORRESPONDENT_BATCH_MAX_SOURCES:,} items"
            ),
        ), 400

    db = SessionLocal()
    results: List[Tuple[CorrespondentSource, bool]] = []
    failed_index: Optional[int] = None
    try:
        for failed_index, source_payload in enumerate(sources):
            results.append(
                ingest_correspondent_source(db, source_payload, commit=False)
            )
        db.commit()
    except CorrespondentIngestNotFound as exc:
        db.rollback()
        return jsonify(ok=False, error=str(exc), failed_index=failed_index), 404
    except CorrespondentIngestConflict as exc:
        db.rollback()
        return jsonify(ok=False, error=str(exc), failed_index=failed_index), 409
    except ValueError as exc:
        db.rollback()
        return jsonify(ok=False, error=str(exc), failed_index=failed_index), 400

    created_count = sum(1 for _, created in results if created)
    return jsonify(
        ok=True,
        received=len(sources),
        created=created_count,
        existing=len(sources) - created_count,
        sources=[
            {
                "id": source.id,
                "external_id": source.external_id,
                "created": created,
            }
            for source, created in results
        ],
    ), 201 if created_count else 200


@app.post("/admin/set-results")
def admin_set_results():
    if not is_admin_session():
        abort(403, "Admin locked")
    db = SessionLocal()
    season = active_season(db)
    if season is None or season.is_archived:
        abort(403, "No writable active season")
    wk_number = int(request.form["week"])
    wk = season_week(db, season, wk_number)
    if not wk:
        abort(404, "Week not found")

    fixtures = db.query(Fixture).filter_by(week_id=wk.id).all()
    # Apply changes fixture-by-fixture
    for f in fixtures:
        key = f"outcome_{f.id}"
        raw = request.form.get(key, "").strip()
        if not raw:
            continue  # no change
        # Map team-name/Draw into canonical
        if raw.lower() == "draw":
            outcome = "Draw"
        elif raw == f.home:
            outcome = "Home"
        elif raw == f.away:
            outcome = "Away"
        else:
            continue  # ignore invalid values
        existing = db.query(Result).filter_by(fixture_id=f.id).first()
        if existing:
            existing.outcome = outcome
            existing.home_score = None
            existing.away_score = None
            existing.source = "manual"
            existing.updated_at = utcnow()
        else:
            db.add(Result(
                fixture_id=f.id,
                outcome=outcome,
                source="manual",
                updated_at=utcnow(),
            ))

    # Optional: force status back to provisional, useful after correcting a finalized week
    if request.form.get("force_status") == "provisional":
        wk.status = "provisional"

    db.commit()
    # Recompute status in case everything is filled
    update_week_status(db, wk)
    return redirect(url_for("admin", week=wk.number))

@app.get("/tab/season")
def tab_season():
    db = SessionLocal()
    you = current_player(db)
    selected_season = requested_season(db)
    if selected_season is None:
        return "<div class='card'>No seasons initialized yet.</div>"
    seasons = db.query(Season).order_by(Season.id.desc()).all()
    weeks = db.query(Week).filter_by(season_id=selected_season.id).order_by(Week.number.asc()).all()
    players = season_players(db, selected_season)
    totals = season_totals_finalized(db, selected_season)
    details = season_detailed_totals_finalized(db, selected_season)
    season_rows = []
    for p in players:
        row = {
            "name": p.name,
            "for": totals.get(p.id, {}).get("for", 0),
            "against": totals.get(p.id, {}).get("against", 0),
            "net": totals.get(p.id, {}).get("net", 0),
        }
        row.update(details.get(p.id, {}))
        row["for_net"] = row["correct"] - row["incorrect"]
        row["against_net"] = row["against_correct"] - row["against_incorrect"]
        row["total_net"] = row["for_net"] - row["against_net"]
        season_rows.append(row)

    # Standings: net points first, then correct picks. Exact ties share a rank.
    season_rows.sort(key=lambda row: (-row["net"], -row["correct"], row["name"].lower()))
    previous_key = None
    for position, row in enumerate(season_rows, start=1):
        rank_key = (row["net"], row["correct"])
        if rank_key != previous_key:
            current_rank = position
            previous_key = rank_key
        row["rank"] = current_rank
    weekly_points: Dict[int, Dict[int,int]] = {}
    for wk in weeks:
        weekly_points[wk.number] = weekly_points_map(db, wk)
    return render_template_string(SEASON_PARTIAL, season_rows=season_rows, players=players,
                                  weeks=weeks, weekly_points=weekly_points, you=you,
                                  seasons=seasons, selected_season=selected_season)


@app.get("/tab/stats")
def tab_stats():
    db = SessionLocal()
    you = current_player(db)
    selected_season = requested_season(db)
    if selected_season is None:
        return "<div class='card'>No seasons initialized yet.</div>"
    seasons = db.query(Season).order_by(Season.id.desc()).all()
    players = season_players(db, selected_season)
    requested_player_id = request.args.get("player", type=int)
    selected_player = next(
        (player for player in players if player.id == requested_player_id), None
    )
    if selected_player is None and you is not None:
        selected_player = next((player for player in players if player.id == you.id), None)
    if selected_player is None and players:
        selected_player = players[0]

    min_picks = request.args.get("min_picks", default=1, type=int)
    if min_picks not in (1, 3, 5, 10):
        min_picks = 1
    club_sort = request.args.get("club_sort", "best")
    if club_sort not in ("best", "worst", "most"):
        club_sort = "best"
    club_filter = request.args.get("club", "").strip()

    head_to_head = []
    club_records = []
    club_names: List[str] = []
    if selected_player is not None:
        head_to_head = head_to_head_for_player(db, selected_season, selected_player)
        all_club_records = club_records_for_player(
            db, selected_season, selected_player, min_picks=1, sort_mode="most"
        )
        club_names = sorted(
            (row["club"] for row in all_club_records), key=str.lower
        )
        club_records = club_records_for_player(
            db,
            selected_season,
            selected_player,
            club_filter=club_filter,
            min_picks=min_picks,
            sort_mode=club_sort,
        )

    return render_template_string(
        STATS_PARTIAL,
        seasons=seasons,
        selected_season=selected_season,
        players=players,
        selected_player=selected_player,
        head_to_head=head_to_head,
        club_records=club_records,
        club_names=club_names,
        club_filter=club_filter,
        min_picks=min_picks,
        club_sort=club_sort,
        leader_stats=season_leader_stats(db, selected_season),
        you=you,
    )

# -------------------- Page shell --------------------
@app.route("/")
def shell():
    db = SessionLocal()
    you = current_player(db)
    initial = tab_current()
    arsenal_banter_images = [
        url_for("static", filename=filename)
        for filename in ARSENAL_BANTER_FILENAMES
    ]
    return render_template_string(
        BASE_HTML,
        you=you,
        active_tab='current',
        body=initial,
        arsenal_banter_images=arsenal_banter_images,
    )

# -------------------- Partials used within tabs --------------------
@app.route("/partials/fixtures/<int:week_number>")
def fixtures_partial(week_number: int):
    db = SessionLocal()
    season = requested_season(db)
    wk = None if season is None else season_week(db, season, week_number)
    if wk is None:
        abort(404, "Week not found")
    fixtures = db.query(Fixture).filter_by(week_id=wk.id).order_by(Fixture.match_number.asc()).all()
    return render_template_string(FIXTURES_PARTIAL, fixtures=fixtures)

@app.route("/partials/matchups/<int:week_number>")
def matchups_partial(week_number: int):
    db = SessionLocal()
    season = requested_season(db)
    wk = None if season is None else season_week(db, season, week_number)
    if wk is None:
        abort(404, "Week not found")
    you = current_player(db)
    matchups = []
    for m in db.query(Matchup).filter_by(week_id=wk.id).all():
        turn_id = compute_next_turn(db, m)
        avail = available_fixtures_for_matchup(db, m)
        avail_view = [{"id": f.id, "match_number": f.match_number, "home": f.home, "away": f.away} for f in avail]
        log = []
        for p in db.query(Pick).filter_by(matchup_id=m.id).order_by(Pick.created_at.asc(), Pick.id.asc()).all():
            log.append({
                "player": p.player.name,
                "match_number": p.fixture.match_number,
                "home": p.fixture.home,
                "away": p.fixture.away,
                "team": p.team,
                "when": p.created_at.strftime("%H:%M:%S")
            })
        matchups.append({
            "id": m.id,
            "a": m.player_a.name,
            "b": m.player_b.name,
            "first": m.first_picker.name,
            "turn_id": turn_id,
            "turn_name": db.query(Player).get(turn_id).name,
            "available": avail_view,
            "log": log
        })
    return render_template_string(MATCHUPS_PARTIAL, matchups=matchups, week=wk, season=season,
                                  read_only=bool(season.is_archived), you=you)

@app.route("/partials/scores/<int:week_number>")
def scores_partial(week_number: int):
    db = SessionLocal()
    season = requested_season(db)
    wk = None if season is None else season_week(db, season, week_number)
    if wk is None:
        abort(404, "Week not found")
    update_week_status(db, wk)
    points = weekly_points_map(db, wk)
    records = weekly_pick_records(db, wk)
    scores = []
    for player in db.query(Player).order_by(Player.name.asc()).all():
        record = records.get(player.id, {"correct": 0, "incorrect": 0, "draws": 0})
        scores.append({
            "name": player.name,
            "points": points.get(player.id, 0),
            "games_finalized": record["correct"] + record["incorrect"] + record["draws"],
            "correct": record["correct"],
            "incorrect": record["incorrect"],
            "draws": record["draws"],
        })
    payouts = payouts_for_week(db, wk)
    fixtures = db.query(Fixture).filter_by(week_id=wk.id).order_by(Fixture.match_number.asc()).all()
    results_map = {
        result.fixture_id: result
        for result in db.query(Result).join(Fixture).filter(Fixture.week_id == wk.id)
    }
    fixtures_with_results = []
    for f in fixtures:
        result = results_map.get(f.id)
        outcome = None if result is None else result.outcome
        if outcome == "Home":
            display = f.home
        elif outcome == "Away":
            display = f.away
        elif outcome == "Draw":
            display = "Draw"
        else:
            display = "—"
        score_display = "—"
        source_display = "—"
        if result is not None:
            if result.home_score is not None and result.away_score is not None:
                score_display = f"{result.home_score}–{result.away_score}"
            source_display = "API" if result.source == FOOTBALL_DATA_PROVIDER else "Manual"
        fixtures_with_results.append({
            "match_number": f.match_number,
            "home": f.home,
            "away": f.away,
            "score_display": score_display,
            "outcome_display": display,
            "source_display": source_display,
        })
    return render_template_string(SCORES_PARTIAL, week=wk, season=season, scores=scores, payouts=payouts,
                                  fixtures=fixtures, fixtures_with_results=fixtures_with_results,
                                  read_only=bool(season.is_archived))


def payouts_for_week(db, week):
    points = weekly_points_map(db, week)
    rows = []
    for m in db.query(Matchup).filter_by(week_id=week.id).all():
        pa = points.get(m.player_a_id, 0); pb = points.get(m.player_b_id, 0)
        diff = pa - pb
        if diff > 0:
            rows.append({"from": m.player_b.name, "to": m.player_a.name, "points": diff, "payout": diff*5})
        elif diff < 0:
            rows.append({"from": m.player_a.name, "to": m.player_b.name, "points": -diff, "payout": -diff*5})
        else:
            rows.append({"from": "-", "to": "-", "points": 0, "payout": 0})
    return rows

# ---------- Outcome options for team-name results (returns full <select>) ----------
@app.get("/partials/outcome-options")
def outcome_options():
    """Return a full <select> for the outcome based on selected fixture."""
    db = SessionLocal()
    fx_id = request.args.get("fixture_id", type=int) or request.form.get("fixture_id", type=int)
    if not fx_id:
        return '<select name="outcome" id="outcome-options"><option>Draw</option></select>'
    fx = db.query(Fixture).get(int(fx_id))
    if not fx:
        return '<select name="outcome" id="outcome-options"><option>Draw</option></select>'
    return (
        f'<select name="outcome" id="outcome-options">'
        f'<option>{fx.home}</option>'
        f'<option>{fx.away}</option>'
        f'<option>Draw</option>'
        f'</select>'
    )

# -------------------- Actions --------------------
@app.post("/pick")
def make_pick():
    db = SessionLocal()
    me = current_player(db)
    if me is None:
        abort(403, "Not logged in")
    season = requested_season(db, request.form.get("season"))
    if season is None or season.is_archived:
        abort(403, "Archived seasons are read only")
    wk_number = int(request.form["week"])
    wk = season_week(db, season, wk_number)
    if wk is None:
        abort(404, "Week not found")
    m = db.query(Matchup).get(int(request.form["matchup_id"]))
    fx_id = int(request.form["fixture_id"])
    team_name = request.form["team"].strip()

    if m.week_id != wk.id:
        abort(400, "Bad matchup/week")

    turn_id = compute_next_turn(db, m)
    if me.id != turn_id:
        abort(400, "Not your turn in this matchup")

    # Ensure the fixture is still available in this matchup
    avail_ids = [f.id for f in available_fixtures_for_matchup(db, m)]
    if fx_id not in avail_ids:
        abort(400, "Fixture already taken or not in this week")

    fx = db.query(Fixture).get(fx_id)
    if team_name not in (fx.home, fx.away):
        abort(400, "Team must be one of the fixture teams")

    try:
        p = Pick(matchup_id=m.id, player_id=me.id, fixture_id=fx.id, team=team_name)
        db.add(p); db.commit()
    except Exception as e:
        db.rollback()
        abort(400, f"Pick failed: {e}")

    # Re-render the matchups panel after pick. Arsenal gets a one-time client
    # event in this successful POST response, so refreshes never replay it.
    response = make_response(matchups_partial(wk.number))
    if team_name.casefold() in ARSENAL_TEAM_ALIASES:
        response.headers["HX-Trigger"] = json.dumps({"arsenalBanter": {}})
    return response

@app.post("/set_result")
def set_result():
    db = SessionLocal()
    season = requested_season(db, request.form.get("season"))
    if season is None or season.is_archived:
        abort(403, "Archived seasons are read only")
    wk_number = int(request.form["week"])
    wk = season_week(db, season, wk_number)
    if wk is None:
        abort(404, "Week not found")
    fx_id = int(request.form["fixture_id"])
    raw = request.form["outcome"].strip()

    fx = db.query(Fixture).get(fx_id)
    if fx.week_id != wk.id:
        abort(400, "Fixture not in this week")

    # Map team name / Draw to canonical outcome
    if raw.lower() == "draw":
        outcome = "Draw"
    elif raw == fx.home:
        outcome = "Home"
    elif raw == fx.away:
        outcome = "Away"
    else:
        abort(400, "Outcome must be one of the fixture's team names or Draw")

    existing = db.query(Result).filter_by(fixture_id=fx.id).first()
    if existing:
        existing.outcome = outcome
        existing.home_score = None
        existing.away_score = None
        existing.source = "manual"
        existing.updated_at = utcnow()
    else:
        db.add(Result(
            fixture_id=fx.id,
            outcome=outcome,
            source="manual",
            updated_at=utcnow(),
        ))
    db.commit()
    # Auto-finalization update
    update_week_status(db, wk)
    return scores_partial(wk.number)

# -------------------- Join flow --------------------
@app.route("/join", methods=["GET", "POST"])
def join():
    db = SessionLocal()
    # Determine a sensible week to join against
    season = active_season(db)
    wk = None if season is None else current_drafting_week(db, season)
    allowed_names = [] if season is None else [p.name for p in season_players(db, season)]
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        code = request.form.get("room_code", "").strip()
        if wk is None:
            abort(400, "Week not initialized yet.")
        if code != wk.room_code:
            abort(403, "Wrong room code.")
        if name not in allowed_names:
            abort(403, "Name not in allowed players.")
        session["player_name"] = name
        return redirect(url_for("shell"))
    return render_template_string(JOIN_HTML, allowed_names=allowed_names)

# -------------------- Initialization helpers --------------------
def parse_weeks_arg(weeks_arg: str, df: pd.DataFrame) -> List[int]:
    if weeks_arg.strip().lower() in ("all", "any"):
        return sorted(int(x) for x in df["Round Number"].unique().tolist())
    parts = [p.strip() for p in weeks_arg.split(",")]
    out = set()
    for p in parts:
        if "-" in p:
            a, b = p.split("-", 1)
            a, b = int(a), int(b)
            for n in range(min(a,b), max(a,b)+1):
                out.add(n)
        else:
            out.add(int(p))
    return sorted(out)

def _delete_season_weeks(db, season: Season) -> None:
    weeks = db.query(Week).filter_by(season_id=season.id).all()
    week_ids = [week.id for week in weeks]
    if not week_ids:
        return
    fixture_ids = [row[0] for row in db.query(Fixture.id).filter(Fixture.week_id.in_(week_ids)).all()]
    matchup_ids = [row[0] for row in db.query(Matchup.id).filter(Matchup.week_id.in_(week_ids)).all()]
    recap_ids = [
        row[0]
        for row in db.query(WeeklyRecap.id).filter(
            WeeklyRecap.week_id.in_(week_ids)
        ).all()
    ]
    source_ids = [
        row[0]
        for row in db.query(CorrespondentSource.id).filter(
            CorrespondentSource.week_id.in_(week_ids)
        ).all()
    ]
    db.query(WeeklyRecapSelection).filter(
        WeeklyRecapSelection.week_id.in_(week_ids)
    ).delete(synchronize_session=False)
    if recap_ids:
        db.query(RecapSourceUsage).filter(
            RecapSourceUsage.recap_id.in_(recap_ids)
        ).delete(synchronize_session=False)
    if source_ids:
        db.query(RecapSourceUsage).filter(
            RecapSourceUsage.source_id.in_(source_ids)
        ).delete(synchronize_session=False)
        db.query(CorrespondentSourceClassification).filter(
            CorrespondentSourceClassification.source_id.in_(source_ids)
        ).delete(synchronize_session=False)
    db.query(WeeklyRecap).filter(WeeklyRecap.week_id.in_(week_ids)).delete(
        synchronize_session=False
    )
    db.query(CorrespondentSource).filter(
        CorrespondentSource.week_id.in_(week_ids)
    ).delete(synchronize_session=False)
    if fixture_ids:
        db.query(Result).filter(Result.fixture_id.in_(fixture_ids)).delete(synchronize_session=False)
    if matchup_ids:
        db.query(Pick).filter(Pick.matchup_id.in_(matchup_ids)).delete(synchronize_session=False)
    db.query(Matchup).filter(Matchup.week_id.in_(week_ids)).delete(synchronize_session=False)
    db.query(Fixture).filter(Fixture.week_id.in_(week_ids)).delete(synchronize_session=False)
    db.query(Week).filter(Week.id.in_(week_ids)).delete(synchronize_session=False)


def init_weeks_from_csv(
    csv_path: str,
    weeks: Iterable[int],
    players: List[str],
    room_code: str,
    season_code: str = "year-2",
    season_name: str = "Year 2",
    allow_reset: bool = False,
):
    db = SessionLocal()
    df = pd.read_csv(csv_path)

    season = db.query(Season).filter_by(code=season_code).first()
    if season is None:
        season = Season(code=season_code, name=season_name, is_active=1, is_archived=0)
        db.add(season)
        db.flush()
    else:
        existing_weeks = db.query(Week).filter_by(season_id=season.id).count()
        if (season.is_archived or existing_weeks) and not allow_reset:
            raise RuntimeError(
                f"{season.name} already contains data. Set ALLOW_SEASON_RESET=1 only after making a backup."
            )
        if allow_reset:
            _delete_season_weeks(db, season)
        season.name = season_name
        season.is_archived = 0
        season.is_active = 1

    db.query(Season).filter(Season.id != season.id).update(
        {Season.is_active: 0}, synchronize_session=False
    )

    # Players are shared across seasons; add missing names without deleting history.
    for name in players:
        if db.query(Player).filter_by(name=name).first() is None:
            db.add(Player(name=name))
    db.commit()

    for week_number in weeks:
        wk = Week(
            season_id=season.id,
            number=week_number,
            room_code=room_code,
            status="drafting",
        )
        db.add(wk)
        db.flush()

        wkdf = df[df["Round Number"] == week_number]
        for _, r in wkdf.iterrows():
            db.add(Fixture(week_id=wk.id,
                           match_number=int(r["Match Number"]),
                           home=str(r["Home Team"]),
                           away=str(r["Away Team"])))
        db.commit()

        # create 3 matchups for the week
        pls = db.query(Player).filter(Player.name.in_(players)).order_by(Player.name.asc()).all()
        names = [p.name for p in pls]
        random.shuffle(names)
        pairs = [(names[i], names[i+1]) for i in range(0, len(names), 2)]
        for a_name, b_name in pairs:
            a = db.query(Player).filter_by(name=a_name).first()
            b = db.query(Player).filter_by(name=b_name).first()
            first = random.choice([a, b])
            db.add(Matchup(week_id=wk.id, player_a_id=a.id, player_b_id=b.id, first_picker_id=first.id))
        db.commit()

    return season

# -------------------- CLI --------------------
def main():
    parser = argparse.ArgumentParser(description="Pick 'Em Flask + HTMX (tabs, multi-week, team-name picks)")
    parser.add_argument("--csv", help="Legacy CSV path (only needed with INIT_ON_START=1)")
    parser.add_argument("--weeks", default="all", help="Legacy CSV weeks to initialize")
    parser.add_argument("--players", default="", help="Comma-separated 6 player names")
    parser.add_argument("--room", default="", help="Room code (shared password)")
    parser.add_argument("--season-code", default=os.environ.get("SEASON_CODE", "year-2"))
    parser.add_argument("--season-name", default=os.environ.get("SEASON_NAME", "Year 2"))
    parser.add_argument(
        "--sync-api-once",
        action="store_true",
        help="Synchronize the active API-backed season and exit (for a scheduled job)",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    if args.sync_api_once:
        db = SessionLocal()
        season = active_season(db)
        if season is None:
            raise RuntimeError("No active season to synchronize")
        summary = sync_results_from_api(season)
        print(
            f"Score sync complete: {summary['results_imported']} final results imported; "
            f"{summary['pending_matches']} pending."
        )
        return

    # --- Only initialize when requested (so we don't wipe DB on every restart) ---
    do_init = os.environ.get("INIT_ON_START", "0") == "1"
    if do_init:
        players = [p.strip() for p in args.players.split(",") if p.strip()]
        if len(players) != 6:
            raise ValueError("Please supply exactly 6 players for CSV initialization")
        if not args.csv or not args.room:
            raise ValueError("--csv and --room are required when INIT_ON_START=1")
        df = pd.read_csv(args.csv)
        weeks = parse_weeks_arg(args.weeks, df)
        if not weeks:
            print("No weeks selected to initialize.")
            return
        allow_reset = os.environ.get("ALLOW_SEASON_RESET", "0") == "1"
        init_weeks_from_csv(
            args.csv,
            weeks,
            players,
            args.room,
            season_code=args.season_code,
            season_name=args.season_name,
            allow_reset=allow_reset,
        )

    app.jinja_env.globals.update(zip=zip)

    # Stable run (no reloader); enable threading for concurrency
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False, threaded=True)

if __name__ == "__main__":
    main()
