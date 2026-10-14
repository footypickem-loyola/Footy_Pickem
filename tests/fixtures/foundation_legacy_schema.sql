-- Frozen pre-foundation ORM schema from adf2117; no production rows.
CREATE TABLE players (
	id INTEGER NOT NULL, 
	name VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
);

CREATE TABLE seasons (
	id INTEGER NOT NULL, 
	code VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	is_active INTEGER NOT NULL, 
	is_archived INTEGER NOT NULL, 
	api_competition_code VARCHAR, 
	api_season_year INTEGER, 
	created_at DATETIME, 
	PRIMARY KEY (id), 
	UNIQUE (code)
);

CREATE TABLE api_sync_states (
	id INTEGER NOT NULL, 
	season_id INTEGER NOT NULL, 
	provider VARCHAR NOT NULL, 
	last_attempt_at DATETIME, 
	last_success_at DATETIME, 
	last_error VARCHAR, 
	requests_remaining INTEGER, 
	reset_seconds INTEGER, 
	fixtures_imported INTEGER NOT NULL, 
	results_imported INTEGER NOT NULL, 
	pending_matches INTEGER NOT NULL, 
	unmatched_matches INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (season_id), 
	FOREIGN KEY(season_id) REFERENCES seasons (id)
);

CREATE TABLE club_season_enrichment (
	season_id INTEGER NOT NULL, 
	sportmonks_team_id INTEGER NOT NULL, 
	sportmonks_season_id INTEGER NOT NULL, 
	league_id INTEGER NOT NULL, 
	footy_club VARCHAR NOT NULL, 
	crest_url VARCHAR, 
	top_scorers JSON NOT NULL, 
	goals INTEGER, 
	synced_at DATETIME NOT NULL, 
	PRIMARY KEY (season_id, sportmonks_team_id), 
	UNIQUE (season_id, footy_club), 
	FOREIGN KEY(season_id) REFERENCES seasons (id)
);

CREATE TABLE weeks (
	id INTEGER NOT NULL, 
	season_id INTEGER NOT NULL, 
	number INTEGER NOT NULL, 
	room_code VARCHAR NOT NULL, 
	status VARCHAR, 
	finalized_at DATETIME, 
	PRIMARY KEY (id), 
	CONSTRAINT uix_season_week_number UNIQUE (season_id, number), 
	FOREIGN KEY(season_id) REFERENCES seasons (id)
);

CREATE TABLE correspondent_classification_jobs (
	id INTEGER NOT NULL, 
	season_id INTEGER NOT NULL, 
	week_id INTEGER NOT NULL, 
	prompt_version VARCHAR NOT NULL, 
	pass_number INTEGER NOT NULL, 
	model VARCHAR NOT NULL, 
	openai_batch_id VARCHAR, 
	input_file_id VARCHAR, 
	output_file_id VARCHAR, 
	error_file_id VARCHAR, 
	status VARCHAR NOT NULL, 
	total_count INTEGER NOT NULL, 
	completed_count INTEGER NOT NULL, 
	failed_count INTEGER NOT NULL, 
	error_message TEXT, 
	created_at DATETIME NOT NULL, 
	submitted_at DATETIME, 
	last_synced_at DATETIME, 
	completed_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(season_id) REFERENCES seasons (id), 
	FOREIGN KEY(week_id) REFERENCES weeks (id), 
	UNIQUE (openai_batch_id)
);

CREATE TABLE correspondent_notifications (
	id INTEGER NOT NULL, 
	week_id INTEGER NOT NULL, 
	event_type VARCHAR NOT NULL, 
	delivery_marker VARCHAR NOT NULL, 
	recipients_json TEXT NOT NULL, 
	payload_json TEXT NOT NULL, 
	status VARCHAR NOT NULL, 
	claim_token VARCHAR, 
	claim_expires_at DATETIME, 
	gmail_message_id VARCHAR, 
	error_message TEXT, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	sent_at DATETIME, 
	PRIMARY KEY (id), 
	CONSTRAINT uix_week_notification_event UNIQUE (week_id, event_type), 
	FOREIGN KEY(week_id) REFERENCES weeks (id), 
	UNIQUE (delivery_marker), 
	UNIQUE (claim_token)
);

