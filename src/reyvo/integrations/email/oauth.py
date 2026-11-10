from __future__ import annotations

from reyvo.core.exceptions import BadRequestError
from reyvo.db.enums import EmailProvider
from reyvo.integrations.email.base import EmailAdapter
from reyvo.integrations.email.gmail import GmailAdapter
from reyvo.integrations.email.graph import GraphAdapter


def get_email_adapter(provider: EmailProvider | str) -> EmailAdapter:
    value = provider.value if isinstance(provider, EmailProvider) else provider
    if value == EmailProvider.GOOGLE.value:
        return GmailAdapter()
    if value == EmailProvider.MICROSOFT.value:
        return GraphAdapter()
    raise BadRequestError("Unsupported email provider.")
