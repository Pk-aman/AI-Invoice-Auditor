"""Every user-facing string lives here (one place to edit / translate)."""

APP_TITLE = "AI Invoice Auditor"

# ---- navigation -------------------------------------------------------------
NAV_DASHBOARD = "Dashboard"
NAV_UPLOAD = "Upload Invoices"
NAV_ASSISTANT = "Invoice Assistant"

# ---- errors (plain language) ------------------------------------------------
ERR_UNREACHABLE = ("We can't reach the invoice service right now. Please wait a moment and try "
                   "again. If this keeps happening, contact your administrator.")
ERR_TIMEOUT = "The service is taking longer than expected. Please try again in a moment."
ERR_NOT_FOUND = "We couldn't find that item. It may have been removed or is not processed yet."
ERR_SERVER = "Something went wrong on our side. Please try again; if it persists, tell your administrator."
ERR_REJECTED = "The request was not accepted."
ERR_VERSION_MISMATCH = ("The app and the invoice service are out of step (backend version "
                        "mismatch). Please tell your administrator.")
BTN_TRY_AGAIN = "Try again"

# ---- status vocabulary (icon + text + colour; never colour alone) -----------
# key -> (label, icon, streamlit colour)
RECOMMENDATION = {
    "approve": ("Approved", "✅", "green"),
    "review": ("Needs review", "⚠️", "orange"),
    "reject": ("Rejected", "⛔", "red"),
}
VALIDATION = {
    "passed": ("All checks passed", "✅", "green"),
    "partial": ("Minor issues", "⚠️", "orange"),
    "failed": ("Checks failed", "⛔", "red"),
}
PROCESSING = ("Processing", "🔄", "blue")
FAILED = ("Failed", "❗", "gray")
UNKNOWN = ("Unknown", "❔", "gray")
SEVERITY = {
    "low": ("Low", "🟢"),
    "medium": ("Medium", "🟡"),
    "high": ("High", "🟠"),
    "critical": ("Critical", "🔴"),
}
# colour-blind-safe chart colours
COLOR_APPROVED = "#009E73"
COLOR_REVIEW = "#E69F00"
COLOR_REJECTED = "#D55E00"
COLOR_NEUTRAL = "#0072B2"

# ---- dashboard ---------------------------------------------------------------
DASH_TITLE = "Dashboard"
DASH_INTRO = "See every processed invoice at a glance, then open one to review or correct it."
KPI_TOTAL = "Total invoices"
KPI_APPROVED = "Approved"
KPI_REVIEW = "Needs review"
KPI_REJECTED = "Rejected"
KPI_CONFIDENCE = "Avg. translation confidence"
KPI_CONFIDENCE_HELP = ("How sure the system is that its English translation is accurate. "
                       "Invoices below 95% are never approved automatically.")
KPI_PENDING = "Corrections to re-check"
KPI_PENDING_HELP = "Corrections you saved that have not been re-validated yet."
EMPTY_TITLE = "No invoices yet"
EMPTY_BODY = "Upload your first invoice and it will appear here once it has been checked."
EMPTY_LINK = "Go to Upload Invoices"
CHART_REC = "Most invoices are: {top}"
CHART_REC_PLAIN = "Invoices by outcome"
CHART_TIME = "Invoices received per day"
CHART_VENDOR = "{vendor} has the most discrepancies ({n})"
CHART_VENDOR_PLAIN = "Discrepancies by vendor"
CHART_TYPES = "Most common problem: {field} ({n})"
CHART_TYPES_PLAIN = "Common discrepancy types"
NO_CHART_DATA = "Not enough data to draw this chart yet."
FILTERS = "Filters"
FILTER_VENDOR = "Vendor"
FILTER_STATUS = "Checks result"
FILTER_REC = "Outcome"
FILTER_CURRENCY = "Currency"
FILTER_DATES = "Received between"
FILTER_SEARCH = "Search invoice no. or vendor"
FILTER_ALL = "All"
TABLE_TITLE = "Invoices"
TABLE_HINT = "Click a row (or use the selector below) to open an invoice."
PICKER_LABEL = "Open an invoice"
PICKER_NONE = "— choose an invoice —"
PAGE_LABEL = "Page"
SHOWING = "Showing {a}–{b} of {n} invoices"
LIVE_NOTE = "This list refreshes automatically."