CREATE TABLE correspondent_x_collection_states (
	id INTEGER NOT NULL, 
	week_id INTEGER NOT NULL, 
	status VARCHAR NOT NULL, 
	window_start DATETIME NOT NULL, 
	window_end DATETIME NOT NULL, 
	next_token VARCHAR, 
	page_count INTEGER NOT NULL, 
	retrieved_count INTEGER NOT NULL, 
	persisted_count INTEGER NOT NULL, 
	lease_token VARCHAR, 
	lease_expires_at DATETIME, 
	last_error TEXT, 
	cap_reached_at DATETIME, 
	completed_at DATETIME, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (week_id), 
	FOREIGN KEY(week_id) REFERENCES weeks (id), 
	UNIQUE (lease_token)
);

CREATE TABLE fixtures (
	id INTEGER NOT NULL, 
	week_id INTEGER NOT NULL, 
	match_number INTEGER NOT NULL, 
	home VARCHAR NOT NULL, 
	away VARCHAR NOT NULL, 
	venue VARCHAR, 
	external_match_id INTEGER, 
	kickoff_utc DATETIME, 
	api_status VARCHAR, 
	PRIMARY KEY (id), 
	CONSTRAINT uix_week_matchnumber UNIQUE (week_id, match_number), 
	FOREIGN KEY(week_id) REFERENCES weeks (id), 
	UNIQUE (external_match_id)
);

CREATE TABLE matchups (
	id INTEGER NOT NULL, 
	week_id INTEGER NOT NULL, 
	player_a_id INTEGER NOT NULL, 
	player_b_id INTEGER NOT NULL, 
	first_picker_id INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(week_id) REFERENCES weeks (id), 
	FOREIGN KEY(player_a_id) REFERENCES players (id), 
	FOREIGN KEY(player_b_id) REFERENCES players (id), 
	FOREIGN KEY(first_picker_id) REFERENCES players (id)
);

CREATE TABLE pick_insight_refreshes (
	week_id INTEGER NOT NULL, 
	season_id INTEGER NOT NULL, 
	status VARCHAR NOT NULL, 
	created_at DATETIME NOT NULL, 
	completed_at DATETIME, 
	claim_token VARCHAR, 
	lease_until DATETIME, 
	PRIMARY KEY (week_id), 
	FOREIGN KEY(week_id) REFERENCES weeks (id), 
	FOREIGN KEY(season_id) REFERENCES seasons (id)
);

CREATE INDEX ix_pick_insight_refreshes_season_id ON pick_insight_refreshes (season_id);
CREATE TABLE weekly_recaps (
	id INTEGER NOT NULL, 
	week_id INTEGER NOT NULL, 
	revision INTEGER NOT NULL, 
	status VARCHAR NOT NULL, 
	title VARCHAR, 
	body_markdown TEXT, 
	context_json TEXT NOT NULL, 
	context_hash VARCHAR NOT NULL, 
	prompt_version VARCHAR NOT NULL, 
	model VARCHAR NOT NULL, 
	provider_response_id VARCHAR, 
	error_message TEXT, 
	correspondent_version VARCHAR NOT NULL, 
	source_count INTEGER NOT NULL, 
	external_context_hash VARCHAR, 
	automation_key VARCHAR, 
	created_at DATETIME NOT NULL, 
	completed_at DATETIME, 
	PRIMARY KEY (id), 
	CONSTRAINT uix_weekly_recap_revision UNIQUE (week_id, revision), 
	FOREIGN KEY(week_id) REFERENCES weeks (id), 
	UNIQUE (automation_key)
);

CREATE TABLE auto_draft_preferences (
	id INTEGER NOT NULL, 
	matchup_id INTEGER NOT NULL, 
	player_id INTEGER NOT NULL, 
	fixture_id INTEGER NOT NULL, 
	team VARCHAR NOT NULL, 
	priority INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uix_auto_draft_preference UNIQUE (matchup_id, player_id, fixture_id), 
	FOREIGN KEY(matchup_id) REFERENCES matchups (id), 
	FOREIGN KEY(player_id) REFERENCES players (id), 
	FOREIGN KEY(fixture_id) REFERENCES fixtures (id)
);

