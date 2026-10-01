"""Additive live tables. Registered on the application's existing metadata."""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, UniqueConstraint, JSON


def register_models(Base):
    class FixtureProviderLink(Base):
        __tablename__ = 'fixture_provider_links'
        id = Column(Integer, primary_key=True)
        fixture_id = Column(Integer, ForeignKey('fixtures.id'), nullable=False)
        provider = Column(String, nullable=False)
        external_fixture_id = Column(String, nullable=False)
        __table_args__ = (UniqueConstraint('provider', 'external_fixture_id'),
                          UniqueConstraint('fixture_id', 'provider'))

    class LiveFixtureState(Base):
        __tablename__ = 'live_fixture_states'
        fixture_id = Column(Integer, ForeignKey('fixtures.id'), primary_key=True)
        provider = Column(String, nullable=False)
        state = Column(String, nullable=False)
        is_live = Column(Boolean, nullable=False, default=False)
        home_score = Column(Integer)
        away_score = Column(Integer)
        minute = Column(Integer)
        extra_minute = Column(Integer)
        provider_updated_at = Column(String)
        last_synced_at = Column(DateTime, nullable=False)

    class MatchEvent(Base):
        __tablename__ = 'match_events'
        id = Column(Integer, primary_key=True)
        fixture_id = Column(Integer, ForeignKey('fixtures.id'), nullable=False, index=True)
        provider = Column(String, nullable=False)
        external_event_id = Column(String, nullable=False)
        event_type = Column(String, nullable=False)
        minute = Column(Integer)
        extra_minute = Column(Integer)
        team_provider_id = Column(String)
        team_name = Column(String)
        side = Column(String)
        player_provider_id = Column(String)
        player_name = Column(String)
        related_player_provider_id = Column(String)
        related_player_name = Column(String)
        detail = Column(String)
        running_score = Column(String)
        sort_order = Column(Integer)
        is_active = Column(Boolean, nullable=False, default=True)
        provider_updated_at = Column(String)
        created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
        updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
        # Normalized prior versions, including withdrawals; never raw provider data.
        revisions = Column(JSON, nullable=False, default=list)
        __table_args__ = (UniqueConstraint('fixture_id', 'provider', 'external_event_id'),)
    return FixtureProviderLink, LiveFixtureState, MatchEvent
