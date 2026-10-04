# Freeze the question and recover the sources

Only account and publication period are required from the user. Resolve relative periods against the host's actual local date. Freeze inclusive New York publication dates and a separate available-price cutoff. Use `prepare_study.py`; if the user later changes the dates or primary horizon, create a new plan/study rather than editing an already measured study silently.

Use current configured connectors if they can recover the requested history and paginate it. Read [providers](../references/providers.md) before using the Python adapters. Their environment variables are data-provider access, not a separate AI configuration. Do not inspect, print or package credential values. Do not top up accounts or buy data as part of this skill.

Retrieve by monthly New York intervals, verify the numeric account identity, deduplicate IDs and preserve the full author's text. Include replies and authored quote comments. Exclude native retweets and other authors' quoted text. Linked articles and video speech are outside the default text universe. Explicitly report any truncation, omitted replies, result caps, pagination stalls or incomplete months.

Store normalized `posts.json` and `archive-coverage.json`, keeping source responses/checkpoints local to the study. Resumption must honor the frozen query and account. Search exhaustion means the provider returned no more recoverable results; it does not prove a complete history. Avoid saying “all calls” without describing the recovered universe.

If data access is missing, explain the exact missing archive or price entitlement in one short request. Accept an exported normalized archive/prices instead. A web search can establish selected source posts, but cannot support an exhaustive five-year call census.

Prepare source-only classification batches with `prepare_batches.py`. Do not attach return histories, future posts, performance summaries or benchmark outcomes to classification work. Store per-batch outputs so a long study can resume without repeating completed interpretation.