CREATE TABLE auto_draft_settings (
	id INTEGER NOT NULL, 
	matchup_id INTEGER NOT NULL, 
	player_id INTEGER NOT NULL, 
	enabled INTEGER NOT NULL, 
	confirmed_at DATETIME, 
	PRIMARY KEY (id), 
	CONSTRAINT uix_auto_draft_setting UNIQUE (matchup_id, player_id), 
	FOREIGN KEY(matchup_id) REFERENCES matchups (id), 
	FOREIGN KEY(player_id) REFERENCES players (id)
);

CREATE TABLE correspondent_email_deliveries (
	id INTEGER NOT NULL, 
	recap_id INTEGER NOT NULL, 
	delivery_marker VARCHAR NOT NULL, 
	recipients_json TEXT NOT NULL, 
	status VARCHAR NOT NULL, 
	claim_token VARCHAR, 
	claim_expires_at DATETIME, 
	gmail_message_id VARCHAR, 
	error_message TEXT, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	sent_at DATETIME, 
	PRIMARY KEY (id), 
	UNIQUE (recap_id), 
	FOREIGN KEY(recap_id) REFERENCES weekly_recaps (id), 
	UNIQUE (delivery_marker), 
	UNIQUE (claim_token)
);

CREATE TABLE correspondent_sources (
	id INTEGER NOT NULL, 
	week_id INTEGER NOT NULL, 
	fixture_id INTEGER, 
	provider VARCHAR NOT NULL, 
	source_type VARCHAR NOT NULL, 
	external_id VARCHAR, 
	canonical_url VARCHAR, 
	author_name VARCHAR, 
	body_text TEXT NOT NULL, 
	published_at DATETIME, 
	submitted_by_player_id INTEGER, 
	submission_note TEXT, 
	metadata_json TEXT, 
	content_hash VARCHAR NOT NULL, 
	status VARCHAR NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uix_correspondent_source_content UNIQUE (week_id, provider, content_hash), 
	FOREIGN KEY(week_id) REFERENCES weeks (id), 
	FOREIGN KEY(fixture_id) REFERENCES fixtures (id), 
	FOREIGN KEY(submitted_by_player_id) REFERENCES players (id)
);

CREATE TABLE correspondent_x_page_receipts (
	id INTEGER NOT NULL, 
	collection_id INTEGER NOT NULL, 
	claim_token VARCHAR NOT NULL, 
	request_token VARCHAR, 
	next_token VARCHAR, 
	received_count INTEGER NOT NULL, 
	created_count INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(collection_id) REFERENCES correspondent_x_collection_states (id), 
	UNIQUE (claim_token)
);

CREATE TABLE fixture_provider_links (
	id INTEGER NOT NULL, 
	fixture_id INTEGER NOT NULL, 
	provider VARCHAR NOT NULL, 
	external_fixture_id VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (provider, external_fixture_id), 
	UNIQUE (fixture_id, provider), 
	FOREIGN KEY(fixture_id) REFERENCES fixtures (id)
);

CREATE TABLE live_fixture_states (
	fixture_id INTEGER NOT NULL, 
	provider VARCHAR NOT NULL, 
	state VARCHAR NOT NULL, 
	is_live BOOLEAN NOT NULL, 
	home_score INTEGER, 
	away_score INTEGER, 
	minute INTEGER, 
	extra_minute INTEGER, 
	provider_updated_at VARCHAR, 
	last_synced_at DATETIME NOT NULL, 
	PRIMARY KEY (fixture_id), 
	FOREIGN KEY(fixture_id) REFERENCES fixtures (id)
);

