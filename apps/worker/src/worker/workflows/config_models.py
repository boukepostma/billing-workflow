from datetime import date

from pydantic import BaseModel, Field


class SprintCfg(BaseModel):
    anchor_tuesday: date | None = None
    work_days: list[int] = Field(default_factory=lambda: [0, 1, 2])
    hours_per_day: float = 8.0
    normal_sprint_weeks: int = 3
    last_sprint_weeks: int = 4
    quarter_weeks: int = 13


class OwnerCfg(BaseModel):
    name: str
    email: str
    confirm_domains: list[str] = Field(default_factory=list)


class CustomerCfg(BaseModel):
    confirm_domains: list[str] = Field(default_factory=lambda: ["tcs.com"])


class EbEndpoints(BaseModel):
    hours: str = "/v1/urenregistraties"          # not exposed by REST v1; used only if hours_via_api=true
    invoice: str = "/v1/invoice"
    outstanding: str = "/v1/mutation/invoice/outstanding"
    relation: str = "/v1/relation"
    invoicetemplate: str = "/v1/invoicetemplate"
    emailtemplate: str = "/v1/emailtemplate"
    unit: str = "/v1/unit"
    ledger: str = "/v1/ledger"


class InvoiceCfg(BaseModel):
    factuursjabloon: str = "EN - Simpel met aantallen"
    emailsjabloon: str = "02. EN - Eerste Factuur"
    product_code: str = "UREN"
    hourly_rate: float = 105.0
    vat_code: str = "HOOG_VERK_21"
    vat_label: str = "Btw hoog 21%"
    unit_name: str = "uren"                  # resolved via GET /v1/unit (singular/plural)
    ledger_code: str = ""                    # resolved via GET /v1/ledger?code=; empty = first category 'Omzet'
    payment_term_days: int = 45
    check_after_days: int = 46
    link_hours: bool = True
    link_hours_field: str = "urenregistratieIds"
    lines_mode: str = "sprint_total"
    factuurtekst_template: str = "Sprint {start} - {end}"
    line_omschrijving_template: str = "Sprint {start} - {end}"
    date_format: str = "nl-short"
    month_names: list[str] = Field(
        default_factory=lambda: [
            "jan", "feb", "mrt", "apr", "mei", "jun",
            "jul", "aug", "sept", "okt", "nov", "dec",
        ]
    )
    send_prefer: str = "smtp"


class EboekhoudenCfg(BaseModel):
    base_url: str = "https://api.e-boekhouden.nl"
    session_source: str = "billingwf"        # POST /v1/session source (max 10 chars)
    hours_via_api: bool = False              # REST v1/SOAP expose no hours endpoint; hours ride on the invoice line
    token_env: str = "EBOEKHOUDEN_API_TOKEN"
    relation_code: str = "GEEKSOFT"
    medewerker: str = "Bouke Postma"
    project: str = "Project for Tata Consultancy Services Netherlands"
    kilometers: int = 0
    hours_description: str = "Consultancy hours"
    endpoints: EbEndpoints = Field(default_factory=EbEndpoints)
    invoice: InvoiceCfg = Field(default_factory=InvoiceCfg)


class Recipients(BaseModel):
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    subject: str = ""


class MailCfg(BaseModel):
    host_env: str = "SMTP_HOST"
    port_env: str = "SMTP_PORT"
    user_env: str = "SMTP_USER"
    password_env: str = "SMTP_PASSWORD"
    from_env: str = "SMTP_FROM"
    starttls: bool = True
    proposal: Recipients = Field(default_factory=Recipients)
    timesheet: Recipients = Field(default_factory=Recipients)
    timesheet_reminder: Recipients = Field(default_factory=Recipients)
    invoice: Recipients = Field(default_factory=Recipients)
    payment_reminder: Recipients = Field(default_factory=Recipients)
    owner_reminder: Recipients = Field(default_factory=Recipients)
    escalation: Recipients = Field(default_factory=Recipients)


class TimeoutsCfg(BaseModel):
    owner_confirm_days: int = 7
    owner_reminder_hours: int = 24
    customer_confirm_days: int = 14
    customer_reminder_weekdays: list[int] = Field(default_factory=lambda: [2, 4])
    customer_reminder_hour_utc: int = 16
    payment_reminder_interval_days: int = 1
    max_payment_reminders: int = 30


class GraphCfg(BaseModel):
    auth_mode: str = "refresh_token"     # "refresh_token" (public client) | "client_secret" (confidential client)
    tenant_id: str = "common"            # "common" works for delegated refresh tokens
    client_id: str = ""                  # app registration client ID (not secret)
    client_secret_env: str = "MSGRAPH_CLIENT_SECRET"      # used when auth_mode=client_secret
    refresh_token_env: str = "MISTRAL_MS_GRAPH_REFRESH_TOKEN"  # used when auth_mode=refresh_token
    mailbox: str = "me"                  # "me" for delegated tokens, else the mailbox UPN
    graph_base: str = "https://graph.microsoft.com/v1.0"
    login_base: str = "https://login.microsoftonline.com"
    scope: str = ""                     # empty = keep the refresh token's original consented scopes (avoids AADSTS70000)


class IngestCfg(BaseModel):
    transport: str = "graph"             # direct MS Graph API (client credentials)
    graph: GraphCfg = Field(default_factory=GraphCfg)
    search_folder: str = "inbox"
    processed_folder: str = "archive"
    poll_query: str = "SP-"
    poll_limit: int = 50
    lookback_days: int = 30
    ref_pattern: str = r"SP-\d{4}-\d{2}-\d{2}"
    billing_workflow_name: str = "billing-sprint"
    trusted_forwarder_domains: list[str] = Field(
        default_factory=lambda: ["guero.nl"]
    )  # forwards from these domains are unwrapped: the quoted From: decides owner/customer


class BillingConfig(BaseModel):
    sprint: SprintCfg = Field(default_factory=SprintCfg)
    owner: OwnerCfg = Field(default_factory=lambda: OwnerCfg(name="", email=""))
    customer: CustomerCfg = Field(default_factory=CustomerCfg)
    eboekhouden: EboekhoudenCfg = Field(default_factory=EboekhoudenCfg)
    mail: MailCfg = Field(default_factory=MailCfg)
    timeouts: TimeoutsCfg = Field(default_factory=TimeoutsCfg)
    ingest: IngestCfg = Field(default_factory=IngestCfg)