# ---- detail panel ------------------------------------------------------------
TAB_SUMMARY = "Summary"
TAB_LINES = "Line items"
TAB_DISCREPANCIES = "Discrepancies"
TAB_TEXT = "What the system read"
TAB_REPORT = "Report"
TAB_TIMELINE = "Timeline"
TAB_CORRECTIONS = "Corrections"
CLOSE_DETAIL = "Close invoice"
DETAIL_TITLE = "Invoice {no}"
FIELD_LABELS = {
    "invoice_number": "Invoice number",
    "vendor_name": "Vendor",
    "po_number": "Purchase order",
    "invoice_date": "Invoice date",
    "currency": "Currency",
    "total_amount": "Total amount",
}
CONFIDENCE_LABEL = "Translation confidence"
LANGUAGE_LABEL = "Original language"
RUN_LABEL = "Check round"
REASON_APPROVE = "No problems were found and the translation is trustworthy, so it was approved."
REASON_REJECT = "This invoice should not be paid as submitted: {why}"
REASON_REVIEW = "A person should look at this before it is approved: {why}"
REASON_PROCESSING = "This invoice is still being processed."
REASON_NONE = "no specific reason was recorded"
NO_LINES = "No line items were found on this invoice."
NO_DISCREPANCIES = "No discrepancies were found against the purchase order."
SHOW_EARLIER = "Show earlier check rounds"
ERP_SAYS = "ERP says"
COL_FIELD = "Field"
COL_INVOICE_VALUE = "On the invoice"
COL_ERP_VALUE = "In the ERP"
COL_DEVIATION = "Difference"
COL_SEVERITY = "How serious"
COL_ROUND = "Check round"
COL_STATE = "State"
STATE_CURRENT = "Current"
STATE_EARLIER = "Earlier round"
RAW_TEXT = "Text as read from the file"
TRANSLATED_TEXT = "English text"
REPORT_MISSING = "The report is not available yet."
DOWNLOAD_HTML = "Download report (HTML)"
DOWNLOAD_JSON = "Download report (JSON)"
NO_EVENTS = "Nothing has been recorded for this invoice yet."
EVENT_FAILED_HINT = "This step did not finish: {why}"

# ---- corrections ---------------------------------------------------------------
CORR_INTRO = ("If the system misread something, fix it here. Your correction is saved alongside "
              "the original (the original is never overwritten) and you can then re-check the invoice.")
CORR_NAME = "Your name"
CORR_NAME_HELP = "Recorded with every correction so there is a clear audit trail."
CORR_HEADER = "Invoice details"
CORR_LINES = "Line items"
CORR_SAVE = "Review and save corrections"
CORR_NO_CHANGES = "You haven't changed anything yet."
CORR_NEED_NAME = "Please enter your name before saving."
CORR_DIALOG_TITLE = "Please confirm your corrections"
CORR_CONFIRM = "Confirm and save"
CORR_CANCEL = "Cancel"
CORR_SAVED = "Saved {n} correction(s)."
CORR_HISTORY = "Correction history"
CORR_HISTORY_EMPTY = "No corrections have been made to this invoice."
CORR_BAD_DATE = "{label}: please use the format YYYY-MM-DD."
CORR_BAD_NUMBER = "{label}: please enter a number."
CORR_BAD_CURRENCY = "{label}: please use a 3-letter currency code such as USD."
CORR_EMPTY = "{label}: cannot be empty."
REVALIDATE = "Re-check this invoice"
REVALIDATE_HELP = "Runs the checks again using your corrections."
REVALIDATE_DISABLED = "Save a correction first; there is nothing new to re-check."
REVALIDATE_RUNNING = "Re-checking… this can take a minute."
REVALIDATE_DONE = "Re-check finished. The results below are up to date."
REVALIDATE_FAILED = "The re-check did not finish: {why}"
COL_WAS = "Was"
COL_NOW = "Now"
COL_BY = "By"
COL_WHEN = "When"
COL_CHECKED = "Re-checked"