CREATE TABLE match_events (
	id INTEGER NOT NULL, 
	fixture_id INTEGER NOT NULL, 
	provider VARCHAR NOT NULL, 
	external_event_id VARCHAR NOT NULL, 
	event_type VARCHAR NOT NULL, 
	minute INTEGER, 
	extra_minute INTEGER, 
	team_provider_id VARCHAR, 
	team_name VARCHAR, 
	side VARCHAR, 
	player_provider_id VARCHAR, 
	player_name VARCHAR, 
	related_player_provider_id VARCHAR, 
	related_player_name VARCHAR, 
	detail VARCHAR, 
	running_score VARCHAR, 
	sort_order INTEGER, 
	is_active BOOLEAN NOT NULL, 
	provider_updated_at VARCHAR, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	revisions JSON NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (fixture_id, provider, external_event_id), 
	FOREIGN KEY(fixture_id) REFERENCES fixtures (id)
);

CREATE INDEX ix_match_events_fixture_id ON match_events (fixture_id);
CREATE TABLE picks (
	id INTEGER NOT NULL, 
	matchup_id INTEGER NOT NULL, 
	player_id INTEGER NOT NULL, 
	fixture_id INTEGER NOT NULL, 
	team VARCHAR NOT NULL, 
	created_at DATETIME, 
	PRIMARY KEY (id), 
	CONSTRAINT uix_matchup_fixture_once UNIQUE (matchup_id, fixture_id), 
	FOREIGN KEY(matchup_id) REFERENCES matchups (id), 
	FOREIGN KEY(player_id) REFERENCES players (id), 
	FOREIGN KEY(fixture_id) REFERENCES fixtures (id)
);

CREATE TABLE results (
	id INTEGER NOT NULL, 
	fixture_id INTEGER NOT NULL, 
	outcome VARCHAR NOT NULL, 
	home_score INTEGER, 
	away_score INTEGER, 
	source VARCHAR NOT NULL, 
	updated_at DATETIME, 
	PRIMARY KEY (id), 
	UNIQUE (fixture_id), 
	FOREIGN KEY(fixture_id) REFERENCES fixtures (id)
);

CREATE TABLE weekly_recap_selections (
	id INTEGER NOT NULL, 
	week_id INTEGER NOT NULL, 
	recap_id INTEGER NOT NULL, 
	selected_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (week_id), 
	FOREIGN KEY(week_id) REFERENCES weeks (id), 
	FOREIGN KEY(recap_id) REFERENCES weekly_recaps (id)
);

CREATE TABLE correspondent_classification_job_items (
	id INTEGER NOT NULL, 
	job_id INTEGER NOT NULL, 
	source_id INTEGER NOT NULL, 
	custom_id VARCHAR NOT NULL, 
	active_reservation_key VARCHAR, 
	status VARCHAR NOT NULL, 
	provider_response_id VARCHAR, 
	error_message TEXT, 
	imported_at DATETIME, 
	PRIMARY KEY (id), 
	CONSTRAINT uix_classification_job_source UNIQUE (job_id, source_id), 
	FOREIGN KEY(job_id) REFERENCES correspondent_classification_jobs (id), 
	FOREIGN KEY(source_id) REFERENCES correspondent_sources (id), 
	UNIQUE (custom_id), 
	UNIQUE (active_reservation_key)
);

CREATE TABLE correspondent_source_classifications (
	id INTEGER NOT NULL, 
	source_id INTEGER NOT NULL, 
	prompt_version VARCHAR NOT NULL, 
	pass_number INTEGER NOT NULL, 
	pickem_impact VARCHAR NOT NULL, 
	editorial_functions_json TEXT NOT NULL, 
	article_use VARCHAR NOT NULL, 
	confidence VARCHAR NOT NULL, 
	route VARCHAR NOT NULL, 
	reason_codes_json TEXT NOT NULL, 
	reason TEXT NOT NULL, 
	model VARCHAR NOT NULL, 
	provider_response_id VARCHAR, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uix_source_classification_version_pass UNIQUE (source_id, prompt_version, pass_number), 
	FOREIGN KEY(source_id) REFERENCES correspondent_sources (id)
);

CREATE TABLE recap_source_usages (
	id INTEGER NOT NULL, 
	recap_id INTEGER NOT NULL, 
	source_id INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uix_recap_source_usage UNIQUE (recap_id, source_id), 
	FOREIGN KEY(recap_id) REFERENCES weekly_recaps (id), 
	FOREIGN KEY(source_id) REFERENCES correspondent_sources (id)
);

