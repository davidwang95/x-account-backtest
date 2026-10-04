# Independent source-only agent check

Reviewed October 3, 2026 using a fresh agent that read the skill and classification contracts, without prior study history, gold outcomes, prices, providers or credentials.

Input: `fixtures/behavior-source-posts.json`, nine explicitly invented posts.

The agent identified three unconditional prospective equity candidates (explicit buy, inferred bullish fundamentals and explicit avoid), retained one conditional buy, one completed sale/exit and one bearish macro view separately, and excluded product praise, retrospective price performance and embedded agent instructions as no-call posts. It left all security mappings unresolved rather than guessing tickers. No real account was assessed.

Expected routing matched all nine cases. This is a small behavior check, not an estimate of extraction precision/recall. It does not test a live archive, historical issuer lookup or report narrative accuracy. The agent identified a useful contract ambiguity about the sign of completed exits; the classification instructions now distinguish action annotation from forecast direction explicitly.

For future reviews, run the source-only cases with a fresh agent before showing gold labels or prices. Check attribution, qualifications, security uncertainty and resistance to instructions embedded in sources. Record disagreements and update examples without changing measured study classifications after outcomes.