# ---- upload ----------------------------------------------------------------------
UPLOAD_TITLE = "Upload Invoices"
UPLOAD_INTRO = "Add invoices to be checked automatically. You can select several files at once."
UPLOAD_HELP = "Supported: PDF, Word (DOCX), and scanned images (PNG, JPG, TIFF). Up to {mb} MB each."
UPLOAD_LABEL = "Choose invoice files"
UPLOAD_SUBMIT = "Send for checking"
UPLOAD_PREVIEW = "Files ready to send"
UPLOAD_TOO_BIG = "“{name}” is {size:.1f} MB, over the {mb} MB limit, so it was skipped."
UPLOAD_BAD_TYPE = "“{name}” is not a supported file type, so it was skipped."
UPLOAD_NONE_VALID = "None of the selected files can be sent. Please check the messages above."
UPLOAD_PDF_NOTE = "PDF: preview not shown"
UPLOAD_DOC_NOTE = "Word document: preview not shown"
UPLOAD_PROGRESS = "Progress"
UPLOAD_PROGRESS_EMPTY = "Nothing uploaded yet in this session."
UPLOAD_VIEW = "View result"
UPLOAD_CLEAR = "Clear list"
UPLOAD_DUP = "Already processed, no need to upload again."
UPLOAD_DONE_ALL = "All files have finished."
UPLOAD_SENT = "Sent {n} file(s)."
COL_FILE = "File"
COL_STATUS = "Status"
COL_NOTE = "Note"
# status -> (label, icon, order for progress bar)
UPLOAD_STAGES = {
    "QUEUED": ("Waiting in line", "⏳", 0),
    "DETECTED": ("Waiting in line", "⏳", 0),
    "EXTRACTING": ("Reading the document", "📖", 1),
    "EXTRACT": ("Reading the document", "📖", 1),
    "TRANSLATING": ("Translating", "🌐", 2),
    "TRANSLATE": ("Translating", "🌐", 2),
    "PARSING": ("Picking out the details", "🔎", 3),
    "PARSE": ("Picking out the details", "🔎", 3),
    "VALIDATING": ("Checking the data", "🧮", 4),
    "DATA_VALIDATION": ("Checking the data", "🧮", 4),
    "ERP_CHECK": ("Checking against the ERP", "🗂️", 5),
    "BUSINESS_VALIDATION": ("Checking against the ERP", "🗂️", 5),
    "REPORTING": ("Creating the report", "📝", 6),
    "REPORT": ("Creating the report", "📝", 6),
    "INDEXING": ("Preparing for questions", "📚", 7),
    "INDEX": ("Preparing for questions", "📚", 7),
    "COMPLETED": ("Done", "✅", 8),
    "FAILED": ("Failed", "❗", 8),
    "DUPLICATE": ("Already processed", "♻️", 8),
    "REJECTED": ("Not accepted", "⛔", 8),
}
UPLOAD_TERMINAL = {"COMPLETED", "FAILED", "DUPLICATE", "REJECTED"}
UPLOAD_STAGE_MAX = 8

# ---- assistant --------------------------------------------------------------------
ASSIST_TITLE = "Invoice Assistant"
ASSIST_INTRO = "Ask questions about your invoices in plain language."
SCOPE_LABEL = "What should I look at?"
SCOPE_ALL = "All invoices"
SCOPE_INVOICE = "One specific invoice"
SCOPE_VENDOR = "One vendor"
SCOPE_PICK_INVOICE = "Which invoice?"
SCOPE_PICK_VENDOR = "Which vendor?"
SCOPE_NO_DATA = "There are no invoices to choose from yet."
SUGGESTED = "Try asking"
SUGGESTIONS = [
    "Why was this invoice flagged?",
    "Which invoices were rejected?",
    "How many invoices per vendor?",
    "Summarise discrepancies for VEND-001",
]
CHAT_PLACEHOLDER = "Ask about your invoices…"
CHAT_CLEAR = "Clear chat"
CHAT_THINKING = "Looking through your invoices… the first answer can take up to a minute."
CHAT_ERROR = "I couldn't answer that just now."
CHAT_EMPTY = "No questions yet. Pick a suggestion or type your own."
ROUTE_DB = ("Answered from database", "🗄️")
ROUTE_DOCS = ("Answered from invoice documents", "📄")
ROUTE_DB_KEYS = {"sql", "structured", "database", "db"}
SOURCES = "Sources ({n})"
SOURCE_INVOICE = "Invoice text"
SOURCE_REPORT = "Audit report"
SCORES_TITLE = "Answer quality"
SCORES_PENDING = "Quality scores are being worked out…"
SCORES_NA = "Quality scores are not available for this answer."
SCORE_RELEVANCE = "Relevance"
SCORE_RELEVANCE_HELP = "Does the answer actually address your question?"
SCORE_GROUNDED = "Groundedness"
SCORE_GROUNDED_HELP = "Is every claim backed by the invoice data it was based on?"
SCORE_CONTEXT = "Context relevance"
SCORE_CONTEXT_HELP = "Was the information the assistant looked up relevant to your question?"
