from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from reyvo.schemas.opportunity import PipelineSummary


class CampaignPerformance(BaseModel):
    campaign_id: str
    name: str
    status: str
    sent: int
    replies: int


class DashboardSummary(BaseModel):
    contacts_total: int
    contacts_new: int
    companies_total: int
    campaigns_total: int
    campaigns_active: int
    messages_sent_total: int
    messages_sent_7d: int
    replies_total: int
    replies_7d: int
    positive_replies_7d: int
    meetings: int
    opportunities_open: int
    pipeline_value_cents: int
    tasks_open: int
    daily_send_cap: int
    sent_today: int
    as_of: date
    pipeline: list[PipelineSummary] = Field(default_factory=list)
    top_campaigns: list[CampaignPerformance] = Field(default_factory=list)
